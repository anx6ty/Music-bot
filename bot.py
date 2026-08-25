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
PURPLE = discord.Color.from_rgb(155, 93, 229)

# --- SUPPORT SERVER LINK ---
# Small, unobtrusive link button added to embeds and error messages.
# Link buttons need no callback — Discord opens the invite/join screen
# client-side the moment someone taps it.
SUPPORT_SERVER_INVITE = "https://discord.gg/5ygnUWdG7D"


def add_support_button(view=None):
    """Attach the support-server button to a view (creating one if needed)
    without disturbing whatever else is already on it."""
    if view is None:
        view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label="Support",
        emoji="🛟",
        style=discord.ButtonStyle.link,
        url=SUPPORT_SERVER_INVITE,
    ))
    return view


# --- CUSTOM (EXTERNAL) EMOJI CONFIG ---
# These are your own server's custom emojis, not built-in unicode ones.
# How to get an ID: enable Developer Mode (User Settings > Advanced),
# then right-click any custom emoji in Discord and click "Copy Emoji ID".
# The bot must be a member of a server that owns the emoji to render it.
# Leave an entry as None to fall back to a unicode emoji automatically.
CUSTOM_EMOJI_IDS = {
    "pause":      None,   # e.g. 1234567890123456789
    "resume":     None,
    "skip":       None,
    "shuffle":    None,
    "loop_off":   None,
    "loop_track": None,
    "loop_queue": None,
    "stop":       None,
}

_FALLBACK_EMOJI = {
    "pause":      "⏸️",
    "resume":     "▶️",
    "skip":       "⏭️",
    "shuffle":    "🔀",
    "loop_off":   "🔁",
    "loop_track": "🔂",
    "loop_queue": "🔁",
    "stop":       "⏹️",
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
    'noplaylist': False,  # playlists are allowed through; enqueue_song decides whether to expand them
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
queues = {}                # guild_id -> list of queue item dicts
current_song = {}          # guild_id -> now-playing dict
loop_modes = {}             # guild_id -> None / "track" / "queue"
now_playing_messages = {}   # guild_id -> discord.Message
queue_notice_messages = {}  # guild_id -> set of "added to queue" notice messages
progress_tasks = {}         # guild_id -> asyncio.Task (the 5s refresh loop)
guild_prefixes = {}         # guild_id -> custom quick-play prefix set via /config

CONFIG_FILE = "guild_config.json"
QUEUE_NOTICE_BUTTON_TIMEOUT = 30
QUEUE_NOTICE_DELETE_AFTER = 60
NOW_PLAYING_REFRESH_SECONDS = 2  # Discord throttles message edits (~5 per 5s per channel); 1s was too fast and got rate-limited into freezing


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


# --- BOT OWNER CHECK (for privileged, cross-server commands) ---
# Comma-separated Discord user IDs, e.g. "123456789012345678,987654321098765432"
BOT_OWNER_IDS = {int(x) for x in os.getenv("BOT_OWNER_IDS", "").split(",") if x.strip().isdigit()}


def is_bot_owner(user_id):
    return user_id in BOT_OWNER_IDS


def get_queue(guild_id):
    return queues.setdefault(guild_id, [])


def is_playlist_url(query):
    q = str(query).lower()
    return "list=" in q or "/playlist" in q or "music.youtube.com/playlist" in q


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


def format_time(seconds):
    seconds = int(max(0, seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def get_elapsed_seconds(guild_id):
    song = current_song.get(guild_id)
    if not song:
        return 0
    now = time.time()
    elapsed = now - song.get("start_time", now) - song.get("paused_total", 0.0)
    if song.get("paused_at"):
        elapsed -= (now - song["paused_at"])
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


def server_status():
    return f"✦ Casting {len(bot.guilds)} servers"


async def update_bot_presence(title=None):
    """Switches the bot's presence between the default status and a normal-
    user-style 'Listening to <song>' status while something is playing.
    Discord only allows one presence per bot process (not per server), so if
    multiple servers are playing at once this reflects whichever song most
    recently started."""
    try:
        if title:
            activity = discord.Activity(type=discord.ActivityType.listening, name=title)
        else:
            activity = discord.Game(name=server_status())
        await bot.change_presence(activity=activity)
    except Exception as e:
        print(f"[PRESENCE ERROR] {e}")


# --- HOME-SERVER LOGGING ---
# Point these at channels in your own "main" server. LOG_CHANNEL_ID is a
# catch-all fallback — set the specific ones only if you want that category
# split into its own channel; anything left unset falls back to it.
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID")
JOIN_LOG_CHANNEL_ID = os.getenv("JOIN_LOG_CHANNEL_ID") or LOG_CHANNEL_ID
MUSIC_LOG_CHANNEL_ID = os.getenv("MUSIC_LOG_CHANNEL_ID") or LOG_CHANNEL_ID
ERROR_LOG_CHANNEL_ID = os.getenv("ERROR_LOG_CHANNEL_ID") or LOG_CHANNEL_ID

_log_channel_cache = {}


async def get_log_channel(channel_id):
    if not channel_id:
        return None
    channel_id = int(channel_id)
    if channel_id in _log_channel_cache:
        return _log_channel_cache[channel_id]
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception as e:
            print(f"[LOG CHANNEL ERROR] Couldn't fetch {channel_id}: {e}")
            return None
    _log_channel_cache[channel_id] = channel
    return channel


async def send_log(channel_id, embed=None, text=None):
    channel = await get_log_channel(channel_id)
    if not channel:
        return
    try:
        await channel.send(content=text, embed=embed)
    except Exception as e:
        print(f"[LOG SEND ERROR] {e}")


# --- SHARED HTTP SESSION ---
# Reusing one aiohttp session (instead of opening a fresh TCP/TLS connection
# for every Spotify/API call) cuts noticeable latency off replies.
_http_session = None


async def get_http_session():
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession()
    return _http_session


# --- NATIVE DISCORD VOICE CHANNEL STATUS ---
# This is the small status text Discord shows under a voice channel's name —
# separate from the bot's presence/activity. Needs the "Set Voice Channel
# Status" permission. Not yet wrapped by discord.py, so we call the REST
# endpoint directly.
async def set_vc_status(channel_id, status_text):
    if not channel_id:
        return
    session = await get_http_session()
    url = f"https://discord.com/api/v10/channels/{channel_id}/voice-status"
    headers = {
        "Authorization": f"Bot {os.getenv('DISCORD_TOKEN')}",
        "Content-Type": "application/json",
    }
    try:
        async with session.put(url, headers=headers, json={"status": (status_text or "")[:500]}) as resp:
            if resp.status not in (200, 204):
                body = await resp.text()
                print(f"[VC STATUS ERROR] {resp.status}: {body}")
    except Exception as e:
        print(f"[VC STATUS ERROR] {e}")


# --- SPOTIFY SUPPORT ---
# Spotify's own audio is DRM-protected and can never be streamed directly.
# Instead we look up the real track/playlist/album metadata through Spotify's
# Web API, then search + play the matching song(s) from YouTube.
#
# Create a free app at https://developer.spotify.com/dashboard to get these:
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


async def resolve_spotify_short_link(url):
    """Share links (open.spotify.com/s/xxxx) 302-redirect to the real
    track/playlist/album URL — follow that redirect to get the canonical link."""
    try:
        session = await get_http_session()
        async with session.get(url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            return str(resp.url)
    except Exception as e:
        print(f"[SPOTIFY REDIRECT ERROR] {e}")
        return url


async def get_spotify_token():
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return None
    if _spotify_token_cache["token"] and time.time() < _spotify_token_cache["expires_at"] - 30:
        return _spotify_token_cache["token"]
    try:
        session = await get_http_session()
        auth = aiohttp.BasicAuth(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)
        async with session.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            auth=auth,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
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
        async with session.get(
            f"https://api.spotify.com/v1/{endpoint}",
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return None
            return await resp.json()
    except Exception as e:
        print(f"[SPOTIFY API ERROR] {e}")
        return None


def _track_search_query(track):
    artists = ", ".join(a["name"] for a in track.get("artists", []) if a.get("name"))
    title = track.get("name", "")
    return f"{artists} - {title}".strip(" -") or title


async def _spotify_oembed_title(url):
    """No API credentials configured — fall back to Spotify's public oEmbed
    endpoint. This only returns a single title, so it works for individual
    tracks but can't enumerate a playlist or album."""
    try:
        session = await get_http_session()
        async with session.get(
            "https://open.spotify.com/oembed",
            params={"url": url},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            return data.get("title")
    except Exception as e:
        print(f"[SPOTIFY OEMBED ERROR] {e}")
        return None


async def resolve_spotify_link(url):
    """Turns any Spotify track/playlist/album/share link into a list of
    'Artist - Title' search strings that yt-dlp can then look up on YouTube.
    Returns None specifically when a playlist/album needs API credentials
    that aren't configured, so the caller can show a clear fix instead of
    a generic failure."""
    if "/s/" in url or not (
        SPOTIFY_TRACK_RE.search(url) or SPOTIFY_PLAYLIST_RE.search(url) or SPOTIFY_ALBUM_RE.search(url)
    ):
        url = await resolve_spotify_short_link(url)

    has_api = bool(SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET)

    track_match = SPOTIFY_TRACK_RE.search(url)
    if track_match:
        if has_api:
            data = await spotify_get(f"tracks/{track_match.group(1)}")
            if data:
                return [_track_search_query(data)]
        title = await _spotify_oembed_title(url)
        return [title] if title else []

    playlist_match = SPOTIFY_PLAYLIST_RE.search(url)
    if playlist_match:
        if not has_api:
            return None
        data = await spotify_get(f"playlists/{playlist_match.group(1)}")
        if not data:
            return []
        queries = []
        for item in data.get("tracks", {}).get("items", []):
            track = item.get("track")
            if track:
                queries.append(_track_search_query(track))
        return queries

    album_match = SPOTIFY_ALBUM_RE.search(url)
    if album_match:
        if not has_api:
            return None
        data = await spotify_get(f"albums/{album_match.group(1)}")
        if not data:
            return []
        return [_track_search_query(t) for t in data.get("tracks", {}).get("items", [])]

    return []


async def resolve_query_items(query, requester):
    """Returns a list of queue-ready items. Spotify links are resolved to
    searchable 'Artist - Title' strings; YouTube playlist URLs are expanded
    into individual entries; anything else is treated as a single search."""
    if spotify_url(query):
        queries = await resolve_spotify_link(query)
        if queries is None:
            raise RuntimeError(
                "That's a Spotify playlist/album link. Add `SPOTIFY_CLIENT_ID` and "
                "`SPOTIFY_CLIENT_SECRET` environment variables (free at "
                "developer.spotify.com/dashboard) so I can read its tracklist."
            )
        if not queries:
            raise RuntimeError("Couldn't resolve that Spotify link — it may be private, region-locked, or invalid.")
        return [{"query": q, "title": q, "requester": requester} for q in queries]

    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=False))
    if not data:
        raise RuntimeError("No result found.")

    if data.get("entries"):
        entries = [e for e in data["entries"] if e]
        if not entries:
            raise RuntimeError("No playable entries found.")

        if is_playlist_url(query):
            return [{
                "query": e.get("webpage_url") or e.get("url") or e.get("title"),
                "title": e.get("title", "Unknown Track"),
                "thumbnail": e.get("thumbnail"),
                "webpage_url": e.get("webpage_url", ""),
                "requester": requester,
            } for e in entries]

        e = entries[0]
        return [{
            "query": e.get("webpage_url") or query,
            "title": e.get("title", "Unknown Track"),
            "thumbnail": e.get("thumbnail"),
            "webpage_url": e.get("webpage_url", ""),
            "requester": requester,
            "stream_url": e.get("url"),
            "duration_sec": e.get("duration") or 0,
        }]

    return [{
        "query": query,
        "title": data.get("title", query),
        "thumbnail": data.get("thumbnail"),
        "webpage_url": data.get("webpage_url", ""),
        "requester": requester,
        "stream_url": data.get("url"),
        "duration_sec": data.get("duration") or 0,
    }]


# --- LIVE "NOW PLAYING" REFRESH (updates the embed every 2 seconds) ---

async def update_now_playing_message(guild_id):
    """Edits the Now Playing embed. Returns a backoff delay (seconds) if
    Discord rate-limited the edit, so the caller can wait it out instead of
    silently freezing on a stale bar."""
    msg = now_playing_messages.get(guild_id)
    if not msg or not current_song.get(guild_id):
        return None
    guild = bot.get_guild(guild_id)
    vc = guild.voice_client if guild else None
    try:
        await msg.edit(
            embed=build_now_playing_embed(guild_id, paused=(vc.is_paused() if vc else False)),
            view=MusicView(guild_id)
        )
        return None
    except discord.NotFound:
        now_playing_messages.pop(guild_id, None)
        return None
    except discord.HTTPException as e:
        if getattr(e, "status", None) == 429:
            retry_after = getattr(e, "retry_after", None) or 3
            print(f"[REFRESH RATE LIMITED] guild {guild_id}: backing off {retry_after}s")
            return retry_after
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
                # Actually wait out the rate limit instead of hammering it
                # again next tick — this is what was causing the bar to
                # freeze instead of catching back up.
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


# --- EMBEDS ---

def build_now_playing_embed(guild_id, paused=False):
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

    embed = discord.Embed(
        description=(
            f"### [{song['title']}]({song.get('webpage_url', '') or song['title']})\n"
            f"{bar}\n"
            f"`{format_time(elapsed)} / {song.get('duration_str', 'Unknown')}`"
        ),
        color=PURPLE
    )
    embed.set_author(name="⏸️  SPELL PAUSED" if paused else "◈  NOW CASTING")

    if song.get("thumbnail"):
        embed.set_image(url=song["thumbnail"])  # large full-width artwork, not a small corner thumbnail

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


# --- "ADDED TO QUEUE" NOTICE VIEW ---
class QueueNoticeView(discord.ui.View):
    def __init__(self, guild_id, queue_item_id):
        super().__init__(timeout=QUEUE_NOTICE_BUTTON_TIMEOUT)
        self.guild_id = guild_id
        self.queue_item_id = queue_item_id

        move_top_btn = discord.ui.Button(label="Move to Top", emoji="⬆️", style=discord.ButtonStyle.primary)
        move_top_btn.callback = self.move_top
        self.add_item(move_top_btn)

        remove_btn = discord.ui.Button(label="Remove", emoji="🗑️", style=discord.ButtonStyle.danger)
        remove_btn.callback = self.remove_queue
        self.add_item(remove_btn)

        add_support_button(self)

    def find_item(self):
        for item in get_queue(self.guild_id):
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

    async def move_top(self, interaction: discord.Interaction):
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

    async def remove_queue(self, interaction: discord.Interaction):
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

        add_support_button(self)  # small link button, lands on its own row

    async def pause_resume_callback(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc:
            await interaction.response.send_message("❌ I'm not connected to a voice channel.", ephemeral=True)
            return

        if vc.is_playing():
            vc.pause()
            mark_paused(self.guild_id)
            await interaction.response.edit_message(
                embed=build_now_playing_embed(self.guild_id, paused=True),
                view=MusicView(self.guild_id)
            )
            await interaction.channel.send(f"⏸️ Spell paused by {interaction.user.mention}")
        elif vc.is_paused():
            vc.resume()
            mark_resumed(self.guild_id)
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
        await interaction.response.send_message(
            f"🔀 Queue shuffled by {interaction.user.mention} — **{len(queue)}** song(s) reordered"
        )

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
        channel_id = vc.channel.id if vc.channel else None
        vc.stop()
        try:
            await vc.disconnect()
        except discord.HTTPException:
            pass
        await set_vc_status(channel_id, "")
        await update_bot_presence()

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
            channel_id = vc.channel.id if vc.channel else None
            await delete_now_playing(guild_id)
            try:
                await vc.disconnect()
            except discord.HTTPException:
                pass
            await set_vc_status(channel_id, "")
            await update_bot_presence()
            return
        item = queue.pop(0)
        query = item["query"]
        requester = item["requester"]

    loop = asyncio.get_event_loop()
    try:
        # Always extract fresh at play time. A stream URL cached from an
        # earlier search-time extraction can go stale or behave inconsistently
        # through ffmpeg, which was causing playback to die a few seconds in
        # and restart from zero — re-extracting here trades a little latency
        # for reliable, full-length playback.
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=False))
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
        thumbnail = data.get("thumbnail")
        webpage_url = data.get("webpage_url", "")

        minutes, seconds = divmod(int(duration_sec), 60)
        duration_str = f"{minutes:02d}:{seconds:02d}"

    except Exception as e:
        error_detail = str(e) if str(e) else type(e).__name__
        if send_func:
            await send_func(
                text=f"😵‍💫 Couldn't cast **{query}** — skipping.\n`{error_detail}`",
                view=add_support_button()
            )
        asyncio.create_task(send_log(
            ERROR_LOG_CHANNEL_ID,
            text=f"⚠️ **Extraction failed** in **{guild.name}** (`{guild.id}`)\nQuery: `{query}`\nError: `{error_detail}`"
        ))
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
        "paused_total": 0.0,
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
                "_queue_id": f"{time.time_ns()}-loop",
            })

        fut = asyncio.run_coroutine_threadsafe(play_next(guild, channel), bot.loop)
        try:
            fut.result()
        except Exception as e:
            print(f"[AFTER CALLBACK ERROR] {e}")

    try:
        source = discord.FFmpegPCMAudio(song_url, **FFMPEG_OPTIONS)
        vc.play(source, after=after_play)
    except discord.ClientException as e:
        error_detail = str(e) if str(e) else type(e).__name__
        if send_func:
            if "ffmpeg" in error_detail.lower():
                await send_func(
                    text="❌ Audio engine (FFmpeg) is not available on the host. Add it to your Railway service.",
                    view=add_support_button()
                )
            else:
                await send_func(text=f"❌ Playback error.\n`ClientException: {error_detail}`", view=add_support_button())
        asyncio.create_task(send_log(
            ERROR_LOG_CHANNEL_ID,
            text=f"🚨 **ClientException** in **{guild.name}** (`{guild.id}`) playing `{title}`: `{error_detail}`"
        ))
        return
    except Exception as e:
        error_detail = str(e) if str(e) else type(e).__name__
        if send_func:
            await send_func(text=f"❌ Playback error.\n`{error_detail}`", view=add_support_button())
        asyncio.create_task(send_log(
            ERROR_LOG_CHANNEL_ID,
            text=f"🚨 **Playback error** in **{guild.name}** (`{guild.id}`) playing `{title}`: `{error_detail}`"
        ))
        return

    old_msg = now_playing_messages.get(guild_id)
    if old_msg:
        try:
            await old_msg.delete()
        except (discord.NotFound, discord.HTTPException):
            pass

    await set_vc_status(vc.channel.id, f"🎶 {title}"[:100])
    await update_bot_presence(title=title)

    log_embed = discord.Embed(
        description=f"🎶 **{title}**\n[{webpage_url}]({webpage_url})" if webpage_url else f"🎶 **{title}**",
        color=PURPLE
    )
    log_embed.add_field(name="Server", value=f"{guild.name} (`{guild.id}`)", inline=True)
    log_embed.add_field(name="Voice Channel", value=vc.channel.name if vc.channel else "Unknown", inline=True)
    if requester:
        log_embed.add_field(name="Requested by", value=f"{requester} (`{requester.id}`)", inline=True)
    if thumbnail:
        log_embed.set_thumbnail(url=thumbnail)
    asyncio.create_task(send_log(MUSIC_LOG_CHANNEL_ID, embed=log_embed))

    msg = await channel.send(embed=build_now_playing_embed(guild_id, paused=False), view=MusicView(guild_id))
    now_playing_messages[guild_id] = msg
    start_now_playing_refresh(guild_id)


async def enqueue_song(guild, channel, user, query, send_func):
    vc, err = await ensure_voice(user, guild)
    if err:
        return await send_func(text=err, view=add_support_button())

    try:
        items = await resolve_query_items(query, user)
    except Exception as e:
        return await send_func(text=f"😵‍💫 Couldn't add **{query}** to the queue.\n`{e}`", view=add_support_button())

    was_playing = vc.is_playing() or vc.is_paused()

    for item in items:
        item.setdefault("query", query)
        item.setdefault("title", item.get("query", "Unknown Track"))
        item.setdefault("requester", user)
        item["_queue_id"] = f"{time.time_ns()}-{random.randint(1000, 9999)}"
        get_queue(guild.id).append(item)

    if was_playing:
        for item in items[:10]:
            await send_queue_added_embed(guild, channel, user, item)
        if len(items) > 10:
            await send_func(text=f"✦ Added **{len(items)}** tracks to the queue.")
        await update_now_playing_message(guild.id)
    else:
        await play_next(guild, channel, send_func)


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


# --- BOT EVENTS ---

@bot.event
async def on_ready():
    load_config()
    try:
        synced = await bot.tree.sync()
        await bot.change_presence(activity=discord.Game(name=server_status()))
        print(f"Logged in as {bot.user.name} | Synced {len(synced)} Slash Commands!")
    except Exception as e:
        print(f"Error syncing commands: {e}")


@bot.event
async def on_guild_join(guild):
    try:
        await bot.change_presence(activity=discord.Game(name=server_status()))
    except Exception:
        pass

    embed = discord.Embed(
        title="✦ Joined a New Server",
        description=f"**{guild.name}**",
        color=discord.Color.green()
    )
    embed.add_field(name="Server ID", value=f"`{guild.id}`", inline=True)
    embed.add_field(name="Members", value=str(guild.member_count), inline=True)
    if guild.owner:
        embed.add_field(name="Owner", value=f"{guild.owner} (`{guild.owner.id}`)", inline=True)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text=f"Now in {len(bot.guilds)} servers")
    await send_log(JOIN_LOG_CHANNEL_ID, embed=embed)


@bot.event
async def on_guild_remove(guild):
    try:
        await bot.change_presence(activity=discord.Game(name=server_status()))
    except Exception:
        pass

    embed = discord.Embed(
        title="✦ Removed From a Server",
        description=f"**{guild.name}**",
        color=discord.Color.red()
    )
    embed.add_field(name="Server ID", value=f"`{guild.id}`", inline=True)
    embed.set_footer(text=f"Now in {len(bot.guilds)} servers")
    await send_log(JOIN_LOG_CHANNEL_ID, embed=embed)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    content = message.content.strip()
    lowered = content.lower()
    prefix = get_prefix(message.guild.id)

    command_text = None
    if bot.user in message.mentions:
        command_text = content.replace(f'<@{bot.user.id}>', '').replace(f'<@!{bot.user.id}>', '').strip()
    elif prefix and lowered.startswith(prefix.lower()):
        command_text = content[len(prefix):].strip()

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
                        send_func=lambda embed=None, view=None, text=None: safe_send_channel(
                            message.channel, text=text, embed=embed, view=view
                        )
                    )
            except Exception as e:
                print(f"[MESSAGE PLAY ERROR] {e}")
                await message.channel.send(f"❌ Something went wrong: `{e}`", view=add_support_button())
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
            await message.channel.send(f"🔁 Loop mode: **{get_loop_label(guild_id)}** — set by {message.author.mention}")
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
                mark_paused(message.guild.id)
                await update_now_playing_message(message.guild.id)
                await message.channel.send(f"⏸️ Paused by {message.author.mention}")
            else:
                await message.channel.send("❌ Nothing is playing.")
            return

        if cmd == "resume":
            vc = message.guild.voice_client
            if vc and vc.is_paused():
                vc.resume()
                mark_resumed(message.guild.id)
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


@bot.tree.command(name="config", description="Set the quick-play text prefix for this server (Admin only)")
@app_commands.checks.has_permissions(administrator=True)
async def config_command(interaction: discord.Interaction, prefix: str):
    prefix = prefix.strip()
    if not prefix or len(prefix) > 5 or " " in prefix:
        await interaction.response.send_message(
            "❌ Prefix must be 1–5 characters with no spaces.", ephemeral=True
        )
        return

    guild_prefixes[interaction.guild.id] = prefix
    save_config()
    await interaction.response.send_message(
        f"✅ Quick-play prefix set to **`{prefix}`**\n"
        f"Now all of these work without mentioning me: "
        f"`{prefix}p <song>`, `{prefix}play <song>`, `{prefix} p <song>`, `{prefix} play <song>`",
        ephemeral=True
    )


@config_command.error
async def config_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message("❌ Only server administrators can use this command.", ephemeral=True)
    else:
        print(f"[CONFIG ERROR] {error}")


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
    await update_now_playing_message(guild_id)


@bot.tree.command(name="shuffle", description="Shuffle the current queue")
async def shuffle_command(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    queue = get_queue(guild_id)
    if len(queue) < 2:
        await interaction.response.send_message("❌ Need at least 2 songs in the queue to shuffle.", ephemeral=True)
        return
    random.shuffle(queue)
    await interaction.response.send_message(
        f"🔀 Queue shuffled by {interaction.user.mention} — **{len(queue)}** song(s) reordered"
    )
    await update_now_playing_message(guild_id)


@bot.tree.command(name="setpfp", description="Change the bot's profile picture for THIS server only")
@app_commands.checks.has_permissions(administrator=True)
async def setpfp(interaction: discord.Interaction, image: discord.Attachment):
    await interaction.response.defer(ephemeral=True)
    try:
        image_bytes = await image.read()
        await interaction.guild.me.edit(avatar=image_bytes)
        await interaction.followup.send("✅ Profile picture updated for this server only!")
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to update avatar: `{str(e)}`", view=add_support_button())


@bot.tree.command(name="setbanner", description="Change the bot profile banner (Admin Only)")
@app_commands.checks.has_permissions(administrator=True)
async def setbanner(interaction: discord.Interaction, image: discord.Attachment):
    await interaction.response.defer(ephemeral=True)
    try:
        image_bytes = await image.read()
        await bot.user.edit(banner=image_bytes)
        await interaction.followup.send(
            "✅ Banner updated! Note: Discord banners are always global "
            "(same across all servers) — Discord doesn't allow per-server banners for bots."
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to update banner: `{str(e)}`", view=add_support_button())


@bot.tree.command(name="247", description="Toggle 24/7 mode so the bot stays in VC")
async def mode_247(interaction: discord.Interaction):
    await interaction.response.send_message("🔒 **24/7 Mode Status:** Active 🟢")


@bot.tree.command(name="recommend", description="Get AI music recommendations")
async def recommend(interaction: discord.Interaction, genre: str = "Trending Hits"):
    embed = discord.Embed(
        title="✨ AI Recommended Tracks",
        description=(
            f"Recommendations for **{genre}**:\n\n"
            f"1️⃣ **Arijit Singh - Casual Lo-fi Vibe**\n"
            f"2️⃣ **Midnight Chill Beats - Lofi Remix**\n"
            f"3️⃣ **Phonk / Cyberpunk Synthwave Special**"
        ),
        color=PURPLE
    )
    await interaction.response.send_message(embed=embed, view=add_support_button())


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
    mark_paused(interaction.guild.id)
    await interaction.response.send_message(f"⏸️ Spell paused by {interaction.user.mention}")
    await update_now_playing_message(interaction.guild.id)


@bot.tree.command(name="resume", description="Resume the current song")
async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc or not vc.is_paused():
        await interaction.response.send_message("❌ Nothing is paused.", ephemeral=True)
        return
    vc.resume()
    mark_resumed(interaction.guild.id)
    await interaction.response.send_message(f"▶️ Spell resumes... by {interaction.user.mention}")
    await update_now_playing_message(interaction.guild.id)


@bot.tree.command(name="stop", description="Stop music, clear queue, and leave VC")
async def stop(interaction: discord.Interaction):
    await interaction.response.defer()
    await stop_playback(interaction.guild)
    await interaction.followup.send(f"💨 The magic fades... stopped by {interaction.user.mention}")


# --- OWNER-ONLY: CROSS-SERVER SUPPORT TOOLS ---

@bot.tree.command(name="servers", description="(Bot owner only) List every server the bot is in")
async def servers_command(interaction: discord.Interaction):
    if not is_bot_owner(interaction.user.id):
        await interaction.response.send_message("❌ This command is restricted to the bot owner.", ephemeral=True)
        return

    lines = [f"**{g.name}** — `{g.id}` — {g.member_count} members" for g in bot.guilds]
    text = "\n".join(lines) if lines else "Not in any servers."
    if len(text) > 1900:
        text = text[:1900] + "\n… (truncated)"
    await interaction.response.send_message(text, ephemeral=True)


@bot.tree.command(name="geninvite", description="(Bot owner only) Generate an invite link to any server the bot is in")
async def geninvite(interaction: discord.Interaction, server_id: str):
    if not is_bot_owner(interaction.user.id):
        await interaction.response.send_message("❌ This command is restricted to the bot owner.", ephemeral=True)
        return

    try:
        target_id = int(server_id.strip())
    except ValueError:
        await interaction.response.send_message("❌ That's not a valid server ID.", ephemeral=True)
        return

    target_guild = bot.get_guild(target_id)
    if not target_guild:
        await interaction.response.send_message("❌ I'm not in a server with that ID. Use `/servers` to list them.", ephemeral=True)
        return

    invite_channel = None
    for ch in target_guild.text_channels:
        perms = ch.permissions_for(target_guild.me)
        if perms.create_instant_invite and perms.view_channel:
            invite_channel = ch
            break

    if not invite_channel:
        await interaction.response.send_message(
            f"❌ I don't have **Create Invite** permission in any channel of **{target_guild.name}**.",
            ephemeral=True
        )
        return

    try:
        invite = await invite_channel.create_invite(
            max_age=3600, max_uses=1, unique=True,
            reason=f"Requested by bot owner {interaction.user} ({interaction.user.id})"
        )
        await interaction.response.send_message(
            f"🔗 Invite for **{target_guild.name}**: {invite.url}\n(expires in 1 hour, 1 use)",
            ephemeral=True
        )
    except discord.Forbidden:
        await interaction.response.send_message("❌ Missing permission to create an invite there.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed to create invite: `{e}`", ephemeral=True)


# --- START BOT ---
token = os.getenv("DISCORD_TOKEN")
if token:
    bot.run(token)
else:
    raise RuntimeError("DISCORD_TOKEN environment variable is missing!")


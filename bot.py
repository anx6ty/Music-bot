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
    for lib_name in (
        "libopus.so.0",
        "libopus.so",
        "opus",
        "/usr/lib/x86_64-linux-gnu/libopus.so.0",
    ):
        try:
            discord.opus.load_opus(lib_name)
            print(f"[OPUS] Loaded successfully using: {lib_name}")
            break
        except OSError:
            continue

    if not discord.opus.is_loaded():
        print("[OPUS] WARNING: Could not load opus library!")


# ============================================================
# BOT SETUP
# ============================================================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

PURPLE = discord.Color.from_rgb(155, 93, 229)

SUPPORT_SERVER_INVITE = "https://discord.gg/5ygnUWdG7D"


# ============================================================
# SUPPORT BUTTON
# ============================================================

def add_support_button(view=None):
    if view is None:
        view = discord.ui.View(timeout=None)

    view.add_item(
        discord.ui.Button(
            label="Support",
            emoji="🛟",
            style=discord.ButtonStyle.link,
            url=SUPPORT_SERVER_INVITE,
        )
    )

    return view


# ============================================================
# CUSTOM EMOJIS
# ============================================================

CUSTOM_EMOJI_IDS = {
    "pause": None,
    "resume": None,
    "skip": None,
    "shuffle": None,
    "loop_off": None,
    "loop_track": None,
    "loop_queue": None,
    "stop": None,
}

_FALLBACK_EMOJI = {
    "pause": "⏸️",
    "resume": "▶️",
    "skip": "⏭️",
    "shuffle": "🔀",
    "loop_off": "🔁",
    "loop_track": "🔂",
    "loop_queue": "🔁",
    "stop": "⏹️",
}


def get_emoji(key):
    emoji_id = CUSTOM_EMOJI_IDS.get(key)

    if emoji_id:
        return discord.PartialEmoji(
            name=key,
            id=emoji_id
        )

    return _FALLBACK_EMOJI[key]


# ============================================================
# YT-DLP / FFMPEG
# ============================================================

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
    "extractor_args": {
        "youtube": {
            "player_client": ["android", "web"],
        }
    },
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)


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

CONFIG_FILE = "guild_config.json"

QUEUE_NOTICE_BUTTON_TIMEOUT = 30
QUEUE_NOTICE_DELETE_AFTER = 60

NOW_PLAYING_REFRESH_SECONDS = 2


# ============================================================
# CONFIG
# ============================================================

def load_config():
    global guild_prefixes

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        guild_prefixes = {
            int(k): str(v)
            for k, v in data.get("prefixes", {}).items()
        }

    except (
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
    ):
        guild_prefixes = {}


def save_config():
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "prefixes": {
                        str(k): v
                        for k, v in guild_prefixes.items()
                    }
                },
                f,
                indent=2,
            )

    except OSError as e:
        print(f"[CONFIG SAVE ERROR] {e}")


def get_prefix(guild_id):
    return guild_prefixes.get(guild_id, "")


# ============================================================
# BOT OWNER
# ============================================================

BOT_OWNER_IDS = {
    int(x)
    for x in os.getenv("BOT_OWNER_IDS", "").split(",")
    if x.strip().isdigit()
}


def is_bot_owner(user_id):
    return user_id in BOT_OWNER_IDS


# ============================================================
# QUEUE HELPERS
# ============================================================

def get_queue(guild_id):
    return queues.setdefault(guild_id, [])


def is_playlist_url(query):
    q = str(query).lower()

    return (
        "list=" in q
        or "/playlist" in q
        or "music.youtube.com/playlist" in q
    )


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


def get_elapsed_seconds(guild_id):
    song = current_song.get(guild_id)

    if not song:
        return 0

    now = time.time()

    elapsed = (
        now
        - song.get("start_time", now)
        - song.get("paused_total", 0.0)
    )

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
        song["paused_total"] = (
            song.get("paused_total", 0.0)
            + (time.time() - song["paused_at"])
        )

        song["paused_at"] = None


def format_queue_names(guild_id, limit=8):
    queue = get_queue(guild_id)

    if not queue:
        return "empty"

    lines = []

    for i, item in enumerate(queue[:limit], 1):
        title = (
            item.get("title")
            or item.get("query")
            or "Unknown"
        )

        lines.append(f"{i}. {title[:80]}")

    if len(queue) > limit:
        lines.append(
            f"… and {len(queue) - limit} more"
        )

    return "\n".join(lines)


# ============================================================
# BOT PROFILE STATUS
# ============================================================
# IMPORTANT:
# This status NEVER changes to the song name.
# It always shows how many servers the bot is in.
#
# The song name is still shown in the VOICE CHANNEL STATUS
# through set_vc_status() below.
# ============================================================

def server_status():
    return f"✦ Casting {len(bot.guilds)} servers"


async def update_bot_presence():
    try:
        await bot.change_presence(
            activity=discord.Game(
                name=server_status()
            )
        )

    except Exception as e:
        print(f"[PRESENCE ERROR] {e}")


# ============================================================
# LOGGING CONFIG
# ============================================================

# These are stored per bot process / environment.
# Use environment variables if you want initial values.
#
# IMPORTANT:
# Do NOT put private IDs in public code.
#
# Commands:
# /setmusiclogs
# /setjoinlogs
# /seterrorlogs

LOG_CHANNELS = {
    "music": None,
    "join": None,
    "error": None,
}


def channel_link(guild_id, channel_id):
    return (
        f"https://discord.com/channels/"
        f"{guild_id}/{channel_id}"
    )


def server_link(guild_id):
    return (
        f"https://discord.com/channels/"
        f"{guild_id}"
    )


def get_configured_log_channel_id(log_type):
    return LOG_CHANNELS.get(log_type)


async def get_log_channel(log_type):
    channel_id = get_configured_log_channel_id(log_type)

    if not channel_id:
        return None

    try:
        channel_id = int(channel_id)
    except (TypeError, ValueError):
        return None

    channel = bot.get_channel(channel_id)

    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception as e:
            print(
                f"[LOG CHANNEL ERROR] "
                f"Couldn't fetch {channel_id}: {e}"
            )
            return None

    return channel


async def send_log(
    log_type,
    embed=None,
    text=None,
):
    channel = await get_log_channel(log_type)

    if not channel:
        return

    try:
        await channel.send(
            content=text,
            embed=embed
        )

    except Exception as e:
        print(
            f"[LOG SEND ERROR] "
            f"{log_type}: {e}"
        )


def make_server_log_fields(guild):
    fields = []

    fields.append(
        (
            "Server",
            f"[{guild.name}]({server_link(guild.id)})\n"
            f"`{guild.id}`",
            True,
        )
    )

    return fields


# ============================================================
# SHARED HTTP SESSION
# ============================================================

_http_session = None


async def get_http_session():
    global _http_session

    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession()

    return _http_session


# ============================================================
# VOICE CHANNEL STATUS
# ============================================================
# KEEP THIS.
#
# This is separate from the bot profile status.
# It will continue showing the currently playing song.
# ============================================================

async def set_vc_status(channel_id, status_text):
    if not channel_id:
        return

    session = await get_http_session()

    url = (
        f"https://discord.com/api/v10/"
        f"channels/{channel_id}/voice-status"
    )

    headers = {
        "Authorization": f"Bot {os.getenv('DISCORD_TOKEN')}",
        "Content-Type": "application/json",
    }

    try:
        async with session.put(
            url,
            headers=headers,
            json={
                "status": (status_text or "")[:500]
            },
        ) as resp:

            if resp.status not in (200, 204):
                body = await resp.text()

                print(
                    f"[VC STATUS ERROR] "
                    f"{resp.status}: {body}"
                )

    except Exception as e:
        print(f"[VC STATUS ERROR] {e}")


# ============================================================
# SPOTIFY
# ============================================================

SPOTIFY_CLIENT_ID = os.getenv(
    "SPOTIFY_CLIENT_ID"
)

SPOTIFY_CLIENT_SECRET = os.getenv(
    "SPOTIFY_CLIENT_SECRET"
)

_spotify_token_cache = {
    "token": None,
    "expires_at": 0,
}

SPOTIFY_TRACK_RE = re.compile(
    r"open\.spotify\.com/(?:intl-\w+/)?track/([A-Za-z0-9]+)"
)

SPOTIFY_PLAYLIST_RE = re.compile(
    r"open\.spotify\.com/(?:intl-\w+/)?playlist/([A-Za-z0-9]+)"
)

SPOTIFY_ALBUM_RE = re.compile(
    r"open\.spotify\.com/(?:intl-\w+/)?album/([A-Za-z0-9]+)"
)


def spotify_url(query):
    if not isinstance(query, str):
        return False

    try:
        return urlparse(
            query.strip()
        ).netloc.lower().endswith(
            "spotify.com"
        )

    except ValueError:
        return False


async def resolve_spotify_short_link(url):
    try:
        session = await get_http_session()

        async with session.get(
            url,
            allow_redirects=True,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:

            return str(resp.url)

    except Exception as e:
        print(
            f"[SPOTIFY REDIRECT ERROR] {e}"
        )

        return url


async def get_spotify_token():
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return None

    if (
        _spotify_token_cache["token"]
        and time.time()
        < _spotify_token_cache["expires_at"] - 30
    ):
        return _spotify_token_cache["token"]

    try:
        session = await get_http_session()

        auth = aiohttp.BasicAuth(
            SPOTIFY_CLIENT_ID,
            SPOTIFY_CLIENT_SECRET,
        )

        async with session.post(
            "https://accounts.spotify.com/api/token",
            data={
                "grant_type":
                    "client_credentials"
            },
            auth=auth,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:

            if resp.status != 200:
                return None

            data = await resp.json()

            _spotify_token_cache["token"] = (
                data.get("access_token")
            )

            _spotify_token_cache["expires_at"] = (
                time.time()
                + data.get(
                    "expires_in",
                    3600
                )
            )

            return _spotify_token_cache["token"]

    except Exception as e:
        print(
            f"[SPOTIFY TOKEN ERROR] {e}"
        )

        return None


async def spotify_get(endpoint):
    token = await get_spotify_token()

    if not token:
        return None

    try:
        session = await get_http_session()

        headers = {
            "Authorization":
                f"Bearer {token}"
        }

        async with session.get(
            f"https://api.spotify.com/v1/{endpoint}",
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:

            if resp.status != 200:
                return None

            return await resp.json()

    except Exception as e:
        print(
            f"[SPOTIFY API ERROR] {e}"
        )

        return None


def _track_search_query(track):
    artists = ", ".join(
        a["name"]
        for a in track.get("artists", [])
        if a.get("name")
    )

    title = track.get("name", "")

    return (
        f"{artists} - {title}"
    ).strip(" -") or title


async def _spotify_oembed_title(url):
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
        print(
            f"[SPOTIFY OEMBED ERROR] {e}"
        )

        return None


async def resolve_spotify_link(url):
    if (
        "/s/" in url
        or not (
            SPOTIFY_TRACK_RE.search(url)
            or SPOTIFY_PLAYLIST_RE.search(url)
            or SPOTIFY_ALBUM_RE.search(url)
        )
    ):
        url = await resolve_spotify_short_link(url)

    has_api = bool(
        SPOTIFY_CLIENT_ID
        and SPOTIFY_CLIENT_SECRET
    )

    track_match = SPOTIFY_TRACK_RE.search(url)

    if track_match:

        if has_api:
            data = await spotify_get(
                f"tracks/{track_match.group(1)}"
            )

            if data:
                return [
                    _track_search_query(data)
                ]

        title = await _spotify_oembed_title(url)

        return [title] if title else []

    playlist_match = SPOTIFY_PLAYLIST_RE.search(url)

    if playlist_match:

        if not has_api:
            return None

        data = await spotify_get(
            f"playlists/{playlist_match.group(1)}"
        )

        if not data:
            return []

        queries = []

        for item in data.get(
            "tracks",
            {}
        ).get("items", []):

            track = item.get("track")

            if track:
                queries.append(
                    _track_search_query(track)
                )

        return queries

    album_match = SPOTIFY_ALBUM_RE.search(url)

    if album_match:

        if not has_api:
            return None

        data = await spotify_get(
            f"albums/{album_match.group(1)}"
        )

        if not data:
            return []

        return [
            _track_search_query(t)
            for t in data.get(
                "tracks",
                {}
            ).get("items", [])
        ]

    return []


async def resolve_query_items(query, requester):
    if spotify_url(query):

        queries = await resolve_spotify_link(
            query
        )

        if queries is None:
            raise RuntimeError(
                "That's a Spotify playlist/album link. "
                "Add SPOTIFY_CLIENT_ID and "
                "SPOTIFY_CLIENT_SECRET."
            )

        if not queries:
            raise RuntimeError(
                "Couldn't resolve that Spotify link."
            )

        return [
            {
                "query": q,
                "title": q,
                "requester": requester,
            }
            for q in queries
        ]

    loop = asyncio.get_event_loop()

    data = await loop.run_in_executor(
        None,
        lambda: ytdl.extract_info(
            query,
            download=False
        )
    )

    if not data:
        raise RuntimeError(
            "No result found."
        )

    if data.get("entries"):

        entries = [
            e
            for e in data["entries"]
            if e
        ]

        if not entries:
            raise RuntimeError(
                "No playable entries found."
            )

        if is_playlist_url(query):

            return [
                {
                    "query":
                        e.get("webpage_url")
                        or e.get("url")
                        or e.get("title"),

                    "title":
                        e.get(
                            "title",
                            "Unknown Track"
                        ),

                    "thumbnail":
                        e.get("thumbnail"),

                    "webpage_url":
                        e.get(
                            "webpage_url",
                            ""
                        ),

                    "requester":
                        requester,
                }
                for e in entries
            ]

        e = entries[0]

        return [
            {
                "query":
                    e.get("webpage_url")
                    or query,

                "title":
                    e.get(
                        "title",
                        "Unknown Track"
                    ),

                "thumbnail":
                    e.get("thumbnail"),

                "webpage_url":
                    e.get(
                        "webpage_url",
                        ""
                    ),

                "requester":
                    requester,

                "stream_url":
                    e.get("url"),

                "duration_sec":
                    e.get("duration") or 0,
            }
        ]

    return [
        {
            "query": query,

            "title":
                data.get(
                    "title",
                    query
                ),

            "thumbnail":
                data.get("thumbnail"),

            "webpage_url":
                data.get(
                    "webpage_url",
                    ""
                ),

            "requester":
                requester,

            "stream_url":
                data.get("url"),

            "duration_sec":
                data.get("duration") or 0,
        }
    ]


# ============================================================
# NOW PLAYING
# ============================================================

def build_now_playing_embed(
    guild_id,
    paused=False
):
    song = current_song.get(guild_id)

    if not song:
        embed = discord.Embed(
            description=(
                "### ◈ Nothing Casting\n"
                "▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹"
            ),
            color=PURPLE,
        )

        embed.set_footer(
            text="✦ Queue a track with /play"
        )

        return embed

    elapsed = get_elapsed_seconds(
        guild_id
    )

    duration_sec = (
        song.get("duration_sec")
        or 0
    )

    bar = build_progress_bar(
        elapsed,
        duration_sec
    )

    embed = discord.Embed(
        description=(
            f"### [{song['title']}]"
            f"({song.get('webpage_url', '') or song['title']})\n"
            f"{bar}\n"
            f"`{format_time(elapsed)} / "
            f"{song.get('duration_str', 'Unknown')}`"
        ),
        color=PURPLE,
    )

    embed.set_author(
        name=(
            "⏸️  SPELL PAUSED"
            if paused
            else "◈  NOW CASTING"
        )
    )

    if song.get("thumbnail"):
        embed.set_image(
            url=song["thumbnail"]
        )

    embed.add_field(
        name="⏱️ DURATION",
        value=f"`{song.get('duration_str', 'Unknown')}`",
        inline=True,
    )

    embed.add_field(
        name="🔁 LOOP",
        value=f"`{get_loop_label(guild_id)}`",
        inline=True,
    )

    embed.add_field(
        name="📃 UP NEXT",
        value=format_queue_names(guild_id),
        inline=False,
    )

    requester = song.get("requester")

    if requester:
        embed.set_footer(
            text=(
                f"🪄 cast by "
                f"{requester.display_name} · "
                f"updates every 2s"
            ),
            icon_url=requester.display_avatar.url,
        )

    return embed


async def update_now_playing_message(
    guild_id
):
    msg = now_playing_messages.get(
        guild_id
    )

    if not msg or not current_song.get(
        guild_id
    ):
        return None

    guild = bot.get_guild(guild_id)

    vc = (
        guild.voice_client
        if guild
        else None
    )

    try:
        await msg.edit(
            embed=build_now_playing_embed(
                guild_id,
                paused=(
                    vc.is_paused()
                    if vc
                    else False
                ),
            ),
            view=MusicView(guild_id),
        )

        return None

    except discord.NotFound:
        now_playing_messages.pop(
            guild_id,
            None
        )

        return None

    except discord.HTTPException as e:

        if getattr(e, "status", None) == 429:
            retry_after = (
                getattr(
                    e,
                    "retry_after",
                    None
                )
                or 3
            )

            print(
                f"[REFRESH RATE LIMITED] "
                f"{guild_id}: "
                f"backing off {retry_after}s"
            )

            return retry_after

        print(
            f"[REFRESH ERROR] {e}"
        )

        return None


async def now_playing_refresh_loop(
    guild_id
):
    try:

        while current_song.get(
            guild_id
        ):

            await asyncio.sleep(
                NOW_PLAYING_REFRESH_SECONDS
            )

            if not current_song.get(
                guild_id
            ):
                break

            backoff = (
                await update_now_playing_message(
                    guild_id
                )
            )

            if backoff:
                await asyncio.sleep(
                    backoff
                )

    except asyncio.CancelledError:
        pass

    finally:
        progress_tasks.pop(
            guild_id,
            None
        )


def start_now_playing_refresh(
    guild_id
):
    old = progress_tasks.get(
        guild_id
    )

    if old and not old.done():
        old.cancel()

    progress_tasks[guild_id] = (
        asyncio.create_task(
            now_playing_refresh_loop(
                guild_id
            )
        )
    )


async def delete_now_playing(
    guild_id
):
    task = progress_tasks.pop(
        guild_id,
        None
    )

    if task and not task.done():
        task.cancel()

    msg = now_playing_messages.pop(
        guild_id,
        None
    )

    if msg:

        try:
            await msg.delete()

        except (
            discord.NotFound,
            discord.HTTPException,
        ):
            pass


# ============================================================
# QUEUE NOTICE
# ============================================================

async def send_queue_added_embed(
    guild,
    channel,
    user,
    item
):
    embed = discord.Embed(
        title="✦ Added to Queue",
        description=(
            f"{user.mention} added **"
            f"{item.get('title', item.get('query', 'Unknown Track'))}"
            f"**"
        ),
        color=PURPLE,
    )

    if item.get("webpage_url"):
        embed.url = item[
            "webpage_url"
        ]

    if item.get("thumbnail"):
        embed.set_image(
            url=item["thumbnail"]
        )

    embed.set_footer(
        text=(
            "Buttons active for 30 seconds • "
            "This notice expires after 1 minute"
        )
    )

    view = QueueNoticeView(
        guild.id,
        item["_queue_id"]
    )

    msg = await channel.send(
        embed=embed,
        view=view
    )

    queue_notice_messages.setdefault(
        guild.id,
        set()
    ).add(msg)

    async def delete_later():

        await asyncio.sleep(
            QUEUE_NOTICE_DELETE_AFTER
        )

        try:
            await msg.delete()

        except (
            discord.NotFound,
            discord.HTTPException,
        ):
            pass

        queue_notice_messages.get(
            guild.id,
            set()
        ).discard(msg)

    asyncio.create_task(
        delete_later()
    )

    return msg


class QueueNoticeView(discord.ui.View):

    def __init__(
        self,
        guild_id,
        queue_item_id
    ):
        super().__init__(
            timeout=QUEUE_NOTICE_BUTTON_TIMEOUT
        )

        self.guild_id = guild_id
        self.queue_item_id = queue_item_id

        move_top_btn = discord.ui.Button(
            label="Move to Top",
            emoji="⬆️",
            style=discord.ButtonStyle.primary,
        )

        move_top_btn.callback = (
            self.move_top
        )

        self.add_item(
            move_top_btn
        )

        remove_btn = discord.ui.Button(
            label="Remove",
            emoji="🗑️",
            style=discord.ButtonStyle.danger,
        )

        remove_btn.callback = (
            self.remove_queue
        )

        self.add_item(
            remove_btn
        )

        add_support_button(self)

    def find_item(self):

        for item in get_queue(
            self.guild_id
        ):

            if (
                item.get("_queue_id")
                == self.queue_item_id
            ):
                return item

        return None

    async def on_timeout(self):

        for child in self.children:
            child.disabled = True

        messages = queue_notice_messages.get(
            self.guild_id,
            set()
        )

        for msg in list(messages):

            try:
                await msg.edit(
                    view=self
                )

            except (
                discord.NotFound,
                discord.HTTPException,
            ):
                pass

    async def move_top(
        self,
        interaction
    ):
        item = self.find_item()

        if not item:
            await interaction.response.send_message(
                "❌ That queue item is no longer available.",
                ephemeral=True,
            )
            return

        queue = get_queue(
            self.guild_id
        )

        queue.remove(item)
        queue.insert(0, item)

        await interaction.response.send_message(
            f"⬆️ **{item.get('title', item.get('query', 'Track'))}** "
            "moved to the top of the queue.",
            ephemeral=True,
        )

        await update_now_playing_message(
            self.guild_id
        )

    async def remove_queue(
        self,
        interaction
    ):
        item = self.find_item()

        if not item:
            await interaction.response.send_message(
                "❌ That queue item is no longer available.",
                ephemeral=True,
            )
            return

        get_queue(
            self.guild_id
        ).remove(item)

        await interaction.response.send_message(
            f"🗑️ Removed **"
            f"{item.get('title', item.get('query', 'Track'))}"
            f"** from the queue.",
            ephemeral=True,
        )

        await update_now_playing_message(
            self.guild_id
        )


# ============================================================
# MUSIC CONTROL VIEW
# ============================================================

class MusicView(discord.ui.View):

    def __init__(self, guild_id):
        super().__init__(timeout=None)

        self.guild_id = guild_id

        guild = bot.get_guild(
            guild_id
        )

        vc = (
            guild.voice_client
            if guild
            else None
        )

        is_paused = (
            vc.is_paused()
            if vc
            else False
        )

        mode = loop_modes.get(
            guild_id
        )

        pause_btn = discord.ui.Button(
            label=(
                "Resume"
                if is_paused
                else "Pause"
            ),
            emoji=(
                get_emoji("resume")
                if is_paused
                else get_emoji("pause")
            ),
            style=discord.ButtonStyle.primary,
        )

        pause_btn.callback = (
            self.pause_resume_callback
        )

        self.add_item(
            pause_btn
        )

        skip_btn = discord.ui.Button(
            label="Skip",
            emoji=get_emoji("skip"),
            style=discord.ButtonStyle.secondary,
        )

        skip_btn.callback = (
            self.skip_callback
        )

        self.add_item(
            skip_btn
        )

        shuffle_btn = discord.ui.Button(
            label="Shuffle",
            emoji=get_emoji("shuffle"),
            style=discord.ButtonStyle.secondary,
        )

        shuffle_btn.callback = (
            self.shuffle_callback
        )

        self.add_item(
            shuffle_btn
        )

        if mode == "track":
            loop_label = "Loop: Track"
            loop_emoji = get_emoji(
                "loop_track"
            )
            loop_style = (
                discord.ButtonStyle.success
            )

        elif mode == "queue":
            loop_label = "Loop: Queue"
            loop_emoji = get_emoji(
                "loop_queue"
            )
            loop_style = (
                discord.ButtonStyle.success
            )

        else:
            loop_label = "Loop: Off"
            loop_emoji = get_emoji(
                "loop_off"
            )
            loop_style = (
                discord.ButtonStyle.secondary
            )

        loop_btn = discord.ui.Button(
            label=loop_label,
            emoji=loop_emoji,
            style=loop_style,
        )

        loop_btn.callback = (
            self.loop_callback
        )

        self.add_item(
            loop_btn
        )

        stop_btn = discord.ui.Button(
            label="Stop",
            emoji=get_emoji("stop"),
            style=discord.ButtonStyle.danger,
        )

        stop_btn.callback = (
            self.stop_callback
        )

        self.add_item(
            stop_btn
        )

        add_support_button(self)

    async def pause_resume_callback(
        self,
        interaction
    ):
        vc = interaction.guild.voice_client

        if not vc:
            await interaction.response.send_message(
                "❌ I'm not connected to a voice channel.",
                ephemeral=True,
            )
            return

        if vc.is_playing():

            vc.pause()

            mark_paused(
                self.guild_id
            )

            await interaction.response.edit_message(
                embed=build_now_playing_embed(
                    self.guild_id,
                    paused=True,
                ),
                view=MusicView(
                    self.guild_id
                ),
            )

            await interaction.channel.send(
                f"⏸️ Spell paused by "
                f"{interaction.user.mention}"
            )

        elif vc.is_paused():

            vc.resume()

            mark_resumed(
                self.guild_id
            )

            await interaction.response.edit_message(
                embed=build_now_playing_embed(
                    self.guild_id,
                    paused=False,
                ),
                view=MusicView(
                    self.guild_id
                ),
            )

            await interaction.channel.send(
                f"▶️ Spell resumes... by "
                f"{interaction.user.mention}"
            )

        else:
            await interaction.response.send_message(
                "❌ Nothing is playing.",
                ephemeral=True,
            )

    async def skip_callback(
        self,
        interaction
    ):
        vc = interaction.guild.voice_client

        if vc and (
            vc.is_playing()
            or vc.is_paused()
        ):

            vc.stop()

            await interaction.response.send_message(
                f"⏭️ Skipped by "
                f"{interaction.user.mention}"
            )

        else:
            await interaction.response.send_message(
                "❌ Nothing to skip.",
                ephemeral=True,
            )

    async def shuffle_callback(
        self,
        interaction
    ):
        queue = get_queue(
            self.guild_id
        )

        if len(queue) < 2:
            await interaction.response.send_message(
                "❌ Need at least 2 songs in the queue to shuffle.",
                ephemeral=True,
            )
            return

        random.shuffle(queue)

        await interaction.response.send_message(
            f"🔀 Queue shuffled by "
            f"{interaction.user.mention} — "
            f"**{len(queue)}** song(s) reordered"
        )

    async def loop_callback(
        self,
        interaction
    ):
        loop_modes[
            self.guild_id
        ] = next_loop_mode(
            loop_modes.get(
                self.guild_id
            )
        )

        vc = interaction.guild.voice_client

        is_paused = (
            vc.is_paused()
            if vc
            else False
        )

        await interaction.response.edit_message(
            embed=build_now_playing_embed(
                self.guild_id,
                paused=is_paused,
            ),
            view=MusicView(
                self.guild_id
            ),
        )

    async def stop_callback(
        self,
        interaction
    ):
        await interaction.response.defer()

        await stop_playback(
            interaction.guild,
            delete_message=interaction.message,
        )

        await interaction.channel.send(
            f"💨 The magic fades... "
            f"stopped by "
            f"{interaction.user.mention}"
        )


# ============================================================
# VOICE
# ============================================================

async def ensure_voice(
    user,
    guild
):
    if not user.voice or not user.voice.channel:
        return (
            None,
            "🚫 Yo, hop into a voice channel first — "
            "I can't vibe alone!"
        )

    voice_channel = user.voice.channel

    vc = guild.voice_client

    if not vc:
        vc = await voice_channel.connect(
            self_deaf=True,
            self_mute=False
        )

    elif vc.channel != voice_channel:
        await vc.move_to(
            voice_channel
        )

    return vc, None


async def stop_playback(
    guild,
    delete_message=None
):
    guild_id = guild.id

    queues[guild_id] = []
    loop_modes[guild_id] = None

    vc = guild.voice_client

    if vc:

        channel_id = (
            vc.channel.id
            if vc.channel
            else None
        )

        vc.stop()

        try:
            await vc.disconnect()

        except discord.HTTPException:
            pass

        await set_vc_status(
            channel_id,
            ""
        )

    current_song.pop(
        guild_id,
        None
    )

    await delete_now_playing(
        guild_id
    )

    # Profile status stays server count only.
    await update_bot_presence()

    if delete_message:
        try:
            await delete_message.delete()

        except (
            discord.NotFound,
            discord.HTTPException,
        ):
            pass


# ============================================================
# PLAY NEXT
# ============================================================

async def play_next(
    guild,
    channel,
    send_func=None
):
    guild_id = guild.id

    vc = guild.voice_client

    if not vc:
        return

    if (
        loop_modes.get(guild_id)
        == "track"
        and current_song.get(guild_id)
    ):

        query = current_song[
            guild_id
        ]["_query"]

        requester = current_song[
            guild_id
        ]["requester"]

    else:

        queue = get_queue(
            guild_id
        )

        if not queue:

            current_song.pop(
                guild_id,
                None
            )

            channel_id = (
                vc.channel.id
                if vc.channel
                else None
            )

            await delete_now_playing(
                guild_id
            )

            try:
                await vc.disconnect()

            except discord.HTTPException:
                pass

            await set_vc_status(
                channel_id,
                ""
            )

            # IMPORTANT:
            # Profile status remains server count.
            await update_bot_presence()

            return

        item = queue.pop(0)

        query = item["query"]
        requester = item["requester"]

    loop = asyncio.get_event_loop()

    try:

        data = await loop.run_in_executor(
            None,
            lambda: ytdl.extract_info(
                query,
                download=False
            )
        )

        if not data:
            raise RuntimeError(
                "No media result returned."
            )

        if data.get("entries"):

            entries = [
                e
                for e in data["entries"]
                if e
            ]

            if not entries:
                raise RuntimeError(
                    "No playable entries found."
                )

            data = entries[0]

        song_url = data.get("url")

        if not song_url:
            raise RuntimeError(
                "No playable audio URL found."
            )

        title = data.get(
            "title",
            "Unknown Track"
        )

        duration_sec = (
            data.get("duration")
            or 0
        )

        thumbnail = data.get(
            "thumbnail"
        )

        webpage_url = data.get(
            "webpage_url",
            ""
        )

        minutes, seconds = divmod(
            int(duration_sec),
            60
        )

        duration_str = (
            f"{minutes:02d}:{seconds:02d}"
        )

    except Exception as e:

        error_detail = (
            str(e)
            if str(e)
            else type(e).__name__
        )

        if send_func:
            await send_func(
                text=(
                    f"😵‍💫 Couldn't cast "
                    f"**{query}** — skipping.\n"
                    f"`{error_detail}`"
                ),
                view=add_support_button(),
            )

        error_embed = discord.Embed(
            title="⚠️ Music Extraction Error",
            color=discord.Color.red(),
        )

        error_embed.add_field(
            name="Server",
            value=(
                f"[{guild.name}]"
                f"({server_link(guild.id)})\n"
                f"`{guild.id}`"
            ),
            inline=False,
        )

        error_embed.add_field(
            name="Query",
            value=f"`{query}`",
            inline=False,
        )

        error_embed.add_field(
            name="Error",
            value=f"`{error_detail[:1000]}`",
            inline=False,
        )

        asyncio.create_task(
            send_log(
                "error",
                embed=error_embed
            )
        )

        await play_next(
            guild,
            channel,
            send_func
        )

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
            print(
                f"[PLAYER ERROR] {error}"
            )

        if (
            loop_modes.get(guild_id)
            == "queue"
        ):

            get_queue(
                guild_id
            ).append(
                {
                    "query": query,
                    "title": title,
                    "thumbnail": thumbnail,
                    "webpage_url": webpage_url,
                    "requester": requester,
                    "_queue_id":
                        f"{time.time_ns()}-loop",
                }
            )

        fut = asyncio.run_coroutine_threadsafe(
            play_next(
                guild,
                channel
            ),
            bot.loop
        )

        try:
            fut.result()

        except Exception as e:
            print(
                f"[AFTER CALLBACK ERROR] {e}"
            )

    try:

        source = discord.FFmpegPCMAudio(
            song_url,
            **FFMPEG_OPTIONS
        )

        vc.play(
            source,
            after=after_play
        )

    except discord.ClientException as e:

        error_detail = (
            str(e)
            if str(e)
            else type(e).__name__
        )

        if send_func:

            if "ffmpeg" in error_detail.lower():

                await send_func(
                    text=(
                        "❌ Audio engine (FFmpeg) "
                        "is not available on the host."
                    ),
                    view=add_support_button(),
                )

            else:

                await send_func(
                    text=(
                        "❌ Playback error.\n"
                        f"`ClientException: "
                        f"{error_detail}`"
                    ),
                    view=add_support_button(),
                )

        error_embed = discord.Embed(
            title="🚨 Playback Error",
            color=discord.Color.red(),
        )

        error_embed.add_field(
            name="Server",
            value=(
                f"[{guild.name}]"
                f"({server_link(guild.id)})\n"
                f"`{guild.id}`"
            ),
            inline=False,
        )

        error_embed.add_field(
            name="Song",
            value=f"`{title}`",
            inline=False,
        )

        error_embed.add_field(
            name="Error",
            value=f"`{error_detail[:1000]}`",
            inline=False,
        )

        asyncio.create_task(
            send_log(
                "error",
                embed=error_embed
            )
        )

        return

    except Exception as e:

        error_detail = (
            str(e)
            if str(e)
            else type(e).__name__
        )

        if send_func:
            await send_func(
                text=(
                    "❌ Playback error.\n"
                    f"`{error_detail}`"
                ),
                view=add_support_button(),
            )

        error_embed = discord.Embed(
            title="🚨 Playback Error",
            color=discord.Color.red(),
        )

        error_embed.add_field(
            name="Server",
            value=(
                f"[{guild.name}]"
                f"({server_link(guild.id)})\n"
                f"`{guild.id}`"
            ),
            inline=False,
        )

        error_embed.add_field(
            name="Song",
            value=f"`{title}`",
            inline=False,
        )

        error_embed.add_field(
            name="Error",
            value=f"`{error_detail[:1000]}`",
            inline=False,
        )

        asyncio.create_task(
            send_log(
                "error",
                embed=error_embed
            )
        )

        return

    old_msg = now_playing_messages.get(
        guild_id
    )

    if old_msg:

        try:
            await old_msg.delete()

        except (
            discord.NotFound,
            discord.HTTPException,
        ):
            pass

    # KEEP VOICE CHANNEL SONG STATUS.
    await set_vc_status(
        vc.channel.id,
        f"🎶 {title}"[:100]
    )

    # IMPORTANT:
    # Do NOT set profile status to song.
    # It remains "✦ Casting X servers".
    await update_bot_presence()

    # ========================================================
    # MUSIC LOG
    # ========================================================

    log_embed = discord.Embed(
        title="🎶 Music Played",
        description=(
            f"**[{title}]"
            f"({webpage_url})**"
            if webpage_url
            else f"**{title}**"
        ),
        color=PURPLE,
    )

    log_embed.add_field(
        name="Server",
        value=(
            f"[{guild.name}]"
            f"({server_link(guild.id)})\n"
            f"`{guild.id}`"
        ),
        inline=True,
    )

    log_embed.add_field(
        name="Voice Channel",
        value=(
            f"[{vc.channel.name}]"
            f"({channel_link(guild.id, vc.channel.id)})\n"
            f"`{vc.channel.id}`"
        )
        if vc.channel
        else "Unknown",
        inline=True,
    )

    if requester:
        log_embed.add_field(
            name="Requested By",
            value=(
                f"{requester.mention}\n"
                f"`{requester.id}`"
            ),
            inline=True,
        )

    log_embed.add_field(
        name="Duration",
        value=f"`{duration_str}`",
        inline=True,
    )

    if thumbnail:
        log_embed.set_thumbnail(
            url=thumbnail
        )

    asyncio.create_task(
        send_log(
            "music",
            embed=log_embed
        )
    )

    msg = await channel.send(
        embed=build_now_playing_embed(
            guild_id,
            paused=False
        ),
        view=MusicView(
            guild_id
        )
    )

    now_playing_messages[
        guild_id
    ] = msg

    start_now_playing_refresh(
        guild_id
    )


# ============================================================
# ENQUEUE
# ============================================================

async def enqueue_song(
    guild,
    channel,
    user,
    query,
    send_func
):
    vc, err = await ensure_voice(
        user,
        guild
    )

    if err:
        return await send_func(
            text=err,
            view=add_support_button()
        )

    try:
        items = await resolve_query_items(
            query,
            user
        )

    except Exception as e:
        return await send_func(
            text=(
                f"😵‍💫 Couldn't add "
                f"**{query}** to the queue.\n"
                f"`{e}`"
            ),
            view=add_support_button(),
        )

    was_playing = (
        vc.is_playing()
        or vc.is_paused()
    )

    for item in items:

        item.setdefault(
            "query",
            query
        )

        item.setdefault(
            "title",
            item.get(
                "query",
                "Unknown Track"
            )
        )

        item.setdefault(
            "requester",
            user
        )

        item["_queue_id"] = (
            f"{time.time_ns()}-"
            f"{random.randint(1000, 9999)}"
        )

        get_queue(
            guild.id
        ).append(item)

    if was_playing:

        for item in items[:10]:

            await send_queue_added_embed(
                guild,
                channel,
                user,
                item
            )

        if len(items) > 10:

            await send_func(
                text=(
                    f"✦ Added "
                    f"**{len(items)}** "
                    f"tracks to the queue."
                )
            )

        await update_now_playing_message(
            guild.id
        )

    else:

        await play_next(
            guild,
            channel,
            send_func
        )


# ============================================================
# SAFE SEND
# ============================================================

async def safe_send_channel(
    channel,
    text=None,
    embed=None,
    view=None
):
    kwargs = {}

    if text is not None:
        kwargs["content"] = text

    if embed is not None:
        kwargs["embed"] = embed

    if view is not None:
        kwargs["view"] = view

    return await channel.send(
        **kwargs
    )


async def safe_send_followup(
    interaction,
    text=None,
    embed=None,
    view=None
):
    kwargs = {}

    if text is not None:
        kwargs["content"] = text

    if embed is not None:
        kwargs["embed"] = embed

    if view is not None:
        kwargs["view"] = view

    return await interaction.followup.send(
        **kwargs
    )


# ============================================================
# EVENTS
# ============================================================

@bot.event
async def on_ready():
    load_config()

    try:
        synced = await bot.tree.sync()

        # PROFILE STATUS:
        # ONLY server count.
        await update_bot_presence()

        print(
            f"Logged in as "
            f"{bot.user.name} | "
            f"Synced {len(synced)} Slash Commands!"
        )

    except Exception as e:
        print(
            f"Error syncing commands: {e}"
        )


@bot.event
async def on_guild_join(guild):

    await update_bot_presence()

    embed = discord.Embed(
        title="✦ Joined a New Server",
        description=(
            f"**[{guild.name}]"
            f"({server_link(guild.id)})**"
        ),
        color=discord.Color.green(),
    )

    embed.add_field(
        name="Server ID",
        value=f"`{guild.id}`",
        inline=True,
    )

    embed.add_field(
        name="Members",
        value=str(
            guild.member_count
        ),
        inline=True,
    )

    if guild.owner:
        embed.add_field(
            name="Owner",
            value=(
                f"{guild.owner}\n"
                f"`{guild.owner.id}`"
            ),
            inline=True,
        )

    if guild.icon:
        embed.set_thumbnail(
            url=guild.icon.url
        )

    embed.add_field(
        name="Server Link",
        value=(
            f"[Open Server]"
            f"({server_link(guild.id)})"
        ),
        inline=False,
    )

    embed.set_footer(
        text=(
            f"Now in {len(bot.guilds)} servers"
        )
    )

    await send_log(
        "join",
        embed=embed
    )


@bot.event
async def on_guild_remove(guild):

    await update_bot_presence()

    embed = discord.Embed(
        title="✦ Removed From a Server",
        description=(
            f"**[{guild.name}]"
            f"({server_link(guild.id)})**"
        ),
        color=discord.Color.red(),
    )

    embed.add_field(
        name="Server ID",
        value=f"`{guild.id}`",
        inline=True,
    )

    embed.add_field(
        name="Server Link",
        value=(
            f"[Open Server]"
            f"({server_link(guild.id)})"
        ),
        inline=True,
    )

    embed.set_footer(
        text=(
            f"Now in {len(bot.guilds)} servers"
        )
    )

    await send_log(
        "join",
        embed=embed
    )


# ============================================================
# MESSAGE COMMANDS
# ============================================================

@bot.event
async def on_message(
    message: discord.Message
):
    if (
        message.author.bot
        or not message.guild
    ):
        return

    content = message.content.strip()
    lowered = content.lower()

    prefix = get_prefix(
        message.guild.id
    )

    command_text = None

    if bot.user in message.mentions:

        command_text = (
            content
            .replace(
                f"<@{bot.user.id}>",
                ""
            )
            .replace(
                f"<@!{bot.user.id}>",
                ""
            )
            .strip()
        )

    elif (
        prefix
        and lowered.startswith(
            prefix.lower()
        )
    ):

        command_text = (
            content[len(prefix):]
            .strip()
        )

    if command_text is not None:

        parts = command_text.split(
            maxsplit=1
        )

        cmd = (
            parts[0].lower()
            if parts
            else ""
        )

        arg = (
            parts[1].strip()
            if len(parts) > 1
            else ""
        )

        if cmd in (
            "p",
            "play"
        ):

            if not arg:
                await message.channel.send(
                    "❌ Give me a song name, "
                    "YouTube URL, Spotify URL, "
                    "or playlist URL."
                )
                return

            try:

                async with message.channel.typing():

                    await enqueue_song(
                        guild=message.guild,
                        channel=message.channel,
                        user=message.author,
                        query=arg,
                        send_func=lambda
                            embed=None,
                            view=None,
                            text=None:
                            safe_send_channel(
                                message.channel,
                                text=text,
                                embed=embed,
                                view=view,
                            ),
                    )

            except Exception as e:

                print(
                    f"[MESSAGE PLAY ERROR] {e}"
                )

                await message.channel.send(
                    f"❌ Something went wrong: "
                    f"`{e}`",
                    view=add_support_button(),
                )

            return

        if cmd == "shuffle":

            queue = get_queue(
                message.guild.id
            )

            if len(queue) < 2:

                await message.channel.send(
                    "❌ Need at least 2 songs "
                    "in the queue to shuffle."
                )

            else:

                random.shuffle(queue)

                await message.channel.send(
                    f"🔀 Queue shuffled by "
                    f"{message.author.mention}."
                )

                await update_now_playing_message(
                    message.guild.id
                )

            return

        if cmd == "loop":

            guild_id = message.guild.id

            chosen = (
                arg.lower()
                if arg.lower()
                in (
                    "track",
                    "queue",
                    "off"
                )
                else None
            )

            loop_modes[
                guild_id
            ] = (
                None
                if chosen == "off"
                else (
                    chosen
                    or next_loop_mode(
                        loop_modes.get(
                            guild_id
                        )
                    )
                )
            )

            await message.channel.send(
                f"🔁 Loop mode: "
                f"**{get_loop_label(guild_id)}** "
                f"— set by "
                f"{message.author.mention}"
            )

            await update_now_playing_message(
                guild_id
            )

            return

        if cmd == "skip":

            vc = (
                message.guild.voice_client
            )

            if vc and (
                vc.is_playing()
                or vc.is_paused()
            ):

                vc.stop()

                await message.channel.send(
                    f"⏭️ Skipped by "
                    f"{message.author.mention}"
                )

            else:

                await message.channel.send(
                    "❌ Nothing is playing."
                )

            return

        if cmd == "pause":

            vc = (
                message.guild.voice_client
            )

            if vc and vc.is_playing():

                vc.pause()

                mark_paused(
                    message.guild.id
                )

                await update_now_playing_message(
                    message.guild.id
                )

                await message.channel.send(
                    f"⏸️ Paused by "
                    f"{message.author.mention}"
                )

            else:

                await message.channel.send(
                    "❌ Nothing is playing."
                )

            return

        if cmd == "resume":

            vc = (
                message.guild.voice_client
            )

            if vc and vc.is_paused():

                vc.resume()

                mark_resumed(
                    message.guild.id
                )

                await update_now_playing_message(
                    message.guild.id
                )

                await message.channel.send(
                    f"▶️ Resumed by "
                    f"{message.author.mention}"
                )

            else:

                await message.channel.send(
                    "❌ Nothing is paused."
                )

            return

        if cmd == "stop":

            await stop_playback(
                message.guild
            )

            await message.channel.send(
                f"⏹️ Stopped by "
                f"{message.author.mention}"
            )

            return

    await bot.process_commands(
        message
    )


# ============================================================
# SLASH COMMANDS
# ============================================================

@bot.tree.command(
    name="play",
    description="Play or queue a song in your voice channel"
)
async def play(
    interaction: discord.Interaction,
    query: str
):
    await interaction.response.defer()

    await enqueue_song(
        guild=interaction.guild,
        channel=interaction.channel,
        user=interaction.user,
        query=query,
        send_func=lambda
            embed=None,
            view=None,
            text=None:
            safe_send_followup(
                interaction,
                text=text,
                embed=embed,
                view=view,
            ),
    )


# ============================================================
# CONFIG PREFIX
# ============================================================

@bot.tree.command(
    name="config",
    description="Set the quick-play text prefix for this server"
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def config_command(
    interaction: discord.Interaction,
    prefix: str
):
    prefix = prefix.strip()

    if (
        not prefix
        or len(prefix) > 5
        or " " in prefix
    ):

        await interaction.response.send_message(
            "❌ Prefix must be 1–5 characters with no spaces.",
            ephemeral=True,
        )

        return

    guild_prefixes[
        interaction.guild.id
    ] = prefix

    save_config()

    await interaction.response.send_message(
        f"✅ Quick-play prefix set to "
        f"**`{prefix}`**\n"
        f"Now these work:\n"
        f"`{prefix}p <song>`\n"
        f"`{prefix}play <song>`",
        ephemeral=True,
    )


@config_command.error
async def config_command_error(
    interaction,
    error
):
    if isinstance(
        error,
        app_commands.errors.MissingPermissions
    ):

        await interaction.response.send_message(
            "❌ Only server administrators "
            "can use this command.",
            ephemeral=True,
        )

    else:

        print(
            f"[CONFIG ERROR] {error}"
        )


# ============================================================
# LOG CHANNEL COMMANDS
# ============================================================

@bot.tree.command(
    name="setmusiclogs",
    description="Set the music logs channel (Bot owner only)"
)
async def setmusiclogs(
    interaction: discord.Interaction,
    channel: discord.TextChannel
):
    if not is_bot_owner(
        interaction.user.id
    ):

        await interaction.response.send_message(
            "❌ Only the bot owner can use this command.",
            ephemeral=True,
        )

        return

    LOG_CHANNELS["music"] = channel.id

    await interaction.response.send_message(
        f"✅ **Music logs channel set!**\n"
        f"📃 Channel: {channel.mention}\n"
        f"🔗 [Open Channel]"
        f"({channel_link(interaction.guild.id, channel.id)})",
        ephemeral=True,
    )


@bot.tree.command(
    name="setjoinlogs",
    description="Set the join/leave logs channel (Bot owner only)"
)
async def setjoinlogs(
    interaction: discord.Interaction,
    channel: discord.TextChannel
):
    if not is_bot_owner(
        interaction.user.id
    ):

        await interaction.response.send_message(
            "❌ Only the bot owner can use this command.",
            ephemeral=True,
        )

        return

    LOG_CHANNELS["join"] = channel.id

    await interaction.response.send_message(
        f"✅ **Join/Leave logs channel set!**\n"
        f"📃 Channel: {channel.mention}\n"
        f"🔗 [Open Channel]"
        f"({channel_link(interaction.guild.id, channel.id)})",
        ephemeral=True,
    )


@bot.tree.command(
    name="seterrorlogs",
    description="Set the error logs channel (Bot owner only)"
)
async def seterrorlogs(
    interaction: discord.Interaction,
    channel: discord.TextChannel
):
    if not is_bot_owner(
        interaction.user.id
    ):

        await interaction.response.send_message(
            "❌ Only the bot owner can use this command.",
            ephemeral=True,
        )

        return

    LOG_CHANNELS["error"] = channel.id

    await interaction.response.send_message(
        f"✅ **Error logs channel set!**\n"
        f"📃 Channel: {channel.mention}\n"
        f"🔗 [Open Channel]"
        f"({channel_link(interaction.guild.id, channel.id)})",
        ephemeral=True,
    )


# ============================================================
# LOOP
# ============================================================

@bot.tree.command(
    name="loop",
    description="Set loop mode: off, track, or queue"
)
@app_commands.choices(
    mode=[
        app_commands.Choice(
            name="Off",
            value="off"
        ),
        app_commands.Choice(
            name="Track",
            value="track"
        ),
        app_commands.Choice(
            name="Queue",
            value="queue"
        ),
    ]
)
async def loop_command(
    interaction: discord.Interaction,
    mode: app_commands.Choice[str]
):
    guild_id = interaction.guild.id

    loop_modes[guild_id] = (
        None
        if mode.value == "off"
        else mode.value
    )

    await interaction.response.send_message(
        f"🔁 Loop mode set to "
        f"**{mode.name}** by "
        f"{interaction.user.mention}"
    )

    await update_now_playing_message(
        guild_id
    )


# ============================================================
# SHUFFLE
# ============================================================

@bot.tree.command(
    name="shuffle",
    description="Shuffle the current queue"
)
async def shuffle_command(
    interaction: discord.Interaction
):
    guild_id = interaction.guild.id

    queue = get_queue(
        guild_id
    )

    if len(queue) < 2:

        await interaction.response.send_message(
            "❌ Need at least 2 songs "
            "in the queue to shuffle.",
            ephemeral=True,
        )

        return

    random.shuffle(queue)

    await interaction.response.send_message(
        f"🔀 Queue shuffled by "
        f"{interaction.user.mention} — "
        f"**{len(queue)}** song(s) reordered"
    )

    await update_now_playing_message(
        guild_id
    )


# ============================================================
# PROFILE PICTURE
# ============================================================

@bot.tree.command(
    name="setpfp",
    description="Change the bot's profile picture for this server"
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def setpfp(
    interaction: discord.Interaction,
    image: discord.Attachment
):
    await interaction.response.defer(
        ephemeral=True
    )

    try:

        image_bytes = await image.read()

        await interaction.guild.me.edit(
            avatar=image_bytes
        )

        await interaction.followup.send(
            "✅ Profile picture updated!"
        )

    except Exception as e:

        await interaction.followup.send(
            f"❌ Failed to update avatar: "
            f"`{str(e)}`",
            view=add_support_button(),
        )


@bot.tree.command(
    name="resetpfp",
    description="Reset the bot's avatar for this server"
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def resetpfp(
    interaction: discord.Interaction
):
    await interaction.response.defer(
        ephemeral=True
    )

    try:

        await interaction.guild.me.edit(
            avatar=None
        )

        await interaction.followup.send(
            "✅ Avatar reset to default."
        )

    except Exception as e:

        await interaction.followup.send(
            f"❌ Failed to reset avatar: "
            f"`{str(e)}`",
            view=add_support_button(),
        )


# ============================================================
# BANNER
# ============================================================

@bot.tree.command(
    name="setbanner",
    description="Change the bot profile banner"
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def setbanner(
    interaction: discord.Interaction,
    image: discord.Attachment
):
    await interaction.response.defer(
        ephemeral=True
    )

    try:

        image_bytes = await image.read()

        await bot.user.edit(
            banner=image_bytes
        )

        await interaction.followup.send(
            "✅ Banner updated! "
            "Discord banners are global."
        )

    except Exception as e:

        await interaction.followup.send(
            f"❌ Failed to update banner: "
            f"`{str(e)}`",
            view=add_support_button(),
        )


# ============================================================
# 24/7
# ============================================================

@bot.tree.command(
    name="247",
    description="Toggle 24/7 mode so the bot stays in VC"
)
async def mode_247(
    interaction: discord.Interaction
):
    await interaction.response.send_message(
        "🔒 **24/7 Mode Status:** Active 🟢"
    )


# ============================================================
# RECOMMEND
# ============================================================

@bot.tree.command(
    name="recommend",
    description="Get music recommendations"
)
async def recommend(
    interaction: discord.Interaction,
    genre: str = "Trending Hits"
):
    embed = discord.Embed(
        title="✨ AI Recommended Tracks",
        description=(
            f"Recommendations for "
            f"**{genre}**:\n\n"
            f"1️⃣ **Arijit Singh - Casual Lo-fi Vibe**\n"
            f"2️⃣ **Midnight Chill Beats - Lofi Remix**\n"
            f"3️⃣ **Phonk / Cyberpunk Synthwave Special**"
        ),
        color=PURPLE,
    )

    await interaction.response.send_message(
        embed=embed,
        view=add_support_button()
    )


# ============================================================
# SKIP
# ============================================================

@bot.tree.command(
    name="skip",
    description="Skip current song"
)
async def skip(
    interaction: discord.Interaction
):
    vc = interaction.guild.voice_client

    if vc and (
        vc.is_playing()
        or vc.is_paused()
    ):

        vc.stop()

        await interaction.response.send_message(
            f"⏭️ Skipped by "
            f"{interaction.user.mention}"
        )

    else:

        await interaction.response.send_message(
            "❌ Nothing is playing.",
            ephemeral=True,
        )


# ============================================================
# PAUSE
# ============================================================

@bot.tree.command(
    name="pause",
    description="Pause the current song"
)
async def pause(
    interaction: discord.Interaction
):
    vc = interaction.guild.voice_client

    if not vc or not vc.is_playing():

        await interaction.response.send_message(
            "❌ Nothing is playing.",
            ephemeral=True,
        )

        return

    vc.pause()

    mark_paused(
        interaction.guild.id
    )

    await interaction.response.send_message(
        f"⏸️ Spell paused by "
        f"{interaction.user.mention}"
    )

    await update_now_playing_message(
        interaction.guild.id
    )


# ============================================================
# RESUME
# ============================================================

@bot.tree.command(
    name="resume",
    description="Resume the current song"
)
async def resume(
    interaction: discord.Interaction
):
    vc = interaction.guild.voice_client

    if not vc or not vc.is_paused():

        await interaction.response.send_message(
            "❌ Nothing is paused.",
            ephemeral=True,
        )

        return

    vc.resume()

    mark_resumed(
        interaction.guild.id
    )

    await interaction.response.send_message(
        f"▶️ Spell resumes... by "
        f"{interaction.user.mention}"
    )

    await update_now_playing_message(
        interaction.guild.id
    )


# ============================================================
# STOP
# ============================================================

@bot.tree.command(
    name="stop",
    description="Stop music, clear queue, and leave VC"
)
async def stop(
    interaction: discord.Interaction
):
    await interaction.response.defer()

    await stop_playback(
        interaction.guild
    )

    await interaction.followup.send(
        f"💨 The magic fades... "
        f"stopped by "
        f"{interaction.user.mention}"
    )


# ============================================================
# OWNER ONLY — SERVERS
# ============================================================

@bot.tree.command(
    name="servers",
    description="Bot owner only: list every server"
)
async def servers_command(
    interaction: discord.Interaction
):
    if not is_bot_owner(
        interaction.user.id
    ):

        await interaction.response.send_message(
            "❌ This command is restricted to the bot owner.",
            ephemeral=True,
        )

        return

    lines = []

    for g in bot.guilds:

        lines.append(
            f"**[{g.name}]"
            f"({server_link(g.id)})** — "
            f"`{g.id}` — "
            f"{g.member_count} members"
        )

    text = (
        "\n".join(lines)
        if lines
        else "Not in any servers."
    )

    if len(text) > 1900:
        text = (
            text[:1900]
            + "\n… (truncated)"
        )

    await interaction.response.send_message(
        text,
        ephemeral=True
    )


# ============================================================
# OWNER ONLY — GENERATE INVITE
# ============================================================

@bot.tree.command(
    name="geninvite",
    description="Bot owner only: generate a server invite"
)
async def geninvite(
    interaction: discord.Interaction,
    server_id: str
):
    if not is_bot_owner(
        interaction.user.id
    ):

        await interaction.response.send_message(
            "❌ This command is restricted to the bot owner.",
            ephemeral=True,
        )

        return

    try:
        target_id = int(
            server_id.strip()
        )

    except ValueError:

        await interaction.response.send_message(
            "❌ That's not a valid server ID.",
            ephemeral=True,
        )

        return

    target_guild = bot.get_guild(
        target_id
    )

    if not target_guild:

        await interaction.response.send_message(
            "❌ I'm not in that server. "
            "Use `/servers`.",
            ephemeral=True,
        )

        return

    invite_channel = None

    for ch in target_guild.text_channels:

        perms = ch.permissions_for(
            target_guild.me
        )

        if (
            perms.create_instant_invite
            and perms.view_channel
        ):

            invite_channel = ch
            break

    if not invite_channel:

        await interaction.response.send_message(
            f"❌ I don't have Create Invite "
            f"permission in **{target_guild.name}**.",
            ephemeral=True,
        )

        return

    try:

        invite = await invite_channel.create_invite(
            max_age=3600,
            max_uses=1,
            unique=True,
            reason=(
                f"Requested by bot owner "
                f"{interaction.user}"
            ),
        )

        await interaction.response.send_message(
            f"🔗 Invite for "
            f"**{target_guild.name}**:\n"
            f"{invite.url}\n"
            f"(expires in 1 hour, 1 use)",
            ephemeral=True,
        )

    except discord.Forbidden:

        await interaction.response.send_message(
            "❌ Missing permission to "
            "create an invite there.",
            ephemeral=True,
        )

    except Exception as e:

        await interaction.response.send_message(
            f"❌ Failed to create invite: "
            f"`{e}`",
            ephemeral=True,
        )


# ============================================================
# OWNER ONLY — RESET PFP ALL
# ============================================================

@bot.tree.command(
    name="resetpfpall",
    description="Bot owner only: reset avatar in every server"
)
async def resetpfpall(
    interaction: discord.Interaction
):
    if not is_bot_owner(
        interaction.user.id
    ):

        await interaction.response.send_message(
            "❌ This command is restricted to the bot owner.",
            ephemeral=True,
        )

        return

    await interaction.response.defer(
        ephemeral=True
    )

    success = 0
    failed = []

    for g in bot.guilds:

        try:

            await g.me.edit(
                avatar=None
            )

            success += 1

        except Exception as e:

            failed.append(
                f"{g.name} (`{g.id}`): {e}"
            )

        await asyncio.sleep(1)

    msg = (
        f"✅ Reset avatar in "
        f"**{success}/{len(bot.guilds)}** "
        f"server(s)."
    )

    if failed:

        shown = "\n".join(
            failed[:10]
        )

        msg += (
            f"\n\n❌ Failed in:\n"
            f"{shown}"
        )

        if len(failed) > 10:
            msg += (
                f"\n… and "
                f"{len(failed) - 10} more"
            )

    await interaction.followup.send(
        msg
    )


# ============================================================
# START BOT
# ============================================================

token = os.getenv(
    "DISCORD_TOKEN"
)

if token:

    bot.run(token)

else:

    raise RuntimeError(
        "DISCORD_TOKEN environment variable is missing!"
    )
import asyncio
import datetime
import logging
import os
import random
import traceback
from dataclasses import dataclass
from typing import Optional

import aiohttp
import discord
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from discord.ext import commands
from discord.ui import Button, View
import yt_dlp


# ==================== CONFIG ====================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")

OWNER_ID = int(os.getenv("OWNER_ID", "0"))
MAIN_GUILD_ID = int(os.getenv("MAIN_GUILD_ID", "0"))
OWNER_LOG_CHANNEL_ID = int(os.getenv("OWNER_LOG_CHANNEL_ID", "0"))

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv(
    "OPENROUTER_MODEL",
    "google/gemini-2.5-flash",
)

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")

DEFAULT_PREFIX = "!"
FFMPEG_EXECUTABLE = os.getenv("FFMPEG_EXECUTABLE", "ffmpeg")

BOT_NAME = "Fryplex"

EMBED_COLOR = 0x9B59B6
SUCCESS_COLOR = 0x2ECC71
ERROR_COLOR = 0xE74C3C
WARNING_COLOR = 0xF1C40F
INFO_COLOR = 0x3498DB


# ==================== BOT SETUP ====================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(BOT_NAME)

intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True
intents.message_content = True

bot = commands.Bot(
    command_prefix=commands.when_mentioned_or(DEFAULT_PREFIX),
    intents=intents,
    case_insensitive=True,
    help_command=None,
)

spotify_client: Optional[spotipy.Spotify] = None
if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
    spotify_client = spotipy.Spotify(
        auth_manager=SpotifyClientCredentials(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
        )
    )


# ==================== YT-DLP / FFMPEG ====================

YTDLP_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "scsearch",
    "source_address": "0.0.0.0",
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}


# ==================== MUSIC CLASSES ====================

@dataclass
class Song:
    title: str
    webpage_url: str
    duration: int
    thumbnail: str
    requester: discord.Member
    artist: str = "Unknown Artist"
    search_query: str = ""
    stream_url: Optional[str] = None

    @property
    def duration_text(self) -> str:
        if not self.duration:
            return "Live / Unknown"

        minutes, seconds = divmod(int(self.duration), 60)
        hours, minutes = divmod(minutes, 60)

        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        return f"{minutes:02d}:{seconds:02d}"


class MusicQueue:
    def __init__(self) -> None:
        self.songs: list[Song] = []
        self.current_song: Optional[Song] = None
        self.history: list[Song] = []
        self.volume = 0.5
        self.loop = False
        self.stay_247 = False
        self.text_channel_id: Optional[int] = None

    def reset(self) -> None:
        self.songs.clear()
        self.current_song = None


guild_queues: dict[int, MusicQueue] = {}


# ==================== HELPERS ====================

def get_queue(guild_id: int) -> MusicQueue:
    if guild_id not in guild_queues:
        guild_queues[guild_id] = MusicQueue()

    return guild_queues[guild_id]


def safe_text(value: object, limit: int = 256) -> str:
    text = str(value or "Unknown")

    if len(text) <= limit:
        return text

    return f"{text[:limit - 1]}…"


def create_embed(
    title: str,
    description: str,
    color: int = EMBED_COLOR,
    thumbnail: Optional[str] = None,
    fields: Optional[list[tuple[str, str, bool]]] = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=safe_text(title, 256),
        description=safe_text(description, 4096),
        color=color,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )

    if bot.user:
        embed.set_footer(
            text=f"🎵 {BOT_NAME}",
            icon_url=bot.user.display_avatar.url,
        )
    else:
        embed.set_footer(text=f"🎵 {BOT_NAME}")

    if thumbnail:
        embed.set_thumbnail(url=thumbnail)

    if fields:
        for name, value, inline in fields:
            embed.add_field(
                name=safe_text(name, 256),
                value=safe_text(value, 1024),
                inline=inline,
            )

    return embed


async def send_response(
    ctx: commands.Context,
    **kwargs,
) -> Optional[discord.Message]:
    if ctx.interaction:
        if not ctx.interaction.response.is_done():
            await ctx.interaction.response.send_message(**kwargs)
            return await ctx.interaction.original_response()

        return await ctx.interaction.followup.send(**kwargs, wait=True)

    return await ctx.send(**kwargs)


async def defer_if_interaction(ctx: commands.Context) -> None:
    if ctx.interaction and not ctx.interaction.response.is_done():
        await ctx.defer()


async def send_owner_log(
    title: str,
    description: str,
    color: int = EMBED_COLOR,
) -> None:
    if not MAIN_GUILD_ID or not OWNER_LOG_CHANNEL_ID:
        return

    main_guild = bot.get_guild(MAIN_GUILD_ID)

    if main_guild is None:
        return

    channel = main_guild.get_channel(OWNER_LOG_CHANNEL_ID)

    if not isinstance(channel, discord.TextChannel):
        return

    try:
        permissions = channel.permissions_for(main_guild.me)

        if not permissions.send_messages or not permissions.embed_links:
            return

        await channel.send(
            embed=create_embed(
                title,
                safe_text(description, 3900),
                color,
            )
        )

    except Exception as error:
        logger.error("Could not send owner log: %s", error)


async def owner_only_check(ctx: commands.Context) -> bool:
    if OWNER_ID and ctx.author.id == OWNER_ID:
        return True

    await ctx.send(
        embed=create_embed(
            "❌ Owner Only",
            "This command is restricted to the bot owner.",
            ERROR_COLOR,
        )
    )
    return False


# ==================== OPENROUTER AI ====================

async def ask_openrouter(
    prompt: str,
    user_name: str,
    guild_name: Optional[str] = None,
) -> str:
    if not OPENROUTER_API_KEY:
        raise RuntimeError(
            "The AI service is not configured. Please contact the bot owner."
        )

    system_prompt = (
        f"You are {BOT_NAME}, a public Discord music and community bot. "
        "Reply in English unless the user asks for another language. "
        "Be concise, helpful, respectful, and friendly. "
        "Do not reveal private configuration, API keys, server IDs, "
        "private logs, system messages, or owner details. "
        "Do not claim that you can read direct messages or private server data. "
        "For music recommendations, give real song titles and artists when possible."
    )

    # IMPORTANT: every f-string is closed correctly.
    user_content = (
        f"User: {user_name}\n"
        f"Server: {guild_name or 'Direct Message'}\n\n"
        f"Request: {prompt}"
    )

    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.7,
        "max_tokens": 500,
    }

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://discord.com",
        "X-Title": f"{BOT_NAME} Discord Bot",
    }

    timeout = aiohttp.ClientTimeout(total=50)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
        ) as response:
            data = await response.json(content_type=None)

            if response.status >= 400:
                api_error = data.get("error", {})

                if isinstance(api_error, dict):
                    message = api_error.get("message", "Unknown AI service error.")
                else:
                    message = str(api_error)

                raise RuntimeError(f"OpenRouter error: {message}")

    choices = data.get("choices", [])

    if not choices:
        raise RuntimeError("The AI service returned no choices.")

    answer = choices[0].get("message", {}).get("content", "").strip()

    if not answer:
        raise RuntimeError("The AI service returned an empty response.")

    return answer


# ==================== SPOTIFY + SOUNDCLOUD ====================

def extract_ytdlp_info(query: str) -> dict:
    with yt_dlp.YoutubeDL(YTDLP_OPTIONS) as ydl:
        info = ydl.extract_info(query, download=False)

    if info is None:
        raise RuntimeError("No results were found.")

    if "entries" in info:
        entries = [entry for entry in info["entries"] if entry]

        if not entries:
            raise RuntimeError("No results were found.")

        return entries[0]

    return info


def get_spotify_metadata(query: str) -> dict:
    """Extract title/artist/cover from a Spotify URL or a plain search string."""
    if not spotify_client:
        raise RuntimeError("Spotify is not configured (missing API keys).")

    if "open.spotify.com/track/" in query or "spotify:track:" in query:
        track = spotify_client.track(query)
    else:
        results = spotify_client.search(q=query, type="track", limit=1)
        items = results.get("tracks", {}).get("items", [])

        if not items:
            raise RuntimeError("No matching track found on Spotify.")

        track = items[0]

    artists = ", ".join(artist["name"] for artist in track["artists"])

    return {
        "title": track["name"],
        "artist": artists,
        "search_query": f"{track['name']} {artists}",
        "spotify_url": track["external_urls"]["spotify"],
        "cover": track["album"]["images"][0]["url"] if track["album"]["images"] else "",
        "duration_ms": track["duration_ms"],
    }


async def get_song_from_spotify(query: str, requester: discord.Member) -> Song:
    loop = asyncio.get_running_loop()

    meta = await loop.run_in_executor(None, get_spotify_metadata, query)

    search_query = f"scsearch1:{meta['search_query']}"
    info = await loop.run_in_executor(None, extract_ytdlp_info, search_query)

    return Song(
        title=safe_text(meta["title"], 200),
        webpage_url=meta["spotify_url"],
        duration=int(meta["duration_ms"] / 1000),
        thumbnail=meta["cover"] or info.get("thumbnail", ""),
        requester=requester,
        artist=safe_text(meta["artist"], 150),
        search_query=meta["search_query"],
        stream_url=info.get("url"),
    )


async def refresh_song_stream(song: Song) -> Song:
    """SoundCloud stream URLs expire — re-resolve using the saved search query."""
    loop = asyncio.get_running_loop()

    query = song.search_query or song.title
    info = await loop.run_in_executor(
        None,
        extract_ytdlp_info,
        f"scsearch1:{query}",
    )

    song.stream_url = info.get("url")

    if not song.thumbnail and info.get("thumbnail"):
        song.thumbnail = info["thumbnail"]

    return song


# ==================== VOICE CHANNEL STATUS ====================

async def set_voice_channel_status(channel: discord.VoiceChannel, status: str) -> None:
    """Sets the small status line shown under a voice channel's name."""
    try:
        await bot.http.request(
            discord.http.Route(
                "PUT",
                "/channels/{channel_id}/voice-status",
                channel_id=channel.id,
            ),
            json={"status": safe_text(status, 490)},
        )
    except discord.HTTPException as error:
        logger.error("Could not set VC status: %s", error)
    except Exception as error:
        logger.error("Unexpected VC status error: %s", error)


async def clear_voice_channel_status(channel: discord.VoiceChannel) -> None:
    await set_voice_channel_status(channel, "")


# ==================== VOICE PLAYBACK ====================

async def connect_to_voice(ctx: commands.Context) -> Optional[discord.VoiceClient]:
    if ctx.guild is None:
        return None

    if not ctx.author.voice or not ctx.author.voice.channel:
        await send_response(
            ctx,
            embed=create_embed(
                "❌ Voice Channel Required",
                "You need to join a voice channel before using this command.",
                ERROR_COLOR,
            ),
        )
        return None

    target_channel = ctx.author.voice.channel
    voice_client = ctx.guild.voice_client

    if voice_client and voice_client.is_connected():
        if voice_client.channel != target_channel:
            await voice_client.move_to(target_channel)

        return voice_client

    return await target_channel.connect()


async def announce_now_playing(guild: discord.Guild, song: Song) -> None:
    queue = get_queue(guild.id)
    voice_client = guild.voice_client

    if voice_client and isinstance(voice_client.channel, discord.VoiceChannel):
        status_text = (
            f"**{song.title}** by {song.artist} "
            f"• duration: {song.duration_text}"
        )
        await set_voice_channel_status(voice_client.channel, status_text)

    if not queue.text_channel_id:
        return

    channel = guild.get_channel(queue.text_channel_id)

    if not isinstance(channel, discord.TextChannel):
        return

    embed = create_embed(
        "🎵 Now Playing",
        f"**[{safe_text(song.title, 150)}]({song.webpage_url})** by {song.artist}",
        EMBED_COLOR,
        thumbnail=song.thumbnail,
        fields=[
            ("Duration", song.duration_text, True),
            ("Requested by", song.requester.mention, True),
            ("Loop", "Enabled" if queue.loop else "Disabled", True),
        ],
    )

    await channel.send(embed=embed, view=MusicControls())


async def play_next(guild: discord.Guild) -> None:
    queue = get_queue(guild.id)
    voice_client = guild.voice_client

    if voice_client is None or not voice_client.is_connected():
        return

    if queue.loop and queue.current_song:
        next_song = queue.current_song
    else:
        if queue.current_song:
            queue.history.insert(0, queue.current_song)
            queue.history = queue.history[:15]

        next_song = queue.songs.pop(0) if queue.songs else None

    if next_song is None:
        queue.current_song = None

        if isinstance(voice_client.channel, discord.VoiceChannel):
            await clear_voice_channel_status(voice_client.channel)

        if not queue.stay_247:
            await asyncio.sleep(25)

            current_voice = guild.voice_client

            if (
                current_voice
                and current_voice.is_connected()
                and not current_voice.is_playing()
                and not current_voice.is_paused()
                and not queue.songs
            ):
                await current_voice.disconnect()

        return

    try:
        next_song = await refresh_song_stream(next_song)

        if not next_song.stream_url:
            raise RuntimeError("Could not retrieve an audio stream.")

        queue.current_song = next_song

        audio_source = discord.FFmpegPCMAudio(
            next_song.stream_url,
            executable=FFMPEG_EXECUTABLE,
            **FFMPEG_OPTIONS,
        )

        volume_source = discord.PCMVolumeTransformer(
            audio_source,
            volume=queue.volume,
        )

        def after_play(error: Optional[Exception]) -> None:
            if error:
                logger.error("Voice playback error: %s", error)

            future = asyncio.run_coroutine_threadsafe(
                play_next(guild),
                bot.loop,
            )

            try:
                future.result()
            except Exception as callback_error:
                logger.error("After-play error: %s", callback_error)

        voice_client.play(volume_source, after=after_play)

        await announce_now_playing(guild, next_song)

        await send_owner_log(
            "🎵 Track Started",
            (
                f"Server: {guild.name} ({guild.id})\n"
                f"Track: {next_song.title} by {next_song.artist}\n"
                f"Requested by: {next_song.requester} "
                f"({next_song.requester.id})\n"
                f"Duration: {next_song.duration_text}"
            ),
            INFO_COLOR,
        )

    except Exception as error:
        logger.exception("Could not play a track.")

        await send_owner_log(
            "❌ Playback Error",
            (
                f"Server: {guild.name} ({guild.id})\n"
                f"Track: {next_song.title}\n"
                f"Error: {safe_text(error, 1200)}"
            ),
            ERROR_COLOR,
        )

        if queue.text_channel_id:
            channel = guild.get_channel(queue.text_channel_id)

            if isinstance(channel, discord.TextChannel):
                await channel.send(
                    embed=create_embed(
                        "❌ Playback Error",
                        (
                            "I could not play this track. It may be unavailable, "
                            "restricted, or temporarily inaccessible."
                        ),
                        ERROR_COLOR,
                    )
                )

        await play_next(guild)


# ==================== MUSIC BUTTONS ====================

class MusicControls(View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This control can only be used in a server.",
                ephemeral=True,
            )
            return False

        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "You need to be in the same voice channel as the bot.",
                ephemeral=True,
            )
            return False

        voice_client = interaction.guild.voice_client

        if voice_client and voice_client.channel != interaction.user.voice.channel:
            await interaction.response.send_message(
                "You need to be in the same voice channel as the bot.",
                ephemeral=True,
            )
            return False

        return True

    @discord.ui.button(
        emoji="⏯️",
        label="Pause / Resume",
        style=discord.ButtonStyle.primary,
        custom_id="fryplex_music_pause_resume",
    )
    async def pause_resume_button(
        self,
        interaction: discord.Interaction,
        button: Button,
    ) -> None:
        voice_client = interaction.guild.voice_client

        if voice_client is None:
            await interaction.response.send_message(
                "There is no active voice connection.",
                ephemeral=True,
            )
            return

        if voice_client.is_paused():
            voice_client.resume()
            await interaction.response.send_message(
                "▶️ Playback resumed.",
                ephemeral=True,
            )
            return

        if voice_client.is_playing():
            voice_client.pause()
            await interaction.response.send_message(
                "⏸️ Playback paused.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "There is no active track.",
            ephemeral=True,
        )

    @discord.ui.button(
        emoji="⏭️",
        label="Skip",
        style=discord.ButtonStyle.secondary,
        custom_id="fryplex_music_skip",
    )
    async def skip_button(
        self,
        interaction: discord.Interaction,
        button: Button,
    ) -> None:
        voice_client = interaction.guild.voice_client

        if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
            voice_client.stop()
            await interaction.response.send_message(
                "⏭️ Track skipped.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "There is no active track to skip.",
            ephemeral=True,
        )

    @discord.ui.button(
        emoji="🔁",
        label="Loop",
        style=discord.ButtonStyle.secondary,
        custom_id="fryplex_music_loop",
    )
    async def loop_button(
        self,
        interaction: discord.Interaction,
        button: Button,
    ) -> None:
        queue = get_queue(interaction.guild.id)
        queue.loop = not queue.loop

        status = "enabled" if queue.loop else "disabled"
        await interaction.response.send_message(
            f"🔁 Loop mode is now {status}.",
            ephemeral=True,
        )

    @discord.ui.button(
        emoji="⏹️",
        label="Stop",
        style=discord.ButtonStyle.danger,
        custom_id="fryplex_music_stop",
    )
    async def stop_button(
        self,
        interaction: discord.Interaction,
        button: Button,
    ) -> None:
        queue = get_queue(interaction.guild.id)
        voice_client = interaction.guild.voice_client

        queue.reset()

        if voice_client and voice_client.is_connected():
            if isinstance(voice_client.channel, discord.VoiceChannel):
                await clear_voice_channel_status(voice_client.channel)

            voice_client.stop()
            await voice_client.disconnect()

        await interaction.response.send_message(
            "⏹️ Playback stopped and queue cleared.",
            ephemeral=True,
        )


# ==================== MUSIC COMMANDS ====================

class MusicCommands(commands.Cog):
    def __init__(self, bot_instance: commands.Bot) -> None:
        self.bot = bot_instance

    @commands.hybrid_command(name="play", description="Play a song from Spotify")
    async def play(self, ctx: commands.Context, *, query: str) -> None:
        await defer_if_interaction(ctx)

        if ctx.guild is None:
            await send_response(
                ctx,
                embed=create_embed(
                    "❌ Server Only",
                    "This command can only be used in a Discord server.",
                    ERROR_COLOR,
                ),
            )
            return

        voice_client = await connect_to_voice(ctx)

        if voice_client is None:
            return

        queue = get_queue(ctx.guild.id)
        queue.text_channel_id = ctx.channel.id

        try:
            song = await get_song_from_spotify(query, ctx.author)
            queue.songs.append(song)

            embed = create_embed(
                "✅ Added to Queue",
                f"**[{safe_text(song.title, 150)}]({song.webpage_url})** by {song.artist}",
                SUCCESS_COLOR,
                thumbnail=song.thumbnail,
                fields=[
                    ("Duration", song.duration_text, True),
                    ("Queue Position", f"#{len(queue.songs)}", True),
                    ("Requested by", ctx.author.mention, True),
                ],
            )

            await send_response(ctx, embed=embed, view=MusicControls())

            if not voice_client.is_playing() and not voice_client.is_paused():
                await play_next(ctx.guild)

        except Exception as error:
            logger.exception("Play command failed.")

            await send_response(
                ctx,
                embed=create_embed(
                    "❌ Unable to Add Track",
                    "I could not find or add that track. Please try a different song name or a valid Spotify URL.",
                    ERROR_COLOR,
                ),
            )

            await send_owner_log(
                "❌ Play Command Error",
                (
                    f"User: {ctx.author} ({ctx.author.id})\n"
                    f"Server: {ctx.guild.name} ({ctx.guild.id})\n"
                    f"Query: {safe_text(query, 500)}\n"
                    f"Error: {safe_text(error, 1200)}"
                ),
                ERROR_COLOR,
            )

    @commands.hybrid_command(name="pause", description="Pause the current music")
    async def pause(self, ctx: commands.Context) -> None:
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.pause()
            await send_response(
                ctx,
                embed=create_embed(
                    "⏸️ Playback Paused",
                    "The current track has been paused.",
                    WARNING_COLOR,
                ),
            )
            return

        await send_response(
            ctx,
            embed=create_embed(
                "❌ Cannot Pause",
                "There is no active track playing.",
                ERROR_COLOR,
            ),
        )

    @commands.hybrid_command(name="resume", description="Resume paused music")
    async def resume(self, ctx: commands.Context) -> None:
        if ctx.voice_client and ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            await send_response(
                ctx,
                embed=create_embed(
                    "▶️ Playback Resumed",
                    "The current track has resumed.",
                    SUCCESS_COLOR,
                ),
            )
            return

        await send_response(
            ctx,
            embed=create_embed(
                "❌ Cannot Resume",
                "There is no paused track.",
                ERROR_COLOR,
            ),
        )

    @commands.hybrid_command(name="skip", description="Skip the current track")
    async def skip(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            return

        voice_client = ctx.voice_client

        if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
            current_song = get_queue(ctx.guild.id).current_song
            voice_client.stop()

            description = (
                f"Skipped **{safe_text(current_song.title, 120)}**."
                if current_song
                else "The current track was skipped."
            )

            await send_response(
                ctx,
                embed=create_embed(
                    "⏭️ Track Skipped",
                    description,
                    EMBED_COLOR,
                ),
            )
            return

        await send_response(
            ctx,
            embed=create_embed(
                "❌ Nothing Playing",
                "There is no active track to skip.",
                ERROR_COLOR,
            ),
        )

    @commands.hybrid_command(
        name="stop",
        description="Stop music, clear the queue, and disconnect",
    )
    async def stop(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            return

        queue = get_queue(ctx.guild.id)
        queue.reset()

        if ctx.voice_client and ctx.voice_client.is_connected():
            if isinstance(ctx.voice_client.channel, discord.VoiceChannel):
                await clear_voice_channel_status(ctx.voice_client.channel)

            ctx.voice_client.stop()
            await ctx.voice_client.disconnect()

        await send_response(
            ctx,
            embed=create_embed(
                "⏹️ Playback Stopped",
                "Music stopped, queue cleared, and bot disconnected.",
                ERROR_COLOR,
            ),
        )

    @commands.hybrid_command(name="queue", description="Show the current music queue")
    async def queue(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            return

        queue = get_queue(ctx.guild.id)

        if queue.current_song is None and not queue.songs:
            await send_response(
                ctx,
                embed=create_embed(
                    "📭 Queue Empty",
                    "No songs are queued. Use `/play <song or Spotify URL>` to add music.",
                    EMBED_COLOR,
                ),
            )
            return

        queue_lines = []

        for index, song in enumerate(queue.songs[:10], start=1):
            queue_lines.append(
                f"`{index}.` **{safe_text(song.title, 40)}** by "
                f"{safe_text(song.artist, 30)} — `{song.duration_text}`"
            )

        if len(queue.songs) > 10:
            queue_lines.append(
                f"*…and {len(queue.songs) - 10} more track(s).*"
            )

        queue_text = "\n".join(queue_lines) or "*No upcoming songs.*"

        current_song = queue.current_song
        current_text = (
            f"▶️ **{safe_text(current_song.title, 70)}** by "
            f"{safe_text(current_song.artist, 40)} — `{current_song.duration_text}`"
            if current_song
            else "Nothing is currently playing."
        )

        await send_response(
            ctx,
            embed=create_embed(
                "📀 Music Queue",
                queue_text,
                EMBED_COLOR,
                fields=[
                    ("Now Playing", current_text, False),
                    ("Queued Tracks", str(len(queue.songs)), True),
                    ("Loop Mode", "Enabled" if queue.loop else "Disabled", True),
                ],
            ),
        )

    @commands.hybrid_command(
        name="nowplaying",
        description="Show the currently playing track",
    )
    async def nowplaying(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            return

        song = get_queue(ctx.guild.id).current_song

        if song is None:
            await send_response(
                ctx,
                embed=create_embed(
                    "❌ Nothing Playing",
                    "There is no track currently playing.",
                    ERROR_COLOR,
                ),
            )
            return

        await send_response(
            ctx,
            embed=create_embed(
                "🎵 Now Playing",
                f"**[{safe_text(song.title, 150)}]({song.webpage_url})** by {song.artist}",
                EMBED_COLOR,
                thumbnail=song.thumbnail,
                fields=[
                    ("Duration", song.duration_text, True),
                    ("Requested by", song.requester.mention, True),
                ],
            ),
            view=MusicControls(),
        )

    @commands.hybrid_command(name="clear", description="Clear all upcoming tracks")
    async def clear(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            return

        queue = get_queue(ctx.guild.id)
        count = len(queue.songs)
        queue.songs.clear()

        await send_response(
            ctx,
            embed=create_embed(
                "🧹 Queue Cleared",
                f"Removed {count} upcoming track(s).",
                SUCCESS_COLOR,
            ),
        )

    @commands.hybrid_command(
        name="volume",
        description="Set the volume from 0 to 100",
    )
    async def volume(self, ctx: commands.Context, level: int) -> None:
        if ctx.guild is None:
            return

        if level < 0 or level > 100:
            await send_response(
                ctx,
                embed=create_embed(
                    "❌ Invalid Volume",
                    "Volume must be between 0 and 100.",
                    ERROR_COLOR,
                ),
            )
            return

        queue = get_queue(ctx.guild.id)
        queue.volume = level / 100

        if (
            ctx.voice_client
            and isinstance(ctx.voice_client.source, discord.PCMVolumeTransformer)
        ):
            ctx.voice_client.source.volume = queue.volume

        await send_response(
            ctx,
            embed=create_embed(
                "🔊 Volume Changed",
                f"Volume set to {level}%.",
                EMBED_COLOR,
            ),
        )

    @commands.hybrid_command(
        name="loop",
        description="Toggle current-track loop mode",
    )
    async def loop(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            return

        queue = get_queue(ctx.guild.id)
        queue.loop = not queue.loop

        status = "enabled" if queue.loop else "disabled"

        await send_response(
            ctx,
            embed=create_embed(
                "🔁 Loop Mode",
                f"Current-track loop has been {status}.",
                EMBED_COLOR,
            ),
        )

    @commands.hybrid_command(
        name="shuffle",
        description="Shuffle all upcoming tracks",
    )
    async def shuffle(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            return

        queue = get_queue(ctx.guild.id)

        if len(queue.songs) < 2:
            await send_response(
                ctx,
                embed=create_embed(
                    "❌ Cannot Shuffle",
                    "At least two upcoming tracks are required.",
                    ERROR_COLOR,
                ),
            )
            return

        random.shuffle(queue.songs)

        await send_response(
            ctx,
            embed=create_embed(
                "🔀 Queue Shuffled",
                f"Shuffled {len(queue.songs)} upcoming track(s).",
                SUCCESS_COLOR,
            ),
        )

    @commands.hybrid_command(
        name="remove",
        description="Remove a song from the queue by position",
    )
    async def remove(self, ctx: commands.Context, position: int) -> None:
        if ctx.guild is None:
            return

        queue = get_queue(ctx.guild.id)

        if position < 1 or position > len(queue.songs):
            await send_response(
                ctx,
                embed=create_embed(
                    "❌ Invalid Position",
                    f"Choose a position between 1 and {len(queue.songs)}.",
                    ERROR_COLOR,
                ),
            )
            return

        song = queue.songs.pop(position - 1)

        await send_response(
            ctx,
            embed=create_embed(
                "🗑️ Track Removed",
                f"Removed **{safe_text(song.title, 120)}** from position #{position}.",
                SUCCESS_COLOR,
            ),
        )

    @commands.hybrid_command(
        name="history",
        description="Show recently played tracks",
    )
    async def history(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            return

        queue = get_queue(ctx.guild.id)

        if not queue.history:
            await send_response(
                ctx,
                embed=create_embed(
                    "📜 Playback History",
                    "No tracks have finished playing during this session.",
                    EMBED_COLOR,
                ),
            )
            return

        lines = []

        for index, song in enumerate(queue.history[:10], start=1):
            lines.append(
                f"`{index}.` **{safe_text(song.title, 50)}** by "
                f"{safe_text(song.artist, 30)} — `{song.duration_text}`"
            )

        await send_response(
            ctx,
            embed=create_embed(
                "📜 Recently Played",
                "\n".join(lines),
                EMBED_COLOR,
            ),
        )

    @commands.hybrid_command(
        name="mode_247",
        description="Toggle 24/7 voice-channel mode",
    )
    async def mode_247(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            return

        voice_client = await connect_to_voice(ctx)

        if voice_client is None:
            return

        queue = get_queue(ctx.guild.id)
        queue.stay_247 = not queue.stay_247

        status = "enabled" if queue.stay_247 else "disabled"

        await send_response(
            ctx,
            embed=create_embed(
                "♾️ 24/7 Mode",
                f"24/7 mode has been {status}.",
                SUCCESS_COLOR if queue.stay_247 else EMBED_COLOR,
            ),
        )

    @commands.hybrid_command(
        name="disconnect",
        description="Disconnect the bot from voice",
    )
    async def disconnect(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            return

        queue = get_queue(ctx.guild.id)
        queue.reset()

        if ctx.voice_client and ctx.voice_client.is_connected():
            if isinstance(ctx.voice_client.channel, discord.VoiceChannel):
                await clear_voice_channel_status(ctx.voice_client.channel)

            ctx.voice_client.stop()
            await ctx.voice_client.disconnect()

            await send_response(
                ctx,
                embed=create_embed(
                    "🔌 Disconnected",
                    "The bot disconnected from the voice channel.",
                    EMBED_COLOR,
                ),
            )
            return

        await send_response(
            ctx,
            embed=create_embed(
                "❌ Not Connected",
                "The bot is not connected to a voice channel.",
                ERROR_COLOR,
            ),
        )


# ==================== SETPFP COMMAND ====================

class SetPFP(commands.Cog):
    def __init__(self, bot_instance: commands.Bot) -> None:
        self.bot = bot_instance

    @commands.hybrid_command(
        name="setpfp",
        description="Change the bot's avatar for this server",
    )
    @commands.has_permissions(administrator=True)
    async def setpfp(
        self,
        ctx: commands.Context,
        file: discord.Attachment,
    ) -> None:
        if not file.content_type or not file.content_type.startswith("image/"):
            await send_response(
                ctx,
                embed=create_embed(
                    "❌ Invalid File",
                    "Please upload a valid image file (png/jpg/webp/gif).",
                    ERROR_COLOR,
                ),
            )
            return

        if file.size > 8 * 1024 * 1024:
            await send_response(
                ctx,
                embed=create_embed(
                    "❌ File Too Large",
                    "Please use an image under 8MB.",
                    ERROR_COLOR,
                ),
            )
            return

        await defer_if_interaction(ctx)

        try:
            import base64

            image_bytes = await file.read()

            await self.bot.http.request(
                discord.http.Route(
                    "PATCH",
                    "/guilds/{guild_id}/members/@me",
                    guild_id=ctx.guild.id,
                ),
                json={
                    "avatar": (
                        f"data:{file.content_type};base64,"
                        f"{base64.b64encode(image_bytes).decode()}"
                    )
                },
            )

            embed = create_embed(
                "✅ Server Avatar Updated",
                f"My avatar for **{ctx.guild.name}** has been changed.",
                SUCCESS_COLOR,
                thumbnail=file.url,
            )

            await send_response(ctx, embed=embed)

        except discord.HTTPException as error:
            await send_response(
                ctx,
                embed=create_embed(
                    "❌ Update Failed",
                    f"Failed to update avatar: `{safe_text(error, 300)}`",
                    ERROR_COLOR,
                ),
            )

    @setpfp.error
    async def setpfp_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.MissingPermissions):
            await send_response(
                ctx,
                embed=create_embed(
                    "❌ Missing Permission",
                    "You need **Administrator** permission to use this command.",
                    ERROR_COLOR,
                ),
            )


# ==================== OWNER COMMANDS ====================

class OwnerCommands(commands.Cog):
    def __init__(self, bot_instance: commands.Bot) -> None:
        self.bot = bot_instance

    @commands.command(name="servers")
    async def servers(self, ctx: commands.Context) -> None:
        if not await owner_only_check(ctx):
            return

        server_lines = []

        for guild in sorted(
            self.bot.guilds,
            key=lambda item: item.name.lower(),
        ):
            server_lines.append(
                f"• **{safe_text(guild.name, 60)}**\n"
                f"ID: `{guild.id}` | Members: `{guild.member_count or 0}`"
            )

        pages = []
        current_page = ""

        for line in server_lines:
            if len(current_page) + len(line) + 2 > 3800:
                pages.append(current_page)
                current_page = ""

            current_page += f"{line}\n\n"

        if current_page:
            pages.append(current_page)

        if not pages:
            pages = ["The bot is not currently in any servers."]

        for page_number, page in enumerate(pages, start=1):
            await ctx.send(
                embed=create_embed(
                    f"📊 Bot Servers — Page {page_number}/{len(pages)}",
                    page,
                    EMBED_COLOR,
                    fields=[
                        ("Total Servers", str(len(self.bot.guilds)), True),
                        ("Command Access", "Owner Only", True),
                    ],
                )
            )

        await send_owner_log(
            "📊 Owner Used Server List",
            f"Owner: {ctx.author} ({ctx.author.id})\nTotal servers listed: {len(self.bot.guilds)}",
            INFO_COLOR,
        )

    @commands.command(name="botlogs")
    async def botlogs(self, ctx: commands.Context) -> None:
        if not await owner_only_check(ctx):
            return

        main_guild = self.bot.get_guild(MAIN_GUILD_ID)
        log_channel = (
            main_guild.get_channel(OWNER_LOG_CHANNEL_ID)
            if main_guild
            else None
        )

        status = (
            "Configured and available"
            if isinstance(log_channel, discord.TextChannel)
            else "Not configured or unavailable"
        )

        await ctx.send(
            embed=create_embed(
                "📋 Private Bot Logging",
                "Logs are sent only to the configured owner log channel.",
                EMBED_COLOR,
                fields=[
                    ("Main Server ID", str(MAIN_GUILD_ID or "Not Set"), False),
                    ("Log Channel ID", str(OWNER_LOG_CHANNEL_ID or "Not Set"), False),
                    ("Status", status, False),
                    (
                        "Events",
                        "Bot joins, leaves, AI errors, command errors, playback errors, track starts, and owner actions.",
                        False,
                    ),
                ],
            )
        )


# ==================== AI COMMANDS ====================

@bot.hybrid_command(
    name="ask",
    description="Ask the AI assistant a question",
)
async def ask(ctx: commands.Context, *, question: str) -> None:
    await defer_if_interaction(ctx)

    try:
        answer = await ask_openrouter(
            prompt=question,
            user_name=str(ctx.author),
            guild_name=ctx.guild.name if ctx.guild else None,
        )

        await send_response(
            ctx,
            embed=create_embed(
                f"🤖 {BOT_NAME} AI",
                safe_text(answer, 4000),
                EMBED_COLOR,
                fields=[
                    ("Asked by", ctx.author.mention, True),
                    ("Model", safe_text(OPENROUTER_MODEL, 100), True),
                ],
            ),
        )

        await send_owner_log(
            "🤖 AI Command Used",
            (
                f"User: {ctx.author} ({ctx.author.id})\n"
                f"Server: {ctx.guild.name if ctx.guild else 'Direct Message'}\n"
                f"Server ID: {ctx.guild.id if ctx.guild else 'N/A'}\n"
                f"Prompt: {safe_text(question, 800)}"
            ),
            INFO_COLOR,
        )

    except Exception as error:
        logger.exception("AI command failed.")

        await send_response(
            ctx,
            embed=create_embed(
                "❌ AI Service Error",
                "The AI service could not process your request right now. Please try again later.",
                ERROR_COLOR,
            ),
        )

        await send_owner_log(
            "❌ AI Command Error",
            f"User: {ctx.author} ({ctx.author.id})\nError: {safe_text(error, 1200)}",
            ERROR_COLOR,
        )


@bot.hybrid_command(
    name="recommend",
    description="Get AI-powered music recommendations",
)
async def recommend(
    ctx: commands.Context,
    *,
    mood_or_genre: str,
) -> None:
    await defer_if_interaction(ctx)

    prompt = (
        f"Recommend exactly 5 songs for this mood or genre: {mood_or_genre}. "
        "Write every item as: Song Title — Artist. Do not add a long explanation."
    )

    try:
        answer = await ask_openrouter(
            prompt=prompt,
            user_name=str(ctx.author),
            guild_name=ctx.guild.name if ctx.guild else None,
        )

        await send_response(
            ctx,
            embed=create_embed(
                "🎶 AI Music Recommendations",
                safe_text(answer, 3900),
                EMBED_COLOR,
                fields=[
                    ("Mood / Genre", safe_text(mood_or_genre, 100), True),
                    ("Requested by", ctx.author.mention, True),
                ],
            ),
        )

    except Exception as error:
        logger.exception("AI recommendation failed.")

        await send_response(
            ctx,
            embed=create_embed(
                "❌ AI Service Error",
                "Music recommendations could not be generated right now.",
                ERROR_COLOR,
            ),
        )

        await send_owner_log(
            "❌ AI Recommendation Error",
            (
                f"User: {ctx.author} ({ctx.author.id})\n"
                f"Prompt: {safe_text(mood_or_genre, 500)}\n"
                f"Error: {safe_text(error, 1200)}"
            ),
            ERROR_COLOR,
        )


# ==================== GENERAL COMMANDS ====================

@bot.hybrid_command(
    name="help",
    description="Show all bot commands",
)
async def help_command(ctx: commands.Context) -> None:
    await send_response(
        ctx,
        embed=create_embed(
            f"🎵 {BOT_NAME} Music Bot Help",
            f"Use `{DEFAULT_PREFIX}` prefix commands or slash commands. Join a voice channel before using playback commands.",
            EMBED_COLOR,
            fields=[
                (
                    "🎶 Music Commands",
                    "`play`, `pause`, `resume`, `skip`, `stop`, `queue`, `nowplaying`, `clear`, `volume`, `loop`, `shuffle`, `remove`, `history`, `mode_247`, `disconnect`",
                    False,
                ),
                ("🤖 AI Commands", "`ask`, `recommend`", False),
                (
                    "🔧 General Commands",
                    "`help`, `botinvite`, `ping`, `stats`, `setpfp`",
                    False,
                ),
                (
                    "📝 Examples",
                    f"`{DEFAULT_PREFIX}play Blinding Lights`\n"
                    "`/ask Give me five study tips`\n"
                    "`/recommend late-night chill music`",
                    False,
                ),
            ],
        ),
    )


@bot.hybrid_command(
    name="botinvite",
    description="Get the public bot invite link",
)
async def botinvite(ctx: commands.Context) -> None:
    if not DISCORD_CLIENT_ID:
        description = (
            "The bot invite link is not configured. Please contact the bot owner."
        )
    else:
        invite_url = (
            "https://discord.com/api/oauth2/authorize"
            f"?client_id={DISCORD_CLIENT_ID}"
            "&permissions=36700160"
            "&scope=bot%20applications.commands"
        )

        description = f"[Click here to invite {BOT_NAME}]({invite_url})"

    await send_response(
        ctx,
        embed=create_embed(
            f"🔗 Invite {BOT_NAME}",
            description,
            EMBED_COLOR,
        ),
    )


@bot.hybrid_command(
    name="ping",
    description="Check bot latency",
)
async def ping(ctx: commands.Context) -> None:
    latency = round(bot.latency * 1000)

    await send_response(
        ctx,
        embed=create_embed(
            "🏓 Pong!",
            f"Current latency: {latency}ms",
            SUCCESS_COLOR,
        ),
    )


@bot.hybrid_command(
    name="stats",
    description="Show bot statistics",
)
async def stats(ctx: commands.Context) -> None:
    total_members = sum(
        guild.member_count or 0
        for guild in bot.guilds
    )

    await send_response(
        ctx,
        embed=create_embed(
            "📊 Bot Statistics",
            "Current bot information.",
            EMBED_COLOR,
            fields=[
                ("Servers", str(len(bot.guilds)), True),
                ("Members", str(total_members), True),
                ("Ping", f"{round(bot.latency * 1000)}ms", True),
                ("Prefix", DEFAULT_PREFIX, True),
                ("AI Model", safe_text(OPENROUTER_MODEL, 60), True),
            ],
        ),
    )


# ==================== EVENTS ====================

@bot.event
async def on_ready() -> None:
    logger.info("Logged in as %s", bot.user)
    logger.info("Connected to %s server(s)", len(bot.guilds))

    try:
        # Register the persistent button view only once.
        if not getattr(bot, "_music_view_registered", False):
            bot.add_view(MusicControls())
            bot._music_view_registered = True
            logger.info("Persistent controls registered.")
    except Exception as error:
        logger.error("Persistent view error: %s", error)

    try:
        synced = await bot.tree.sync()
        logger.info("Synced %s slash command(s)", len(synced))
    except Exception as error:
        logger.error("Slash command sync failed: %s", error)

        await send_owner_log(
            "❌ Slash Command Sync Error",
            f"Error: {safe_text(error, 1200)}",
            ERROR_COLOR,
        )

    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name=f"{DEFAULT_PREFIX}help | /play",
        ),
        status=discord.Status.online,
    )

    await send_owner_log(
        "✅ Bot Online",
        f"Bot: {bot.user}\nServers: {len(bot.guilds)}\nAI Model: {OPENROUTER_MODEL}",
        SUCCESS_COLOR,
    )


@bot.event
async def on_guild_join(guild: discord.Guild) -> None:
    logger.info("Joined server: %s (%s)", guild.name, guild.id)

    await send_owner_log(
        "➕ Bot Joined a Server",
        (
            f"Server: {guild.name}\n"
            f"Server ID: {guild.id}\n"
            f"Members: {guild.member_count or 0}\n"
            f"Owner ID: {guild.owner_id}\n"
            f"Total Servers: {len(bot.guilds)}"
        ),
        SUCCESS_COLOR,
    )

    for channel in guild.text_channels:
        permissions = channel.permissions_for(guild.me)

        if permissions.send_messages and permissions.embed_links:
            try:
                await channel.send(
                    embed=create_embed(
                        "👋 Thanks for Inviting Me!",
                        (
                            f"Hello **{safe_text(guild.name, 100)}**!\n\n"
                            "Use `/help` to view all commands.\n"
                            "Join a voice channel and use `/play <song>`.\n"
                            "Use `/ask <question>` for AI assistance."
                        ),
                        SUCCESS_COLOR,
                    )
                )
            except Exception:
                pass

            break


@bot.event
async def on_guild_remove(guild: discord.Guild) -> None:
    logger.info("Removed from server: %s (%s)", guild.name, guild.id)

    guild_queues.pop(guild.id, None)

    await send_owner_log(
        "➖ Bot Removed From a Server",
        (
            f"Server: {guild.name}\n"
            f"Server ID: {guild.id}\n"
            f"Members: {guild.member_count or 0}\n"
            f"Total Servers Remaining: {len(bot.guilds)}"
        ),
        ERROR_COLOR,
    )


@bot.event
async def on_command_error(
    ctx: commands.Context,
    error: commands.CommandError,
) -> None:
    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.CheckFailure):
        return

    if isinstance(error, commands.MissingRequiredArgument):
        command_name = (
            ctx.command.qualified_name
            if ctx.command
            else "command"
        )

        signature = (
            ctx.command.signature
            if ctx.command
            else ""
        )

        await ctx.send(
            embed=create_embed(
                "❌ Missing Argument",
                f"Usage: {DEFAULT_PREFIX}{command_name} {signature}",
                ERROR_COLOR,
            )
        )
        return

    if isinstance(error, commands.BadArgument):
        await ctx.send(
            embed=create_embed(
                "❌ Invalid Argument",
                "Please check the command arguments and try again.",
                ERROR_COLOR,
            )
        )
        return

    logger.exception("Unhandled command error: %s", error)

    await ctx.send(
        embed=create_embed(
            "❌ Command Error",
            "An unexpected error occurred while running that command. The bot owner has been notified.",
            ERROR_COLOR,
        )
    )

    command_text = (
        ctx.message.content
        if ctx.message
        else "Interaction command"
    )

    await send_owner_log(
        "❌ Unhandled Command Error",
        (
            f"User: {ctx.author} ({ctx.author.id})\n"
            f"Server: {ctx.guild.name if ctx.guild else 'Direct Message'}\n"
            f"Server ID: {ctx.guild.id if ctx.guild else 'N/A'}\n"
            f"Channel ID: {ctx.channel.id}\n"
            f"Command: {safe_text(command_text, 700)}\n"
            f"Error: {safe_text(repr(error), 1500)}"
        ),
        ERROR_COLOR,
    )


@bot.event
async def on_error(event_method: str, *args, **kwargs) -> None:
    error_trace = traceback.format_exc()

    logger.error(
        "Unhandled event error in %s:\n%s",
        event_method,
        error_trace,
    )

    await send_owner_log(
        "❌ Unhandled Bot Event Error",
        (
            f"Event: {event_method}\n"
            f"Traceback:\n{safe_text(error_trace, 3000)}"
        ),
        ERROR_COLOR,
    )


# ==================== MAIN ====================

async def main() -> None:
    if not DISCORD_TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN is missing. Add it to Environment Variables / Secrets."
        )

    if OWNER_ID == 0:
        logger.warning(
            "OWNER_ID is not configured. Owner-only commands will not work."
        )

    if not MAIN_GUILD_ID or not OWNER_LOG_CHANNEL_ID:
        logger.warning(
            "Private owner logs are not configured. "
            "Set MAIN_GUILD_ID and OWNER_LOG_CHANNEL_ID."
        )

    if not OPENROUTER_API_KEY:
        logger.warning(
            "OPENROUTER_API_KEY is not configured. "
            "AI commands will not work until you add the key."
        )

    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        logger.warning(
            "Spotify API keys are not configured. "
            "/play will not work until you add them."
        )

    async with bot:
        await bot.add_cog(MusicCommands(bot))
        await bot.add_cog(OwnerCommands(bot))
        await bot.add_cog(SetPFP(bot))
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())

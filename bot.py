import os
import re
import asyncio
import aiohttp
import discord
import yt_dlp
import spotipy

from dotenv import load_dotenv
from openai import OpenAI
from discord.ext import commands
from discord import app_commands
from spotipy.oauth2 import SpotifyClientCredentials


# ============================================================
# CONFIG
# ============================================================

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")


if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing from .env")

if not OPENROUTER_API_KEY:
    raise RuntimeError("OPENROUTER_API_KEY is missing from .env")


# ============================================================
# OPENROUTER
# ============================================================

ai = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY
)


# ============================================================
# SPOTIFY
# ============================================================

spotify = None

if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
    spotify = spotipy.Spotify(
        auth_manager=SpotifyClientCredentials(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET
        )
    )


# ============================================================
# DISCORD
# ============================================================

intents = discord.Intents.default()

intents.message_content = True
intents.voice_states = True
intents.guilds = True
intents.members = True

bot = commands.Bot(
    command_prefix=commands.when_mentioned_or("p/"),
    intents=intents,
    help_command=None
)


# ============================================================
# MUSIC DATA
# ============================================================

queues = {}
current_song = {}
loop_modes = {}
volume_levels = {}
stay_247 = {}

player_messages = {}
player_tasks = {}

song_positions = {}


def get_queue(guild_id):
    if guild_id not in queues:
        queues[guild_id] = []

    return queues[guild_id]


# ============================================================
# YT-DLP
# ============================================================

SEARCH_OPTIONS = {
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "noplaylist": True,
    "skip_download": True,
}

STREAM_OPTIONS = {
    "quiet": True,
    "no_warnings": True,
    "format": "bestaudio/best",
    "noplaylist": True,
    "skip_download": True,
}

FFMPEG_OPTIONS = {
    "before_options":
        "-reconnect 1 "
        "-reconnect_streamed 1 "
        "-reconnect_delay_max 5",

    "options":
        "-vn"
}


# ============================================================
# HELPERS
# ============================================================

def format_time(seconds):

    seconds = max(0, int(seconds))

    minutes = seconds // 60
    seconds = seconds % 60

    return f"{minutes}:{seconds:02d}"


def progress_bar(position, duration, length=18):

    if not duration or duration <= 0:
        return "🔘──────────────"

    percentage = min(
        1,
        max(0, position / duration)
    )

    filled = int(
        percentage * length
    )

    empty = length - filled

    return (
        "━" * filled +
        "🔘" +
        "━" * empty
    )


def is_spotify(url):
    return "open.spotify.com" in url.lower()


def is_youtube(url):

    return (
        "youtube.com" in url.lower()
        or "youtu.be" in url.lower()
    )


def is_soundcloud(url):
    return "soundcloud.com" in url.lower()


# ============================================================
# SPOTIFY
# ============================================================

def get_spotify_track(url):

    if not spotify:
        raise RuntimeError(
            "Spotify Client ID/Secret are missing."
        )

    track = spotify.track(url)

    artists = ", ".join(
        artist["name"]
        for artist in track["artists"]
    )

    return f"{artists} - {track['name']}"


def get_spotify_playlist(url):

    if not spotify:
        raise RuntimeError(
            "Spotify Client ID/Secret are missing."
        )

    results = spotify.playlist_tracks(url)

    songs = []

    while results:

        for item in results["items"]:

            track = item.get("track")

            if not track:
                continue

            artists = ", ".join(
                artist["name"]
                for artist in track["artists"]
            )

            songs.append(
                f"{artists} - {track['name']}"
            )

        if results.get("next"):
            results = spotify.next(results)
        else:
            break

    return songs


# ============================================================
# YOUTUBE SEARCH
# ============================================================

async def search_youtube(query):

    loop = asyncio.get_running_loop()

    def search():

        with yt_dlp.YoutubeDL(
            SEARCH_OPTIONS
        ) as ydl:

            data = ydl.extract_info(
                f"ytsearch1:{query}",
                download=False
            )

            entries = data.get("entries")

            if not entries:
                return None

            video = entries[0]

            return {
                "title": video.get(
                    "title",
                    "Unknown"
                ),

                "webpage": video.get(
                    "webpage_url"
                ),

                "thumbnail": video.get(
                    "thumbnail"
                ),

                "duration": video.get(
                    "duration",
                    0
                )
            }

    return await loop.run_in_executor(
        None,
        search
    )


# ============================================================
# GET STREAM
# ============================================================

async def get_stream(url):

    loop = asyncio.get_running_loop()

    def extract():

        with yt_dlp.YoutubeDL(
            STREAM_OPTIONS
        ) as ydl:

            info = ydl.extract_info(
                url,
                download=False
            )

            return {
                "url": info["url"],

                "title": info.get(
                    "title",
                    "Unknown"
                ),

                "thumbnail": info.get(
                    "thumbnail"
                ),

                "duration": info.get(
                    "duration",
                    0
                )
            }

    return await loop.run_in_executor(
        None,
        extract
    )


# ============================================================
# OPENROUTER AI
# ============================================================

async def ai_search(query):

    loop = asyncio.get_running_loop()

    def ask():

        response = ai.chat.completions.create(

            model="google/gemini-2.5-flash",

            messages=[
                {
                    "role": "system",
                    "content": """
You are a Discord music search assistant.

Convert the user's request into a good music
search query.

Return ONLY the search query.

Examples:

play faded
Faded Alan Walker

play blinding lights
The Weeknd Blinding Lights

play chill songs by joji
Joji chill songs

play heat waves
Glass Animals Heat Waves
"""
                },

                {
                    "role": "user",
                    "content": query
                }
            ],

            temperature=0.2,
            max_tokens=80
        )

        return response.choices[0].message.content.strip()

    return await loop.run_in_executor(
        None,
        ask
    )


async def ai_suggest(query):

    loop = asyncio.get_running_loop()

    def ask():

        response = ai.chat.completions.create(

            model="google/gemini-2.5-flash",

            messages=[
                {
                    "role": "system",
                    "content": """
You are a music recommendation AI.

Give exactly 10 music recommendations.

Format:

1. Song - Artist
2. Song - Artist
3. Song - Artist

Do not write a long explanation.
"""
                },

                {
                    "role": "user",
                    "content": query
                }
            ],

            temperature=0.8,
            max_tokens=400
        )

        return response.choices[0].message.content.strip()

    return await loop.run_in_executor(
        None,
        ask
    )


# ============================================================
# RESOLVE MUSIC
# ============================================================

async def resolve_music(query):

    query = query.strip()

    # Spotify playlist
    if is_spotify(query) and "/playlist/" in query:

        names = get_spotify_playlist(query)

        results = []

        for name in names[:100]:

            result = await search_youtube(name)

            if result:
                results.append(result)

        return results

    # Spotify track
    if is_spotify(query) and "/track/" in query:

        name = get_spotify_track(query)

        result = await search_youtube(name)

        return [result] if result else []

    # YouTube
    if is_youtube(query):

        result = await search_youtube(query)

        return [result] if result else []

    # SoundCloud
    if is_soundcloud(query):

        result = await search_youtube(query)

        return [result] if result else []

    # Normal search
    ai_query = await ai_search(query)

    print(
        f"[AI] {query} -> {ai_query}"
    )

    result = await search_youtube(ai_query)

    return [result] if result else []


# ============================================================
# VOICE
# ============================================================

async def ensure_voice(ctx):

    if not ctx.author.voice:

        await ctx.send(
            "❌ Pehle voice channel join karo."
        )

        return None

    channel = ctx.author.voice.channel

    voice = ctx.guild.voice_client

    if voice:

        if voice.channel != channel:
            await voice.move_to(channel)

    else:

        voice = await channel.connect()

    return voice


# ============================================================
# PLAYER EMBED
# ============================================================

def create_player_embed(
    guild_id,
    paused=False
):

    song = current_song.get(guild_id)

    if not song:
        return discord.Embed(
            title="🎵 Music Player",
            description="Nothing is playing."
        )


    duration = song.get(
        "duration",
        0
    )

    position = song_positions.get(
        guild_id,
        0
    )


    if paused:

        status = "⏸️ Paused"

    else:

        status = "▶️ Playing"


    bar = progress_bar(
        position,
        duration
    )


    embed = discord.Embed(
        title="🎶 Now Playing",
        description=
            f"**{song['title']}**",
        color=discord.Color.blurple()
    )


    if song.get("thumbnail"):

        embed.set_thumbnail(
            url=song["thumbnail"]
        )


    embed.add_field(
        name="Status",
        value=status,
        inline=True
    )


    embed.add_field(
        name="Loop",
        value=(
            "Off"
            if not loop_modes.get(guild_id)
            else loop_modes[guild_id]
        ),
        inline=True
    )


    embed.add_field(
        name="Progress",
        value=(
            f"`{format_time(position)}` "
            f"{bar} "
            f"`{format_time(duration)}`"
        ),
        inline=False
    )


    embed.set_footer(
        text="10s seek • Playback controls"
    )


    return embed


# ============================================================
# PLAYER BUTTONS
# ============================================================

class MusicView(discord.ui.View):

    def __init__(self, guild_id):

        super().__init__(
            timeout=None
        )

        self.guild_id = guild_id


    # --------------------------------------------------------
    # BACK 10
    # --------------------------------------------------------

    @discord.ui.button(
        label="10s",
        emoji="⏪",
        style=discord.ButtonStyle.secondary
    )
    async def back10(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await seek_music(
            interaction,
            -10
        )


    # --------------------------------------------------------
    # PAUSE / PLAY
    # --------------------------------------------------------

    @discord.ui.button(
        label="Pause",
        emoji="⏯️",
        style=discord.ButtonStyle.primary
    )
    async def pause_play(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        voice = interaction.guild.voice_client

        if not voice:

            await interaction.response.send_message(
                "❌ I'm not in VC.",
                ephemeral=True
            )

            return


        if voice.is_playing():

            voice.pause()

            button.label = "Play"

        elif voice.is_paused():

            voice.resume()

            button.label = "Pause"

        else:

            await interaction.response.send_message(
                "❌ Nothing is playing.",
                ephemeral=True
            )

            return


        await interaction.response.edit_message(
            embed=create_player_embed(
                self.guild_id,
                voice.is_paused()
            ),
            view=self
        )


    # --------------------------------------------------------
    # FORWARD 10
    # --------------------------------------------------------

    @discord.ui.button(
        label="10s",
        emoji="⏩",
        style=discord.ButtonStyle.secondary
    )
    async def forward10(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await seek_music(
            interaction,
            10
        )


    # --------------------------------------------------------
    # LOOP
    # --------------------------------------------------------

    @discord.ui.button(
        label="Loop",
        emoji="🔁",
        style=discord.ButtonStyle.success
    )
    async def loop(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        guild_id = self.guild_id

        current = loop_modes.get(
            guild_id
        )


        if current is None:

            loop_modes[guild_id] = "current"

            text = "🔂 Current song"

        elif current == "current":

            loop_modes[guild_id] = "queue"

            text = "🔁 Queue"

        else:

            loop_modes[guild_id] = None

            text = "➡️ Off"


        await interaction.response.edit_message(
            embed=create_player_embed(
                guild_id
            ),
            view=self
        )


    # --------------------------------------------------------
    # SKIP
    # --------------------------------------------------------

    @discord.ui.button(
        label="Skip",
        emoji="⏭️",
        style=discord.ButtonStyle.danger
    )
    async def skip(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        voice = interaction.guild.voice_client

        if not voice or not voice.is_playing():

            await interaction.response.send_message(
                "❌ Nothing is playing.",
                ephemeral=True
            )

            return


        voice.stop()

        await interaction.response.defer()


    # --------------------------------------------------------
    # STOP
    # --------------------------------------------------------

    @discord.ui.button(
        label="Stop",
        emoji="⏹️",
        style=discord.ButtonStyle.danger
    )
    async def stop(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        guild_id = self.guild_id

        queues[guild_id] = []

        loop_modes[guild_id] = None


        voice = interaction.guild.voice_client

        if voice:

            voice.stop()


        await interaction.response.edit_message(
            embed=discord.Embed(
                title="⏹️ Music Stopped",
                description="Queue cleared.",
                color=discord.Color.red()
            ),
            view=None
        )


# ============================================================
# SEEK
# ============================================================

async def seek_music(
    interaction,
    amount
):

    guild_id = interaction.guild.id

    song = current_song.get(
        guild_id
    )

    voice = interaction.guild.voice_client


    if not song or not voice:

        await interaction.response.send_message(
            "❌ Nothing is playing.",
            ephemeral=True
        )

        return


    old_position = song_positions.get(
        guild_id,
        0
    )

    duration = song.get(
        "duration",
        0
    )


    new_position = old_position + amount

    new_position = max(
        0,
        new_position
    )


    if duration:

        new_position = min(
            new_position,
            duration - 1
        )


    song_positions[guild_id] = new_position


    # Restart stream at requested position
    await restart_from_position(
        guild_id,
        new_position
    )


    await interaction.response.edit_message(
        embed=create_player_embed(
            guild_id
        ),
        view=MusicView(guild_id)
    )


# ============================================================
# RESTART STREAM AT POSITION
# ============================================================

async def restart_from_position(
    guild_id,
    position
):

    guild = bot.get_guild(
        guild_id
    )

    if not guild:
        return


    voice = guild.voice_client

    song = current_song.get(
        guild_id
    )

    if not voice or not song:
        return


    try:

        stream = await get_stream(
            song["webpage"]
        )


        ffmpeg_options = {
            "before_options":
                f"-ss {int(position)} "
                "-reconnect 1 "
                "-reconnect_streamed 1 "
                "-reconnect_delay_max 5",

            "options":
                "-vn"
        }


        source = discord.FFmpegPCMAudio(
            stream["url"],
            **ffmpeg_options
        )


        source = discord.PCMVolumeTransformer(
            source,
            volume=volume_levels.get(
                guild_id,
                0.5
            )
        )


        def after(error):

            if error:

                print(
                    f"[PLAYER ERROR] {error}"
                )


            # Current loop
            if loop_modes.get(
                guild_id
            ) == "current":

                get_queue(
                    guild_id
                ).insert(
                    0,
                    song
                )


            asyncio.run_coroutine_threadsafe(
                play_next(guild_id),
                bot.loop
            )


        voice.stop()

        voice.play(
            source,
            after=after
        )


    except Exception as error:

        print(
            f"[SEEK ERROR] {error}"
        )


# ============================================================
# PROGRESS UPDATER
# ============================================================

async def update_player(guild_id):

    try:

        while True:

            await asyncio.sleep(5)

            message = player_messages.get(
                guild_id
            )

            song = current_song.get(
                guild_id
            )

            voice = (
                bot.get_guild(guild_id)
                .voice_client
                if bot.get_guild(guild_id)
                else None
            )


            if not message or not song:

                return


            if voice and voice.is_playing():

                song_positions[guild_id] = (
                    song_positions.get(
                        guild_id,
                        0
                    ) + 5
                )


            if (
                song.get("duration")
                and song_positions.get(
                    guild_id,
                    0
                ) >= song["duration"]
            ):

                song_positions[guild_id] = (
                    song["duration"]
                )


            try:

                await message.edit(
                    embed=create_player_embed(
                        guild_id,
                        voice.is_paused()
                        if voice
                        else False
                    ),
                    view=MusicView(
                        guild_id
                    )
                )

            except Exception:
                return


    except asyncio.CancelledError:
        return


# ============================================================
# PLAY NEXT
# ============================================================

async def play_next(guild_id):

    guild = bot.get_guild(
        guild_id
    )

    if not guild:
        return


    voice = guild.voice_client

    if not voice:
        return


    queue = get_queue(
        guild_id
    )


    if not queue:

        if not stay_247.get(
            guild_id,
            False
        ):

            await voice.disconnect()

        return


    song = queue.pop(0)

    current_song[guild_id] = song

    song_positions[guild_id] = 0


    try:

        stream = await get_stream(
            song["webpage"]
        )


        source = discord.FFmpegPCMAudio(
            stream["url"],
            **FFMPEG_OPTIONS
        )


        source = discord.PCMVolumeTransformer(
            source,
            volume=volume_levels.get(
                guild_id,
                0.5
            )
        )


        def after(error):

            if error:

                print(
                    f"[PLAYER ERROR] {error}"
                )


            if loop_modes.get(
                guild_id
            ) == "current":

                queue.insert(
                    0,
                    song
                )

            elif loop_modes.get(
                guild_id
            ) == "queue":

                queue.append(
                    song
                )


            asyncio.run_coroutine_threadsafe(
                play_next(guild_id),
                bot.loop
            )


        voice.play(
            source,
            after=after
        )


        embed = create_player_embed(
            guild_id
        )


        view = MusicView(
            guild_id
        )


        # Send / edit player
        try:

            old_message = player_messages.get(
                guild_id
            )

            if old_message:

                await old_message.edit(
                    embed=embed,
                    view=view
                )

                message = old_message

            else:

                message = await voice.channel.send(
                    embed=embed,
                    view=view
                )

            player_messages[guild_id] = message


        except Exception as error:

            print(
                f"[EMBED ERROR] {error}"
            )


        # Start progress updater
        old_task = player_tasks.get(
            guild_id
        )

        if old_task:

            old_task.cancel()


        player_tasks[guild_id] = asyncio.create_task(
            update_player(guild_id)
        )


    except Exception as error:

        print(
            f"[PLAY ERROR] {error}"
        )

        await play_next(
            guild_id
        )


# ============================================================
# PLAY MUSIC
# ============================================================

async def play_music(
    ctx,
    query
):

    voice = await ensure_voice(ctx)

    if not voice:
        return


    await ctx.send(
        "🔎 Searching..."
    )


    try:

        songs = await resolve_music(
            query
        )

    except Exception as error:

        await ctx.send(
            f"❌ Error:\n```{error}```"
        )

        return


    if not songs:

        await ctx.send(
            "❌ Song nahi mili."
        )

        return


    queue = get_queue(
        ctx.guild.id
    )


    for song in songs:
        queue.append(song)


    if len(songs) == 1:

        await ctx.send(
            f"🎵 Added **{songs[0]['title']}**"
        )

    else:

        await ctx.send(
            f"🎵 Added **{len(songs)} songs**."
        )


    if (
        not voice.is_playing()
        and not voice.is_paused()
    ):

        await play_next(
            ctx.guild.id
        )


# ============================================================
# @BOT p SONG
# ============================================================

@bot.event
async def on_message(message):

    if message.author.bot:
        return


    if bot.user and bot.user.mentioned_in(
        message
    ):

        content = re.sub(
            rf"<@!?{bot.user.id}>",
            "",
            message.content
        ).strip()


        if content.lower().startswith(
            "p "
        ):

            query = content[2:].strip()


            if not query:

                await message.channel.send(
                    "🎵 Use:\n"
                    "`@Bot p <song name/link>`"
                )

                return


            ctx = await bot.get_context(
                message
            )


            await play_music(
                ctx,
                query
            )

            return


    await bot.process_commands(
        message
    )


# ============================================================
# /PLAY
# ============================================================

@bot.tree.command(
    name="play",
    description="Play a song or music link."
)
@app_commands.describe(
    query="Song name or music link"
)
async def play_command(
    interaction: discord.Interaction,
    query: str
):

    await interaction.response.defer()


    ctx = await bot.get_context(
        interaction
    )

    ctx.author = interaction.user
    ctx.guild = interaction.guild
    ctx.channel = interaction.channel


    await play_music(
        ctx,
        query
    )


# ============================================================
# /SKIP
# ============================================================

@bot.tree.command(
    name="skip",
    description="Skip current song."
)
async def skip_command(
    interaction: discord.Interaction
):

    voice = interaction.guild.voice_client

    if not voice or not voice.is_playing():

        await interaction.response.send_message(
            "❌ Nothing is playing.",
            ephemeral=True
        )

        return


    voice.stop()

    await interaction.response.send_message(
        "⏭️ Skipped."
    )


# ============================================================
# /PAUSE
# ============================================================

@bot.tree.command(
    name="pause",
    description="Pause music."
)
async def pause_command(
    interaction: discord.Interaction
):

    voice = interaction.guild.voice_client

    if not voice or not voice.is_playing():

        await interaction.response.send_message(
            "❌ Nothing is playing.",
            ephemeral=True
        )

        return


    voice.pause()

    await interaction.response.send_message(
        "⏸️ Paused."
    )


# ============================================================
# /RESUME
# ============================================================

@bot.tree.command(
    name="resume",
    description="Resume music."
)
async def resume_command(
    interaction: discord.Interaction
):

    voice = interaction.guild.voice_client

    if not voice or not voice.is_paused():

        await interaction.response.send_message(
            "❌ Nothing is paused.",
            ephemeral=True
        )

        return


    voice.resume()

    await interaction.response.send_message(
        "▶️ Resumed."
    )


# ============================================================
# /STOP
# ============================================================

@bot.tree.command(
    name="stop",
    description="Stop music and clear queue."
)
async def stop_command(
    interaction: discord.Interaction
):

    guild_id = interaction.guild.id

    queues[guild_id] = []

    loop_modes[guild_id] = None


    voice = interaction.guild.voice_client

    if voice:
        voice.stop()


    await interaction.response.send_message(
        "⏹️ Music stopped."
    )


# ============================================================
# /QUEUE
# ============================================================

@bot.tree.command(
    name="queue",
    description="Show music queue."
)
async def queue_command(
    interaction: discord.Interaction
):

    queue = get_queue(
        interaction.guild.id
    )


    if not queue:

        await interaction.response.send_message(
            "📭 Queue empty."
        )

        return


    text = "\n".join(
        f"`{i + 1}.` {song['title']}"
        for i, song in enumerate(
            queue[:20]
        )
    )


    embed = discord.Embed(
        title="🎶 Music Queue",
        description=text,
        color=discord.Color.blurple()
    )


    await interaction.response.send_message(
        embed=embed
    )


# ============================================================
# /LOOP
# ============================================================

@bot.tree.command(
    name="loop",
    description="Change loop mode."
)
@app_commands.describe(
    mode="off / current / queue"
)
async def loop_command(
    interaction: discord.Interaction,
    mode: str
):

    mode = mode.lower()

    if mode not in (
        "off",
        "current",
        "queue"
    ):

        await interaction.response.send_message(
            "❌ Use `off`, `current`, or `queue`."
        )

        return


    guild_id = interaction.guild.id


    if mode == "off":

        loop_modes[guild_id] = None

    else:

        loop_modes[guild_id] = mode


    await interaction.response.send_message(
        f"🔁 Loop: **{mode}**"
    )


# ============================================================
# /247
# ============================================================

@bot.tree.command(
    name="247",
    description="Toggle 24/7 VC mode."
)
async def mode_247(
    interaction: discord.Interaction
):

    guild_id = interaction.guild.id


    stay_247[guild_id] = not stay_247.get(
        guild_id,
        False
    )


    if stay_247[guild_id]:

        if not interaction.user.voice:

            stay_247[guild_id] = False

            await interaction.response.send_message(
                "❌ Pehle VC join karo."
            )

            return


        if not interaction.guild.voice_client:

            await interaction.user.voice.channel.connect()


        await interaction.response.send_message(
            "♾️ **24/7 mode ON**"
        )

    else:

        await interaction.response.send_message(
            "♾️ **24/7 mode OFF**"
        )


# ============================================================
# /LEAVE
# ============================================================

@bot.tree.command(
    name="leave",
    description="Leave voice channel."
)
async def leave_command(
    interaction: discord.Interaction
):

    guild_id = interaction.guild.id

    stay_247[guild_id] = False


    voice = interaction.guild.voice_client

    if not voice:

        await interaction.response.send_message(
            "❌ I'm not in VC."
        )

        return


    await voice.disconnect()


    await interaction.response.send_message(
        "👋 Left VC."
    )


# ============================================================
# /VOLUME
# ============================================================

@bot.tree.command(
    name="volume",
    description="Change volume."
)
@app_commands.describe(
    amount="1-100"
)
async def volume_command(
    interaction: discord.Interaction,
    amount: int
):

    if amount < 1 or amount > 100:

        await interaction.response.send_message(
            "❌ Volume must be between 1 and 100."
        )

        return


    guild_id = interaction.guild.id

    volume_levels[guild_id] = (
        amount / 100
    )


    voice = interaction.guild.voice_client

    if voice and voice.source:

        if isinstance(
            voice.source,
            discord.PCMVolumeTransformer
        ):

            voice.source.volume = (
                amount / 100
            )


    await interaction.response.send_message(
        f"🔊 Volume: **{amount}%**"
    )


# ============================================================
# /SUGGEST
# ============================================================

@bot.tree.command(
    name="suggest",
    description="Get AI music recommendations."
)
@app_commands.describe(
    request="Mood, genre, artist, activity..."
)
async def suggest_command(
    interaction: discord.Interaction,
    request: str
):

    await interaction.response.defer()


    try:

        result = await ai_suggest(
            request
        )


        embed = discord.Embed(
            title="🤖 AI Music Suggestions",
            description=result,
            color=discord.Color.purple()
        )


        embed.set_footer(
            text="Powered by OpenRouter"
        )


        await interaction.followup.send(
            embed=embed
        )


    except Exception as error:

        await interaction.followup.send(
            f"❌ AI Error:\n```{error}```"
        )


# ============================================================
# /SETPFP
# ============================================================

@bot.tree.command(
    name="setpfp",
    description="Change bot profile picture."
)
@app_commands.checks.has_permissions(
    administrator=True
)
@app_commands.describe(
    image_url="Direct image URL"
)
async def setpfp_command(
    interaction: discord.Interaction,
    image_url: str
):

    await interaction.response.defer(
        ephemeral=True
    )


    try:

        async with aiohttp.ClientSession() as session:

            async with session.get(
                image_url
            ) as response:

                if response.status != 200:

                    raise Exception(
                        "Image couldn't be downloaded."
                    )

                image = await response.read()


        await bot.user.edit(
            avatar=image
        )


        await interaction.followup.send(
            "✅ PFP changed!",
            ephemeral=True
        )


    except Exception as error:

        await interaction.followup.send(
            f"❌ Error: `{error}`",
            ephemeral=True
        )


# ============================================================
# /SETBANNER
# ============================================================

@bot.tree.command(
    name="setbanner",
    description="Change bot banner."
)
@app_commands.checks.has_permissions(
    administrator=True
)
@app_commands.describe(
    image_url="Direct image URL"
)
async def setbanner_command(
    interaction: discord.Interaction,
    image_url: str
):

    await interaction.response.defer(
        ephemeral=True
    )


    try:

        async with aiohttp.ClientSession() as session:

            async with session.get(
                image_url
            ) as response:

                if response.status != 200:

                    raise Exception(
                        "Banner couldn't be downloaded."
                    )

                image = await response.read()


        await bot.user.edit(
            banner=image
        )


        await interaction.followup.send(
            "✅ Banner changed!",
            ephemeral=True
        )


    except Exception as error:

        await interaction.followup.send(
            f"❌ Error: `{error}`",
            ephemeral=True
        )


# ============================================================
# ERROR HANDLER
# ============================================================

@bot.tree.error
async def command_error(
    interaction,
    error
):

    print(
        "[COMMAND ERROR]",
        error
    )


    if isinstance(
        error,
        app_commands.errors.MissingPermissions
    ):

        message = (
            "❌ Administrator permission required."
        )

    else:

        message = (
            "❌ Something went wrong."
        )


    if interaction.response.is_done():

        await interaction.followup.send(
            message,
            ephemeral=True
        )

    else:

        await interaction.response.send_message(
            message,
            ephemeral=True
        )


# ============================================================
# READY
# ============================================================

@bot.event
async def on_ready():

    print(
        "================================"
    )

    print(
        f"Logged in as {bot.user}"
    )

    print(
        f"Bot ID: {bot.user.id}"
    )

    print(
        "================================"
    )


    try:

        synced = await bot.tree.sync()

        print(
            f"Synced {len(synced)} slash commands."
        )

    except Exception as error:

        print(
            f"Slash sync error: {error}"
        )


# ============================================================
# RUN
# ============================================================

bot.run(
    DISCORD_TOKEN
)=========================

bot.run(
    DISCORD_TOKEN
)
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
# OPENROUTER & SPOTIFY
# ============================================================

ai = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY
)

spotify = None
if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
    spotify = spotipy.Spotify(
        auth_manager=SpotifyClientCredentials(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET
        )
    )

# ============================================================
# DISCORD BOT SETUP
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True

bot = commands.Bot(
    command_prefix=commands.when_mentioned_or("p/"),
    intents=intents,
    help_command=None
)

# ============================================================
# GLOBAL DATA
# ============================================================

queues = {}
current_song = {}
loop_modes = {}
volume_levels = {}
stay_247 = {}

player_messages = {}
player_tasks = {}
song_positions = {}
inactivity_tasks = {}

def get_queue(guild_id):
    if guild_id not in queues:
        queues[guild_id] = []
    return queues[guild_id]

# ============================================================
# YT-DLP & FFMPEG
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
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn"
}

# ============================================================
# HELPERS
# ============================================================

def format_time(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"

def progress_bar(position, duration, length=18):
    if not duration or duration <= 0:
        return "🔘──────────────"
    percentage = min(1, max(0, position / duration))
    filled = int(percentage * length)
    return "━" * filled + "🔘" + "━" * (length - filled)

def is_spotify(url): return "open.spotify.com" in url.lower()
def is_youtube(url): return "youtube.com" in url.lower() or "youtu.be" in url.lower()
def is_soundcloud(url): return "soundcloud.com" in url.lower()

# ============================================================
# INACTIVITY AUTO-LEAVE (15 MINS)
# ============================================================

def reset_inactivity_timer(guild_id):
    if guild_id in inactivity_tasks:
        inactivity_tasks[guild_id].cancel()
    inactivity_tasks[guild_id] = asyncio.create_task(inactivity_timeout(guild_id))

async def inactivity_timeout(guild_id):
    await asyncio.sleep(900)  # 15 Minutes
    if stay_247.get(guild_id, False):
        return

    guild = bot.get_guild(guild_id)
    if guild and guild.voice_client:
        if not guild.voice_client.is_playing():
            queues[guild_id] = []
            await guild.voice_client.disconnect()

# ============================================================
# SPOTIFY & YOUTUBE
# ============================================================

def get_spotify_track(url):
    if not spotify: raise RuntimeError("Spotify credentials missing.")
    track = spotify.track(url)
    return f"{', '.join(a['name'] for a in track['artists'])} - {track['name']}"

def get_spotify_playlist(url):
    if not spotify: raise RuntimeError("Spotify credentials missing.")
    results = spotify.playlist_tracks(url)
    songs = []
    while results:
        for item in results["items"]:
            t = item.get("track")
            if t: songs.append(f"{', '.join(a['name'] for a in t['artists'])} - {t['name']}")
        results = spotify.next(results) if results.get("next") else None
    return songs

async def search_youtube(query):
    loop = asyncio.get_running_loop()
    def search():
        with yt_dlp.YoutubeDL(SEARCH_OPTIONS) as ydl:
            data = ydl.extract_info(f"ytsearch1:{query}", download=False)
            entries = data.get("entries")
            if not entries: return None
            v = entries[0]
            return {"title": v.get("title", "Unknown"), "webpage": v.get("webpage_url"), "thumbnail": v.get("thumbnail"), "duration": v.get("duration", 0)}
    return await loop.run_in_executor(None, search)

async def get_stream(url):
    loop = asyncio.get_running_loop()
    def extract():
        with yt_dlp.YoutubeDL(STREAM_OPTIONS) as ydl:
            i = ydl.extract_info(url, download=False)
            return {"url": i["url"], "title": i.get("title", "Unknown"), "thumbnail": i.get("thumbnail"), "duration": i.get("duration", 0)}
    return await loop.run_in_executor(None, extract)

# ============================================================
# AI RESOLVER
# ============================================================

async def ai_search(query):
    loop = asyncio.get_running_loop()
    def ask():
        resp = ai.chat.completions.create(
            model="google/gemini-2.5-flash",
            messages=[{"role": "system", "content": "Convert user music request into exact youtube search string. Return search string only."}, {"role": "user", "content": query}],
            temperature=0.2, max_tokens=80
        )
        return resp.choices[0].message.content.strip()
    return await loop.run_in_executor(None, ask)

async def resolve_music(query):
    query = query.strip()
    loop = asyncio.get_running_loop()
    if is_spotify(query) and "/playlist/" in query:
        names = await loop.run_in_executor(None, get_spotify_playlist, query)
        res = []
        for n in names[:100]:
            r = await search_youtube(n)
            if r: res.append(r)
        return res
    if is_spotify(query) and "/track/" in query:
        n = await loop.run_in_executor(None, get_spotify_track, query)
        r = await search_youtube(n)
        return [r] if r else []
    if is_youtube(query) or is_soundcloud(query):
        r = await search_youtube(query)
        return [r] if r else []

    q = await ai_search(query)
    r = await search_youtube(q)
    return [r] if r else []

# ============================================================
# VOICE JOIN
# ============================================================

async def ensure_voice(interaction_or_ctx):
    author = getattr(interaction_or_ctx, "user", getattr(interaction_or_ctx, "author", None))
    guild = interaction_or_ctx.guild

    if not author or not author.voice or not author.voice.channel:
        return None, "❌ You must be connected to a Voice Channel first!"

    channel = author.voice.channel
    voice = guild.voice_client

    if voice:
        if voice.channel != channel:
            await voice.move_to(channel)
    else:
        try:
            voice = await channel.connect(reconnect=True, timeout=15)
        except Exception as e:
            return None, f"❌ VC Connection Error: {e}"

    return voice, None

# ============================================================
# EMBED & CONTROLS
# ============================================================

def create_player_embed(guild_id, paused=False):
    song = current_song.get(guild_id)
    if not song:
        return discord.Embed(title="🎵 Music Player", description="Nothing is playing.")

    dur, pos = song.get("duration", 0), song_positions.get(guild_id, 0)
    embed = discord.Embed(title="🎶 Now Playing", description=f"**{song['title']}**", color=discord.Color.blurple())
    if song.get("thumbnail"): embed.set_thumbnail(url=song["thumbnail"])
    embed.add_field(name="Status", value="⏸️ Paused" if paused else "▶️ Playing", inline=True)
    embed.add_field(name="Loop", value=loop_modes.get(guild_id) or "Off", inline=True)
    embed.add_field(name="Progress", value=f"`{format_time(pos)}` {progress_bar(pos, dur)} `{format_time(dur)}`", inline=False)
    return embed

class MusicView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="Pause", emoji="⏯️", style=discord.ButtonStyle.primary)
    async def pause_play(self, interaction: discord.Interaction, button: discord.ui.Button):
        voice = interaction.guild.voice_client
        if not voice: return await interaction.response.send_message("❌ Not in VC.", ephemeral=True)
        if voice.is_playing(): voice.pause()
        elif voice.is_paused(): voice.resume()
        await interaction.response.edit_message(embed=create_player_embed(self.guild_id, voice.is_paused()), view=self)

    @discord.ui.button(label="Skip", emoji="⏭️", style=discord.ButtonStyle.danger)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        voice = interaction.guild.voice_client
        if voice and voice.is_playing(): voice.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        queues[self.guild_id] = []
        voice = interaction.guild.voice_client
        if voice: voice.stop()
        reset_inactivity_timer(self.guild_id)
        await interaction.response.edit_message(embed=discord.Embed(title="⏹️ Stopped", color=discord.Color.red()), view=None)

# ============================================================
# PLAYBACK LOOP
# ============================================================

async def play_next(guild_id):
    guild = bot.get_guild(guild_id)
    if not guild or not guild.voice_client: return

    queue = get_queue(guild_id)
    if not queue:
        reset_inactivity_timer(guild_id)
        return

    song = queue.pop(0)
    current_song[guild_id] = song
    song_positions[guild_id] = 0

    try:
        stream = await get_stream(song["webpage"])
        source = discord.FFmpegPCMAudio(stream["url"], **FFMPEG_OPTIONS)
        source = discord.PCMVolumeTransformer(source, volume=volume_levels.get(guild_id, 0.5))

        def after(error):
            if loop_modes.get(guild_id) == "current": queue.insert(0, song)
            elif loop_modes.get(guild_id) == "queue": queue.append(song)
            asyncio.run_coroutine_threadsafe(play_next(guild_id), bot.loop)

        guild.voice_client.play(source, after=after)
        msg = await guild.voice_client.channel.send(embed=create_player_embed(guild_id), view=MusicView(guild_id))
        player_messages[guild_id] = msg
    except Exception as e:
        print(f"[PLAY ERROR]: {e}")
        await play_next(guild_id)

async def play_music(target, query):
    guild_id = target.guild.id
    voice, err = await ensure_voice(target)
    if err:
        if hasattr(target, "followup"): await target.followup.send(err)
        else: await target.send(err)
        return

    songs = await resolve_music(query)
    if not songs:
        msg = "❌ Music search failed."
        if hasattr(target, "followup"): await target.followup.send(msg)
        else: await target.send(msg)
        return

    queue = get_queue(guild_id)
    queue.extend(songs)

    msg = f"🎵 Added **{songs[0]['title']}**" if len(songs) == 1 else f"🎵 Added **{len(songs)} tracks**"
    if hasattr(target, "followup"): await target.followup.send(msg)
    else: await target.send(msg)

    if not voice.is_playing() and not voice.is_paused():
        await play_next(guild_id)

# ============================================================
# COMMANDS
# ============================================================

@bot.event
async def on_message(message):
    if message.author.bot: return
    if bot.user and bot.user.mentioned_in(message):
        content = re.sub(rf"<@!?{bot.user.id}>", "", message.content).strip()
        if content.lower().startswith("p "):
            ctx = await bot.get_context(message)
            await play_music(ctx, content[2:].strip())
            return
    await bot.process_commands(message)

@bot.tree.command(name="play", description="Play music.")
async def play_cmd(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    await play_music(interaction, query)

@bot.tree.command(name="setpfp", description="Update Bot Avatar using File or URL.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(file="Upload image file", url="Direct image URL")
async def setpfp_cmd(interaction: discord.Interaction, file: discord.Attachment = None, url: str = None):
    await interaction.response.defer(ephemeral=True)
    if not file and not url:
        return await interaction.followup.send("❌ Please provide either an image file or an image URL.", ephemeral=True)

    image_bytes = None
    try:
        if file:
            image_bytes = await file.read()
        elif url:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as r:
                    if r.status == 200:
                        image_bytes = await r.read()

        if image_bytes:
            await bot.user.edit(avatar=image_bytes)
            await interaction.followup.send("✅ Avatar updated successfully!", ephemeral=True)
        else:
            await interaction.followup.send("❌ Failed to fetch image.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error updating avatar: `{e}`", ephemeral=True)

@bot.tree.command(name="setbanner", description="Update Bot Banner using File or URL.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(file="Upload image file", url="Direct image URL")
async def setbanner_cmd(interaction: discord.Interaction, file: discord.Attachment = None, url: str = None):
    await interaction.response.defer(ephemeral=True)
    if not file and not url:
        return await interaction.followup.send("❌ Please provide either an image file or an image URL.", ephemeral=True)

    image_bytes = None
    try:
        if file:
            image_bytes = await file.read()
        elif url:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as r:
                    if r.status == 200:
                        image_bytes = await r.read()

        if image_bytes:
            await bot.user.edit(banner=image_bytes)
            await interaction.followup.send("✅ Banner updated successfully!", ephemeral=True)
        else:
            await interaction.followup.send("❌ Failed to fetch banner image.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error updating banner: `{e}`", ephemeral=True)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

bot.run(DISCORD_TOKEN)

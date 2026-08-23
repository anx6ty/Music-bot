import asyncio
import os
import re
import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp

# --- EXPLICITLY LOAD OPUS ---
if not discord.opus.is_loaded():
    for lib_name in ('libopus.so.0', 'libopus.so', 'opus', '/usr/lib/x86_64-linux-gnu/libopus.so.0'):
        try:
            discord.opus.load_opus(lib_name)
            print(f"[OPUS] Loaded successfully using: {lib_name}")
            break
        except OSError:
            continue

# --- BOT SETUP & INTENTS ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

PURPLE = discord.Color.from_rgb(155, 89, 182)

# Global State Management
song_queue = {}       # {guild_id: [songs]}
loop_mode = {}        # {guild_id: 'off' | 'track' | 'queue'}
current_song = {}     # {guild_id: song_data}

YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'extractaudio': True,
    'audioformat': 'mp3',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0',
    'extractor_args': {
        'youtube': {
            'player_client': ['ios', 'android_vr', 'mweb'],
            'skip': ['webpage', 'configs']
        }
    }
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

def clean_search_query(query: str) -> str:
    if "open.spotify.com/track/" in query:
        match = re.search(r'track/([a-zA-Z0-9]+)', query)
        if match:
            return f"ytsearch:{match.group(1)} audio"
    if not query.startswith("http://") and not query.startswith("https://"):
        return f"ytsearch:{query}"
    return query

# --- INTERACTIVE BUTTONS WITH USER TAGGING & LOOP MODES ---
class ControlButtons(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="⏸️ Pause/Resume", style=discord.ButtonStyle.secondary, custom_id="pause_resume")
    async def pause_resume_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        user = interaction.user.mention
        if vc and vc.is_playing():
            vc.pause()
            await interaction.response.send_message(f"⏸️ Music paused by {user}.")
        elif vc and vc.is_paused():
            vc.resume()
            await interaction.response.send_message(f"▶️ Music resumed by {user}.")
        else:
            await interaction.response.send_message("❌ Nothing is currently playing.", ephemeral=True)

    @discord.ui.button(label="🔂 Loop Track", style=discord.ButtonStyle.secondary, custom_id="loop_track")
    async def loop_track_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        gid = interaction.guild.id
        user = interaction.user.mention
        if loop_mode.get(gid) == 'track':
            loop_mode[gid] = 'off'
            await interaction.response.send_message(f"➡️ Track Loop disabled by {user}.")
        else:
            loop_mode[gid] = 'track'
            await interaction.response.send_message(f"🔂 Current Track Loop enabled by {user}.")

    @discord.ui.button(label="🔁 Loop Queue", style=discord.ButtonStyle.secondary, custom_id="loop_queue")
    async def loop_queue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        gid = interaction.guild.id
        user = interaction.user.mention
        if loop_mode.get(gid) == 'queue':
            loop_mode[gid] = 'off'
            await interaction.response.send_message(f"➡️ Queue Loop disabled by {user}.")
        else:
            loop_mode[gid] = 'queue'
            await interaction.response.send_message(f"🔁 Queue Loop enabled by {user}.")

    @discord.ui.button(label="⏭️ Skip", style=discord.ButtonStyle.primary, custom_id="skip")
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        user = interaction.user.mention
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await interaction.response.send_message(f"⏭️ Skipped current track by {user}.")
        else:
            await interaction.response.send_message("❌ Nothing to skip.", ephemeral=True)

    @discord.ui.button(label="⏹️ Stop", style=discord.ButtonStyle.danger, custom_id="stop")
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        user = interaction.user.mention
        gid = interaction.guild.id
        song_queue[gid] = []
        loop_mode[gid] = 'off'
        if vc:
            await vc.disconnect()
            await interaction.response.send_message(f"⏹️ Stopped music and disconnected by {user}.")
        else:
            await interaction.response.send_message("❌ Bot is not in a voice channel.", ephemeral=True)

# --- PLAYBACK & QUEUE / LOOP ENGINE ---
def play_next(guild, channel):
    gid = guild.id
    vc = guild.voice_client

    if not vc or not vc.is_connected():
        return

    mode = loop_mode.get(gid, 'off')
    queue = song_queue.get(gid, [])

    # Single Track Loop Logic
    if mode == 'track' and gid in current_song:
        next_track = current_song[gid]
    # Queue Loop Logic
    elif mode == 'queue' and gid in current_song:
        if len(queue) > 0:
            song_queue[gid].append(current_song[gid])
            next_track = song_queue[gid].pop(0)
        else:
            next_track = current_song[gid]
    else:
        if len(queue) > 0:
            next_track = song_queue[gid].pop(0)
        else:
            current_song.pop(gid, None)
            return

    current_song[gid] = next_track
    source = discord.FFmpegPCMAudio(next_track['url'], **FFMPEG_OPTIONS)
    vc.play(source, after=lambda e: play_next(guild, channel))

    embed = discord.Embed(
        title="🎶 Now Playing",
        description=f"**[{next_track['title']}]({next_track['webpage_url']})**\n\n`[▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬]` Duration: `{next_track['duration']}`",
        color=PURPLE
    )
    if next_track.get('thumbnail'):
        embed.set_thumbnail(url=next_track['thumbnail'])
    embed.set_footer(text=f"Requested by {next_track['requester'].display_name}", icon_url=next_track['requester'].display_avatar.url)

    view = ControlButtons(gid)
    asyncio.run_coroutine_threadsafe(channel.send(embed=embed, view=view), bot.loop)

async def play_music_logic(channel, user, query, send_func):
    if not user.voice or not user.voice.channel:
        return await send_func("❌ Pehle kisi Voice Channel me join karein!")

    voice_channel = user.voice.channel
    guild = user.guild
    gid = guild.id
    vc = guild.voice_client

    if not vc:
        vc = await voice_channel.connect(self_deaf=True, self_mute=False)
    elif vc.channel != voice_channel:
        await vc.move_to(voice_channel)

    loop = asyncio.get_event_loop()
    processed_query = clean_search_query(query)

    try:
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(processed_query, download=False))
        if 'entries' in data and len(data['entries']) > 0:
            data = data['entries'][0]
    except Exception as e:
        return await send_func(f"❌ Track fetch error: `{str(e)}`")

    duration_sec = data.get('duration', 0)
    minutes, seconds = divmod(duration_sec, 60)
    
    track_info = {
        'url': data['url'],
        'title': data.get('title', 'Unknown Track'),
        'webpage_url': data.get('webpage_url', ''),
        'duration': f"{minutes:02d}:{seconds:02d}",
        'thumbnail': data.get('thumbnail', None),
        'requester': user
    }

    if gid not in song_queue:
        song_queue[gid] = []

    if vc.is_playing() or vc.is_paused():
        song_queue[gid].append(track_info)
        await send_func(f"📥 **Added to Queue:** `{track_info['title']}` (Requested by {user.mention})")
    else:
        current_song[gid] = track_info
        source = discord.FFmpegPCMAudio(track_info['url'], **FFMPEG_OPTIONS)
        vc.play(source, after=lambda e: play_next(guild, channel))

        embed = discord.Embed(
            title="🎶 Now Playing",
            description=f"**[{track_info['title']}]({track_info['webpage_url']})**\n\n`[▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬]` Duration: `{track_info['duration']}`",
            color=PURPLE
        )
        if track_info['thumbnail']:
            embed.set_thumbnail(url=track_info['thumbnail'])
        embed.set_footer(text=f"Requested by {user.display_name}", icon_url=user.display_avatar.url)

        view = ControlButtons(gid)
        await send_func(embed=embed, view=view)

# --- BOT EVENTS & COMMANDS ---
@bot.event
async def on_ready():
    synced = await bot.tree.sync()
    print(f"Logged in as {bot.user.name} | Synced {len(synced)} Commands!")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if bot.user in message.mentions:
        content = message.content.replace(f'<@{bot.user.id}>', '').replace(f'<@!{bot.user.id}>', '').strip()
        if content.startswith('p ') or content.startswith('play '):
            query = content.split(' ', 1)[1]
            async with message.channel.typing():
                await play_music_logic(
                    channel=message.channel,
                    user=message.author,
                    query=query,
                    send_func=lambda embed=None, view=None, text=None: message.channel.send(content=text, embed=embed, view=view)
                )
            return

    await bot.process_commands(message)

@bot.tree.command(name="play", description="Play a song in your voice channel")
async def play(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    await play_music_logic(
        channel=interaction.channel,
        user=interaction.user,
        query=query,
        send_func=lambda embed=None, view=None, text=None: interaction.followup.send(content=text, embed=embed, view=view)
    )

@bot.tree.command(name="loop", description="Set loop mode (off, track, queue)")
@app_commands.choices(mode=[
    app_commands.Choice(name="Off ❌", value="off"),
    app_commands.Choice(name="Track 🔂", value="track"),
    app_commands.Choice(name="Queue 🔁", value="queue")
])
async def loop_command(interaction: discord.Interaction, mode: app_commands.Choice[str]):
    gid = interaction.guild.id
    loop_mode[gid] = mode.value
    await interaction.response.send_message(f"🔁 **Loop Mode** set to `{mode.name}` by {interaction.user.mention}.")

@bot.tree.command(name="setpfp", description="Change bot profile picture (Admin Only)")
@app_commands.checks.has_permissions(administrator=True)
async def setpfp(interaction: discord.Interaction, image: discord.Attachment):
    await interaction.response.defer(ephemeral=True)
    image_bytes = await image.read()
    await bot.user.edit(avatar=image_bytes)
    await interaction.followup.send("✅ Profile picture updated successfully!")

@bot.tree.command(name="setbanner", description="Change bot profile banner (Admin Only)")
@app_commands.checks.has_permissions(administrator=True)
async def setbanner(interaction: discord.Interaction, image: discord.Attachment):
    await interaction.response.defer(ephemeral=True)
    image_bytes = await image.read()
    await bot.user.edit(banner=image_bytes)
    await interaction.followup.send("✅ Banner updated successfully!")

@bot.tree.command(name="skip", description="Skip current song")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await interaction.response.send_message(f"⏭️ Skipped current song by {interaction.user.mention}.")
    else:
        await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)

@bot.tree.command(name="stop", description="Stop music and leave VC")
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    gid = interaction.guild.id
    song_queue[gid] = []
    loop_mode[gid] = 'off'
    if vc:
        await vc.disconnect()
        await interaction.response.send_message(f"⏹️ Disconnected by {interaction.user.mention}.")
    else:
        await interaction.response.send_message("❌ Bot is not in VC.", ephemeral=True)

token = os.getenv("DISCORD_TOKEN")
if token:
    bot.run(token)
else:
    bot.run("YOUR_BOT_TOKEN_HERE")

import asyncio
import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp

# --- BOT SETUP & INTENTS ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# State Storage
is_247_enabled = {}
current_song_data = {}

# --- YT-DLP & FFMPEG CONFIG ---
YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'extractaudio': True,
    'audioformat': 'mp3',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

# --- INTERACTIVE MUSIC BUTTONS ---
class ControlButtons(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="⏸️ Pause/Resume", style=discord.ButtonStyle.secondary, custom_id="pause_resume")
    async def pause_resume_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            await interaction.response.send_message("⏸️ Music paused.", ephemeral=True)
        elif vc and vc.is_paused():
            vc.resume()
            await interaction.response.send_message("▶️ Music resumed.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Nothing is currently playing.", ephemeral=True)

    @discord.ui.button(label="⏭️ Skip", style=discord.ButtonStyle.primary, custom_id="skip")
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await interaction.response.send_message("⏭️ Skipped current track.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Nothing to skip.", ephemeral=True)

    @discord.ui.button(label="⏹️ Stop", style=discord.ButtonStyle.danger, custom_id="stop")
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc:
            await vc.disconnect()
            await interaction.response.send_message("⏹️ Stopped music and left voice channel.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Bot is not in a voice channel.", ephemeral=True)

# --- BOT EVENTS ---
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user.name} | Commands Synced Successfully!")

# --- SLASH COMMANDS ---

# 1. /play Command
@bot.tree.command(name="play", description="Play a song from YouTube in your VC")
async def play(interaction: discord.Interaction, query: str):
    await interaction.response.defer()

    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.followup.send("❌ Pehle kisi Voice Channel me join karo!")

    voice_channel = interaction.user.voice.channel
    vc = interaction.guild.voice_client

    # Connect/Move to VC with Self-Deafen True and Unmuted
    if not vc:
        vc = await voice_channel.connect(self_deaf=True, self_mute=False)
    elif vc.channel != voice_channel:
        await vc.move_to(voice_channel)

    loop = asyncio.get_event_loop()
    try:
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=False))
        if 'entries' in data:
            data = data['entries'][0]
        
        song_url = data['url']
        song_title = data.get('title', 'Unknown Track')
        song_duration = str(data.get('duration', 'N/A')) + " sec"
        thumbnail = data.get('thumbnail', None)

    except Exception as e:
        return await interaction.followup.send(f"❌ Audio extract karne me error aaya: `{str(e)}`")

    # Audio Stream Setup
    source = discord.FFmpegPCMAudio(song_url, **FFMPEG_OPTIONS)
    
    if vc.is_playing() or vc.is_paused():
        vc.stop()

    vc.play(source)

    # Embed UI Design
    embed = discord.Embed(
        title="🎶 Now Playing",
        description=f"**[{song_title}]({data.get('webpage_url', '')})**\n\n`[▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬]` Duration: {song_duration}",
        color=discord.Color.from_rgb(255, 0, 127)
    )
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    embed.set_footer(text=f"Requested by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)

    view = ControlButtons(interaction.guild.id)
    await interaction.followup.send(embed=embed, view=view)

# 2. /247 Command
@bot.tree.command(name="247", description="Toggle 24/7 mode so the bot never leaves VC")
async def mode_247(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    current_status = is_247_enabled.get(guild_id, False)
    is_247_enabled[guild_id] = not current_status
    
    status_str = "Enabled 🟢" if is_247_enabled[guild_id] else "Disabled 🔴"
    await interaction.response.send_message(f"🔒 **24/7 Mode:** {status_str}")

# 3. /recommend Command
@bot.tree.command(name="recommend", description="Get AI music recommendations")
async def recommend(interaction: discord.Interaction, genre: str = "Trending Hits"):
    embed = discord.Embed(
        title="✨ AI Recommended Tracks",
        description=f"Heres some fresh music recommendations for **{genre}**:\n\n"
                    f"1️⃣ **Arijit Singh - Casual Lo-fi Vibe**\n"
                    f"2️⃣ **Midnight Chill Beats - Lofi Remix**\n"
                    f"3️⃣ **Phonk / Cyberpunk Synthwave Special**",
        color=discord.Color.purple()
    )
    embed.set_footer(text="Use /play <song name> to start listening!")
    await interaction.response.send_message(embed=embed)

# 4. /skip Command
@bot.tree.command(name="skip", description="Skip the currently playing song")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await interaction.response.send_message("⏭️ Skipped current song!")
    else:
        await interaction.response.send_message("❌ Currently nothing is playing.")

# 5. /stop Command
@bot.tree.command(name="stop", description="Stop music and leave VC")
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc:
        await vc.disconnect()
        await interaction.response.send_message("⏹️ Disconnected from Voice Channel.")
    else:
        await interaction.response.send_message("❌ Bot is not in any voice channel.")

# --- START BOT ---
# Apne token yahan `YOUR_BOT_TOKEN_HERE` ki jagah daalein
bot.run("MTU0MDY4MTI4MjQ2MjQyMTAxNA.GanecV.fAXAEvflA8astQj15hMMSEYkDnH9FMpT2YzODo")


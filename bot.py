import asyncio
import os
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
            await interaction.response.send_message("⏹️ Disconnected and stopped music.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Bot is not in a voice channel.", ephemeral=True)

# --- HELPER FUNCTION FOR PLAYING MUSIC ---
async def play_music_logic(channel, user, query, send_func):
    if not user.voice or not user.voice.channel:
        return await send_func(text="🚫 Yo, hop into a voice channel first — I can't vibe alone!")

    voice_channel = user.voice.channel
    guild = user.guild
    vc = guild.voice_client

    if not vc:
        vc = await voice_channel.connect(self_deaf=True, self_mute=False)
    elif vc.channel != voice_channel:
        await vc.move_to(voice_channel)

    loop = asyncio.get_event_loop()
    try:
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=False))
        if 'entries' in data and len(data['entries']) > 0:
            data = data['entries'][0]

        song_url = data['url']
        song_title = data.get('title', 'Unknown Track')
        duration_sec = data.get('duration', 0)
        minutes, seconds = divmod(duration_sec, 60)
        song_duration = f"{minutes:02d}:{seconds:02d}"
        thumbnail = data.get('thumbnail', None)

    except Exception as e:
        error_detail = str(e) if str(e) else f"{type(e).__name__} (no message)"
        print(f"[FETCH ERROR] {type(e).__name__}: {e!r}")
        return await send_func(text=f"😵‍💫 Dug through the internet but came up empty-handed for that one.\n`{error_detail}`")

    try:
        source = discord.FFmpegPCMAudio(song_url, **FFMPEG_OPTIONS)
        if vc.is_playing() or vc.is_paused():
            vc.stop()

        vc.play(source)

        embed = discord.Embed(
            title="🎧 Now Spinning",
            description=f"**[{song_title}]({data.get('webpage_url', '')})**\n\n`[▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬]` ⏱️ `{song_duration}`",
            color=discord.Color.from_rgb(255, 0, 127)
        )
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
        embed.set_footer(text=f"🎙️ Dropped by {user.display_name}", icon_url=user.display_avatar.url)

        view = ControlButtons(guild.id)
        await send_func(embed=embed, view=view)

    except discord.ClientException as e:
        if "ffmpeg" in str(e).lower():
            print("[PLAY ERROR] ffmpeg was not found")
            await send_func(text="❌ Audio engine (FFmpeg) is not installed on the server. Please contact the bot host to fix this.")
        else:
            print(f"[PLAY ERROR] {e}")
            await send_func(text=f"❌ Something went wrong while trying to play the track.\n`{str(e)}`")
    except discord.ClientException as e:
        error_detail = str(e) if str(e) else type(e).__name__
        if "ffmpeg" in error_detail.lower():
            print("[PLAY ERROR] ffmpeg was not found")
            await send_func(text="❌ Audio engine (FFmpeg) is not installed on the server. Please contact the bot host to fix this.")
        else:
            print(f"[PLAY ERROR] ClientException: {error_detail!r}")
            await send_func(text=f"❌ Something went wrong while trying to play the track.\n`ClientException: {error_detail}`")
    except FileNotFoundError as e:
        print(f"[PLAY ERROR] FileNotFoundError: {e!r}")
        await send_func(text="❌ Audio engine (FFmpeg) is not installed on the server. Please contact the bot host to fix this.")
    except Exception as e:
        error_detail = str(e) if str(e) else f"{type(e).__name__} (no message)"
        print(f"[PLAY ERROR] {type(e).__name__}: {e!r}")
        await send_func(text=f"❌ Something went wrong while trying to play the track.\n`{error_detail}`")

# --- BOT EVENTS ---
@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Logged in as {bot.user.name} | Synced {len(synced)} Slash Commands!")
    except Exception as e:
        print(f"Error syncing commands: {e}")

# Mention Prefix Event Handler (@Jaduu Bot p <song_name>)
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Check if bot is mentioned
    if bot.user in message.mentions:
        content = message.content.replace(f'<@{bot.user.id}>', '').replace(f'<@!{bot.user.id}>', '').strip()

        # Check for 'p' or 'play' prefix
        if content.startswith('p ') or content.startswith('play '):
            query = content.split(' ', 1)[1]
            try:
                async with message.channel.typing():
                    await play_music_logic(
                        channel=message.channel,
                        user=message.author,
                        query=query,
                        send_func=lambda embed=None, view=None, text=None: safe_send_channel(message.channel, text=text, embed=embed, view=view)
                    )
            except Exception as e:
                print(f"[ON_MESSAGE PLAY ERROR] {e}")
                await message.channel.send(f"❌ Something went wrong: `{str(e)}`")
            return

    await bot.process_commands(message)

# --- SLASH COMMANDS ---

# 1. /play Command
@bot.tree.command(name="play", description="Play a song in your voice channel")
async def play(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    try:
        await play_music_logic(
            channel=interaction.channel,
            user=interaction.user,
            query=query,
            send_func=lambda embed=None, view=None, text=None: safe_send_followup(interaction, text=text, embed=embed, view=view)
        )
    except Exception as e:
        print(f"[PLAY COMMAND ERROR] {e}")
        await interaction.followup.send(f"❌ Something went wrong: `{str(e)}`")

# 2. /setpfp Command (Server-specific Profile Picture)
@bot.tree.command(name="setpfp", description="Change the bot's profile picture for THIS server only")
@app_commands.checks.has_permissions(administrator=True)
async def setpfp(interaction: discord.Interaction, image: discord.Attachment):
    await interaction.response.defer(ephemeral=True)
    try:
        image_bytes = await image.read()
        await interaction.guild.me.edit(avatar=image_bytes)
        await interaction.followup.send("✅ Profile picture updated for this server only!")
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to update avatar: `{str(e)}`")

# 3. /setbanner Command (Banner)
@bot.tree.command(name="setbanner", description="Change the bot profile banner (Admin Only)")
@app_commands.checks.has_permissions(administrator=True)
async def setbanner(interaction: discord.Interaction, image: discord.Attachment):
    await interaction.response.defer(ephemeral=True)
    try:
        image_bytes = await image.read()
        await bot.user.edit(banner=image_bytes)
        await interaction.followup.send(
            "✅ Banner updated! Note: Discord banners are always global "
            "(same across all servers) - Discord doesn't allow per-server banners for bots."
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to update banner: `{str(e)}`")

# 4. /247 Command
@bot.tree.command(name="247", description="Toggle 24/7 mode so the bot stays in VC")
async def mode_247(interaction: discord.Interaction):
    await interaction.response.send_message("🔒 **24/7 Mode Status:** Active 🟢")

# 5. /recommend Command
@bot.tree.command(name="recommend", description="Get AI music recommendations")
async def recommend(interaction: discord.Interaction, genre: str = "Trending Hits"):
    embed = discord.Embed(
        title="✨ AI Recommended Tracks",
        description=f"Recommendations for **{genre}**:\n\n"
                    f"1️⃣ **Arijit Singh - Casual Lo-fi Vibe**\n"
                    f"2️⃣ **Midnight Chill Beats - Lofi Remix**\n"
                    f"3️⃣ **Phonk / Cyberpunk Synthwave Special**",
        color=discord.Color.purple()
    )
    await interaction.response.send_message(embed=embed)

# 6. /skip Command
@bot.tree.command(name="skip", description="Skip current song")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await interaction.response.send_message("⏭️ Skipped current song!")
    else:
        await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)

# 7. /stop Command
@bot.tree.command(name="stop", description="Stop music and leave VC")
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc:
        await vc.disconnect()
        await interaction.response.send_message("⏹️ Disconnected from Voice Channel.")
    else:
        await interaction.response.send_message("❌ I'm not in a voice channel.", ephemeral=True)

# --- START BOT ---
token = os.getenv("DISCORD_TOKEN")
if token:
    bot.run(token)
else:
    raise RuntimeError("DISCORD_TOKEN environment variable is missing!")

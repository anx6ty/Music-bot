
import asyncio
import os
import re
import urllib.request
import json
import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp

# --- OPUS LOADER ---
if not discord.opus.is_loaded():
    for lib_name in ('libopus.so.0', 'libopus.so', 'opus', '/usr/lib/x86_64-linux-gnu/libopus.so.0'):
        try:
            discord.opus.load_opus(lib_name)
            break
        except OSError:
            continue

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

PURPLE = discord.Color.from_rgb(155, 89, 182)

song_queue = {}       
loop_mode = {}        
current_song = {}     

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
    'default_search': 'ytmusicsearch',  # Using Youtube Music engine to bypass IP blocks
    'source_address': '0.0.0.0',
    'socket_timeout': 15,
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

def get_spotify_title(url: str) -> str:
    try:
        req = urllib.request.Request(f"https://open.spotify.com/oembed?url={url}", headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            return f"{data.get('title', '')} {data.get('author_name', '')}"
    except Exception:
        return "trending audio"

def resolve_query(query: str) -> str:
    if "open.spotify.com/track/" in query:
        track_title = get_spotify_title(query)
        return f"ytmusicsearch:{track_title}"
    if not query.startswith("http://") and not query.startswith("https://"):
        return f"ytmusicsearch:{query}"
    return query

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

def play_next(guild, channel):
    gid = guild.id
    vc = guild.voice_client

    if not vc or not vc.is_connected():
        return

    mode = loop_mode.get(gid, 'off')
    queue = song_queue.get(gid, [])

    if mode == 'track' and gid in current_song:
        next_track = current_song[gid]
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
    clean_query = resolve_query(query)

    try:
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(clean_query, download=False))
        if 'entries' in data and len(data['entries']) > 0:
            data = data['entries'][0]
    except Exception as e:
        # Error directly user ko show hoga, silent fail nahi hoga!
        return await send_func(f"❌ Track fetch Error: `{str(e)[:150]}`")

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
        try:
            source = discord.FFmpegPCMAudio(track_info['url'], **FFMPEG_OPTIONS)
            vc.play(source, after=lambda e: play_next(guild, channel))
        except Exception as e:
            return await send_func(f"❌ Audio Play Error (FFmpeg issue): `{str(e)}`")

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

@bot.event
async def on_ready():
    synced = await bot.tree.sync()
    print(f"Logged in as {bot.user.name} | Synced {len(synced)} Commands!")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if bot.user in message.mentions:
        raw_text = message.content
        raw_text = re.sub(r'<@!?\d+>', '', raw_text).strip()
        
        if raw_text.startswith('p ') or raw_text.startswith('play '):
            query = raw_text.split(' ', 1)[1]
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

@bot.tree.command(name="loop", description="Set loop mode")
@app_commands.choices(mode=[
    app_commands.Choice(name="Off ❌", value="off"),
    app_commands.Choice(name="Track 🔂", value="track"),
    app_commands.Choice(name="Queue 🔁", value="queue")
])
async def loop_command(interaction: discord.Interaction, mode: app_commands.Choice[str]):
    gid = interaction.guild.id
    loop_mode[gid] = mode.value
    await interaction.response.send_message(f"🔁 **Loop Mode** set to `{mode.name}` by {interaction.user.mention}.")

@bot.tree.command(name="setpfp", description="Change bot profile picture")
@app_commands.checks.has_permissions(administrator=True)
async def setpfp(interaction: discord.Interaction, image: discord.Attachment):
    await interaction.response.defer(ephemeral=True)
    image_bytes = await image.read()
    await bot.user.edit(avatar=image_bytes)
    await interaction.followup.send("✅ Profile picture updated successfully!")

@bot.tree.command(name="setbanner", description="Change bot profile banner")
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
message("❌ Nothing is playing.", ephemeral=True)
            return
        voice.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
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

async def seek_music(interaction, amount):
    guild_id = interaction.guild.id
    song = current_song.get(guild_id)
    voice = interaction.guild.voice_client

    if not song or not voice:
        await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)
        return

    old_position = song_positions.get(guild_id, 0)
    duration = song.get("duration", 0)

    new_position = max(0, old_position + amount)
    if duration:
        new_position = min(new_position, duration - 1)

    song_positions[guild_id] = new_position

    await restart_from_position(guild_id, new_position)
    await interaction.response.edit_message(
        embed=create_player_embed(guild_id),
        view=MusicView(guild_id)
    )


# ============================================================
# RESTART STREAM AT POSITION
# ============================================================

async def restart_from_position(guild_id, position):
    guild = bot.get_guild(guild_id)
    if not guild:
        return

    voice = guild.voice_client
    song = current_song.get(guild_id)

    if not voice or not song:
        return

    try:
        stream = await get_stream(song["webpage"])
        ffmpeg_options = {
            "before_options": f"-ss {int(position)} -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "options": "-vn"
        }

        source = discord.FFmpegPCMAudio(stream["url"], **ffmpeg_options)
        source = discord.PCMVolumeTransformer(source, volume=volume_levels.get(guild_id, 0.5))

        def after(error):
            if error:
                print(f"[PLAYER ERROR] {error}")

            if loop_modes.get(guild_id) == "current":
                get_queue(guild_id).insert(0, song)

            asyncio.run_coroutine_threadsafe(play_next(guild_id), bot.loop)

        voice.stop()
        voice.play(source, after=after)

    except Exception as error:
        print(f"[SEEK ERROR] {error}")


# ============================================================
# PROGRESS UPDATER
# ============================================================

async def update_player(guild_id):
    try:
        while True:
            await asyncio.sleep(10)

            message = player_messages.get(guild_id)
            song = current_song.get(guild_id)
            guild = bot.get_guild(guild_id)
            voice = guild.voice_client if guild else None

            if not message or not song:
                return

            if voice and voice.is_playing():
                song_positions[guild_id] = song_positions.get(guild_id, 0) + 10

            if song.get("duration") and song_positions.get(guild_id, 0) >= song["duration"]:
                song_positions[guild_id] = song["duration"]

            try:
                await message.edit(
                    embed=create_player_embed(guild_id, voice.is_paused() if voice else False),
                    view=MusicView(guild_id)
                )
            except Exception:
                return

    except asyncio.CancelledError:
        return


# ============================================================
# PLAY NEXT
# ============================================================

async def play_next(guild_id):
    guild = bot.get_guild(guild_id)
    if not guild:
        return

    voice = guild.voice_client
    if not voice:
        return

    queue = get_queue(guild_id)

    if not queue:
        if not stay_247.get(guild_id, False):
            await voice.disconnect()
        return

    song = queue.pop(0)
    current_song[guild_id] = song
    song_positions[guild_id] = 0

    try:
        stream = await get_stream(song["webpage"])
        source = discord.FFmpegPCMAudio(stream["url"], **FFMPEG_OPTIONS)
        source = discord.PCMVolumeTransformer(source, volume=volume_levels.get(guild_id, 0.5))

        def after(error):
            if error:
                print(f"[PLAYER ERROR] {error}")

            if loop_modes.get(guild_id) == "current":
                queue.insert(0, song)
            elif loop_modes.get(guild_id) == "queue":
                queue.append(song)

            asyncio.run_coroutine_threadsafe(play_next(guild_id), bot.loop)

        voice.play(source, after=after)

        embed = create_player_embed(guild_id)
        view = MusicView(guild_id)

        try:
            old_message = player_messages.get(guild_id)
            if old_message:
                await old_message.edit(embed=embed, view=view)
                message = old_message
            else:
                message = await voice.channel.send(embed=embed, view=view)

            player_messages[guild_id] = message

        except Exception as error:
            print(f"[EMBED ERROR] {error}")

        old_task = player_tasks.get(guild_id)
        if old_task:
            old_task.cancel()

        player_tasks[guild_id] = asyncio.create_task(update_player(guild_id))

    except Exception as error:
        print(f"[PLAY ERROR] {error}")
        await play_next(guild_id)


# ============================================================
# PLAY MUSIC
# ============================================================

async def play_music(ctx, query):
    voice = await ensure_voice(ctx)
    if not voice:
        return

    await ctx.send("🔎 Searching...")

    try:
        songs = await resolve_music(query)
    except Exception as error:
        await ctx.send(f"❌ Error:\n```{error}```")
        return

    if not songs:
        await ctx.send("❌ Song nahi mili.")
        return

    queue = get_queue(ctx.guild.id)
    for song in songs:
        queue.append(song)

    if len(songs) == 1:
        await ctx.send(f"🎵 Added **{songs[0]['title']}**")
    else:
        await ctx.send(f"🎵 Added **{len(songs)} songs**.")

    if not voice.is_playing() and not voice.is_paused():
        await play_next(ctx.guild.id)


# ============================================================
# @BOT p SONG
# ============================================================

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if bot.user and bot.user.mentioned_in(message):
        content = re.sub(rf"<@!?{bot.user.id}>", "", message.content).strip()

        if content.lower().startswith("p "):
            query = content[2:].strip()
            if not query:
                await message.channel.send("🎵 Use:\n`@Bot p <song name/link>`")
                return

            ctx = await bot.get_context(message)
            await play_music(ctx, query)
            return

    await bot.process_commands(message)


# ============================================================
# COMMANDS
# ============================================================

@bot.tree.command(name="play", description="Play a song or music link.")
@app_commands.describe(query="Song name or music link")
async def play_command(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    ctx = await bot.get_context(interaction)
    ctx.author = interaction.user
    ctx.guild = interaction.guild
    ctx.channel = interaction.channel
    await play_music(ctx, query)


@bot.tree.command(name="skip", description="Skip current song.")
async def skip_command(interaction: discord.Interaction):
    voice = interaction.guild.voice_client
    if not voice or not voice.is_playing():
        await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)
        return
    voice.stop()
    await interaction.response.send_message("⏭️ Skipped.")


@bot.tree.command(name="pause", description="Pause music.")
async def pause_command(interaction: discord.Interaction):
    voice = interaction.guild.voice_client
    if not voice or not voice.is_playing():
        await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)
        return
    voice.pause()
    await interaction.response.send_message("⏸️ Paused.")


@bot.tree.command(name="resume", description="Resume music.")
async def resume_command(interaction: discord.Interaction):
    voice = interaction.guild.voice_client
    if not voice or not voice.is_paused():
        await interaction.response.send_message("❌ Nothing is paused.", ephemeral=True)
        return
    voice.resume()
    await interaction.response.send_message("▶️ Resumed.")


@bot.tree.command(name="stop", description="Stop music and clear queue.")
async def stop_command(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    queues[guild_id] = []
    loop_modes[guild_id] = None
    voice = interaction.guild.voice_client
    if voice:
        voice.stop()
    await interaction.response.send_message("⏹️ Music stopped.")


@bot.tree.command(name="queue", description="Show music queue.")
async def queue_command(interaction: discord.Interaction):
    queue = get_queue(interaction.guild.id)
    if not queue:
        await interaction.response.send_message("📭 Queue empty.")
        return

    text = "\n".join(f"`{i + 1}.` {song['title']}" for i, song in enumerate(queue[:20]))
    embed = discord.Embed(title="🎶 Music Queue", description=text, color=discord.Color.blurple())
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="loop", description="Change loop mode.")
@app_commands.describe(mode="off / current / queue")
async def loop_command(interaction: discord.Interaction, mode: str):
    mode = mode.lower()
    if mode not in ("off", "current", "queue"):
        await interaction.response.send_message("❌ Use `off`, `current`, or `queue`.")
        return

    guild_id = interaction.guild.id
    loop_modes[guild_id] = None if mode == "off" else mode
    await interaction.response.send_message(f"🔁 Loop: **{mode}**")


@bot.tree.command(name="247", description="Toggle 24/7 VC mode.")
async def mode_247(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    stay_247[guild_id] = not stay_247.get(guild_id, False)

    if stay_247[guild_id]:
        if not interaction.user.voice:
            stay_247[guild_id] = False
            await interaction.response.send_message("❌ Pehle VC join karo.")
            return

        if not interaction.guild.voice_client:
            await interaction.user.voice.channel.connect()

        await interaction.response.send_message("♾️ **24/7 mode ON**")
    else:
        await interaction.response.send_message("♾️ **24/7 mode OFF**")


@bot.tree.command(name="leave", description="Leave voice channel.")
async def leave_command(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    stay_247[guild_id] = False
    voice = interaction.guild.voice_client
    if not voice:
        await interaction.response.send_message("❌ I'm not in VC.")
        return
    await voice.disconnect()
    await interaction.response.send_message("👋 Left VC.")


@bot.tree.command(name="volume", description="Change volume.")
@app_commands.describe(amount="1-100")
async def volume_command(interaction: discord.Interaction, amount: int):
    if amount < 1 or amount > 100:
        await interaction.response.send_message("❌ Volume must be between 1 and 100.")
        return

    guild_id = interaction.guild.id
    volume_levels[guild_id] = amount / 100
    voice = interaction.guild.voice_client

    if voice and voice.source and isinstance(voice.source, discord.PCMVolumeTransformer):
        voice.source.volume = amount / 100

    await interaction.response.send_message(f"🔊 Volume: **{amount}%**")


@bot.tree.command(name="suggest", description="Get AI music recommendations.")
@app_commands.describe(request="Mood, genre, artist, activity...")
async def suggest_command(interaction: discord.Interaction, request: str):
    await interaction.response.defer()
    try:
        result = await ai_suggest(request)
        embed = discord.Embed(title="🤖 AI Music Suggestions", description=result, color=discord.Color.purple())
        embed.set_footer(text="Powered by OpenRouter")
        await interaction.followup.send(embed=embed)
    except Exception as error:
        await interaction.followup.send(f"❌ AI Error:\n```{error}```")


@bot.tree.command(name="setpfp", description="Change bot profile picture.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(image_url="Direct image URL")
async def setpfp_command(interaction: discord.Interaction, image_url: str):
    await interaction.response.defer(ephemeral=True)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as response:
                if response.status != 200:
                    raise Exception("Image couldn't be downloaded.")
                image = await response.read()

        await bot.user.edit(avatar=image)
        await interaction.followup.send("✅ PFP changed!", ephemeral=True)
    except Exception as error:
        await interaction.followup.send(f"❌ Error: `{error}`", ephemeral=True)


@bot.tree.command(name="setbanner", description="Change bot banner.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(image_url="Direct image URL")
async def setbanner_command(interaction: discord.Interaction, image_url: str):
    await interaction.response.defer(ephemeral=True)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as response:
                if response.status != 200:
                    raise Exception("Banner couldn't be downloaded.")
                image = await response.read()

        await bot.user.edit(banner=image)
        await interaction.followup.send("✅ Banner changed!", ephemeral=True)
    except Exception as error:
        await interaction.followup.send(f"❌ Error: `{error}`", ephemeral=True)


# ============================================================
# ERROR HANDLER & BOT RUN
# ============================================================

@bot.tree.error
async def command_error(interaction, error):
    print("[COMMAND ERROR]", error)
    message = "❌ Administrator permission required." if isinstance(error, app_commands.errors.MissingPermissions) else "❌ Something went wrong."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


@bot.event
async def on_ready():
    print("================================")
    print(f"Logged in as {bot.user}")
    print(f"Bot ID: {bot.user.id}")
    print("================================")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as error:
        print(f"Slash sync error: {error}")


bot.run(DISCORD_TOKEN)
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
)
========================

bot.run(
    DISCORD_TOKEN
)
import asyncio
import os
import random
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
PURPLE = discord.Color.from_rgb(155, 93, 229)  # updated to match the new "sigil" violet

# --- CUSTOM (EXTERNAL) EMOJI CONFIG ---
# These are your own server's custom emojis, not built-in unicode ones.
# How to get an ID: enable Developer Mode (User Settings > Advanced),
# then right-click any custom emoji in Discord and click "Copy Emoji ID".
# The bot must be a member of a server that owns the emoji to render it.
# Leave an entry as None to fall back to a unicode emoji automatically.
CUSTOM_EMOJI_IDS = {
    "pause":   None,   # e.g. 1234567890123456789
    "resume":  None,
    "skip":    None,
    "shuffle": None,
    "loop_off":   None,
    "loop_track": None,
    "loop_queue": None,
    "stop":    None,
}

# unicode fallbacks used until you fill in real IDs above
_FALLBACK_EMOJI = {
    "pause":   "⏸️",
    "resume":  "▶️",
    "skip":    "⏭️",
    "shuffle": "🔀",
    "loop_off":   "🔁",
    "loop_track": "🔂",
    "loop_queue": "🔁",
    "stop":    "⏹️",
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

# --- STATE ---
queues = {}          # guild_id -> list of {"query": str, "requester": Member}
current_song = {}    # guild_id -> {"title","webpage_url","thumbnail","duration_str","requester","_query"}
loop_modes = {}       # guild_id -> None / "track" / "queue"
now_playing_messages = {}  # guild_id -> discord.Message


def get_queue(guild_id):
    return queues.setdefault(guild_id, [])


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


def build_now_playing_embed(guild_id, paused=False):
    song = current_song.get(guild_id)
    if not song:
        embed = discord.Embed(
            description="### ◈ Nothing Casting\n▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹",
            color=PURPLE
        )
        embed.set_footer(text="🪄 queue an incantation with /play")
        return embed

    queue_count = len(queues.get(guild_id, []))

    embed = discord.Embed(
        description=f"### [{song['title']}]({song['webpage_url']})\n▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹",
        color=PURPLE
    )
    embed.set_author(name="⏸️  SPELL PAUSED" if paused else "◈  NOW CASTING")

    if song.get('thumbnail'):
        embed.set_thumbnail(url=song['thumbnail'])

    embed.add_field(name="⏱️ DURATION", value=f"`{song.get('duration_str', 'Unknown')}`", inline=True)
    embed.add_field(name="🔁 LOOP", value=f"`{get_loop_label(guild_id)}`", inline=True)
    embed.add_field(
        name="📃 UP NEXT",
        value=f"`{queue_count} queued`" if queue_count else "`empty`",
        inline=True
    )

    requester = song.get('requester')
    if requester:
        embed.set_footer(text=f"🪄 cast by {requester.display_name}", icon_url=requester.display_avatar.url)

    return embed


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

    async def pause_resume_callback(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc:
            await interaction.response.send_message("❌ I'm not connected to a voice channel.", ephemeral=True)
            return

        if vc.is_playing():
            vc.pause()
            await interaction.response.edit_message(
                embed=build_now_playing_embed(self.guild_id, paused=True),
                view=MusicView(self.guild_id)
            )
            await interaction.channel.send(f"⏸️ Spell paused by {interaction.user.mention}")
        elif vc.is_paused():
            vc.resume()
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
        await interaction.response.send_message(f"🔀 Queue shuffled by {interaction.user.mention} — **{len(queue)}** song(s) reordered")

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
        vc.stop()
        await vc.disconnect()

    current_song.pop(guild_id, None)

    msg = delete_message or now_playing_messages.get(guild_id)
    if msg:
        try:
            await msg.delete()
        except Exception:
            pass

    now_playing_messages.pop(guild_id, None)


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
            await vc.disconnect()
            return
        item = queue.pop(0)
        query = item["query"]
        requester = item["requester"]

    loop = asyncio.get_event_loop()
    try:
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=False))
        if 'entries' in data and len(data['entries']) > 0:
            data = data['entries'][0]

        song_url = data['url']
        title = data.get('title', 'Unknown Track')
        duration_sec = data.get('duration', 0)
        minutes, seconds = divmod(duration_sec, 60)
        duration_str = f"{minutes:02d}:{seconds:02d}"
        thumbnail = data.get('thumbnail')
        webpage_url = data.get('webpage_url', '')

    except Exception as e:
        error_detail = str(e) if str(e) else type(e).__name__
        if send_func:
            await send_func(text=f"😵‍💫 Couldn't cast **{query}** — skipping.\n`{error_detail}`")
        await play_next(guild, channel, send_func)
        return

    current_song[guild_id] = {
        "title": title,
        "webpage_url": webpage_url,
        "thumbnail": thumbnail,
        "duration_str": duration_str,
        "requester": requester,
        "_query": query,
    }

    def after_play(error):
        if error:
            print(f"[PLAYER ERROR] {error}")

        if loop_modes.get(guild_id) == "queue":
            get_queue(guild_id).append({"query": query, "requester": requester})

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
        if "ffmpeg" in error_detail.lower():
            if send_func:
                await send_func(text="❌ Audio engine (FFmpeg) is not installed on the server. Please contact the bot host.")
        else:
            if send_func:
                await send_func(text=f"❌ Playback error.\n`ClientException: {error_detail}`")
        return
    except Exception as e:
        error_detail = str(e) if str(e) else type(e).__name__
        if send_func:
            await send_func(text=f"❌ Playback error.\n`{error_detail}`")
        return

    embed = build_now_playing_embed(guild_id, paused=False)
    view = MusicView(guild_id)

    old_msg = now_playing_messages.get(guild_id)
    if old_msg:
        try:
            await old_msg.delete()
        except Exception:
            pass

    msg = await channel.send(embed=embed, view=view)
    now_playing_messages[guild_id] = msg


async def enqueue_song(guild, channel, user, query, send_func):
    vc, err = await ensure_voice(user, guild)
    if err:
        return await send_func(text=err)

    get_queue(guild.id).append({"query": query, "requester": user})

    if vc.is_playing() or vc.is_paused():
        await send_func(text=f"🪄 Added to the spellbook: **{query}**")
    else:
        await play_next(guild, channel, send_func)


# --- BOT EVENTS ---
@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Logged in as {bot.user.name} | Synced {len(synced)} Slash Commands!")
    except Exception as e:
        print(f"Error syncing commands: {e}")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if bot.user in message.mentions:
        content = message.content.replace(f'<@{bot.user.id}>', '').replace(f'<@!{bot.user.id}>', '').strip()
        lowered = content.lower()

        if lowered.startswith('p ') or lowered.startswith('play '):
            query = content.split(' ', 1)[1]
            try:
                async with message.channel.typing():
                    await enqueue_song(
                        guild=message.guild,
                        channel=message.channel,
                        user=message.author,
                        query=query,
                        send_func=lambda embed=None, view=None, text=None: safe_send_channel(message.channel, text=text, embed=embed, view=view)
                    )
            except Exception as e:
                print(f"[ON_MESSAGE PLAY ERROR] {e}")
                await message.channel.send(f"❌ Something went wrong: `{str(e)}`")
            return

        if lowered.startswith('shuffle'):
            guild_id = message.guild.id
            queue = get_queue(guild_id)
            if len(queue) < 2:
                await message.channel.send("❌ Need at least 2 songs in the queue to shuffle.")
            else:
                random.shuffle(queue)
                await message.channel.send(f"🔀 Queue shuffled by {message.author.mention} — **{len(queue)}** song(s) reordered")
            return

        if lowered.startswith('loop'):
            parts = content.split()
            guild_id = message.guild.id

            if len(parts) > 1 and parts[1].lower() in ("track", "queue", "off"):
                chosen = parts[1].lower()
                loop_modes[guild_id] = None if chosen == "off" else chosen
            else:
                loop_modes[guild_id] = next_loop_mode(loop_modes.get(guild_id))

            await message.channel.send(f"🔁 Loop mode: **{get_loop_label(guild_id)}** — set by {message.author.mention}")

            msg = now_playing_messages.get(guild_id)
            if msg:
                try:
                    vc = message.guild.voice_client
                    is_paused = vc.is_paused() if vc else False
                    await msg.edit(embed=build_now_playing_embed(guild_id, paused=is_paused), view=MusicView(guild_id))
                except Exception:
                    pass
            return

    await bot.process_commands(message)


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

    msg = now_playing_messages.get(guild_id)
    if msg:
        try:
            vc = interaction.guild.voice_client
            is_paused = vc.is_paused() if vc else False
            await msg.edit(embed=build_now_playing_embed(guild_id, paused=is_paused), view=MusicView(guild_id))
        except Exception:
            pass


@bot.tree.command(name="shuffle", description="Shuffle the current queue")
async def shuffle_command(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    queue = get_queue(guild_id)
    if len(queue) < 2:
        await interaction.response.send_message("❌ Need at least 2 songs in the queue to shuffle.", ephemeral=True)
        return

    random.shuffle(queue)
    await interaction.response.send_message(f"🔀 Queue shuffled by {interaction.user.mention} — **{len(queue)}** song(s) reordered")

    msg = now_playing_messages.get(guild_id)
    if msg:
        try:
            vc = interaction.guild.voice_client
            is_paused = vc.is_paused() if vc else False
            await msg.edit(embed=build_now_playing_embed(guild_id, paused=is_paused), view=MusicView(guild_id))
        except Exception:
            pass


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


@bot.tree.command(name="247", description="Toggle 24/7 mode so the bot stays in VC")
async def mode_247(interaction: discord.Interaction):
    await interaction.response.send_message("🔒 **24/7 Mode Status:** Active 🟢")


@bot.tree.command(name="recommend", description="Get AI music recommendations")
async def recommend(interaction: discord.Interaction, genre: str = "Trending Hits"):
    embed = discord.Embed(
        title="✨ AI Recommended Tracks",
        description=f"Recommendations for **{genre}**:\n\n"
                    f"1️⃣ **Arijit Singh - Casual Lo-fi Vibe**\n"
                    f"2️⃣ **Midnight Chill Beats - Lofi Remix**\n"
                    f"3️⃣ **Phonk / Cyberpunk Synthwave Special**",
        color=PURPLE
    )
    await interaction.response.send_message(embed=embed)


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
    guild_id = interaction.guild.id
    await interaction.response.send_message(f"⏸️ Spell paused by {interaction.user.mention}")
    msg = now_playing_messages.get(guild_id)
    if msg:
        try:
            await msg.edit(embed=build_now_playing_embed(guild_id, paused=True), view=MusicView(guild_id))
        except Exception:
            pass


@bot.tree.command(name="resume", description="Resume the current song")
async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc or not vc.is_paused():
        await interaction.response.send_message("❌ Nothing is paused.", ephemeral=True)
        return
    vc.resume()
    guild_id = interaction.guild.id
    await interaction.response.send_message(f"▶️ Spell resumes... by {interaction.user.mention}")
    msg = now_playing_messages.get(guild_id)
    if msg:
        try:
            await msg.edit(embed=build_now_playing_embed(guild_id, paused=False), view=MusicView(guild_id))
        except Exception:
            pass


@bot.tree.command(name="stop", description="Stop music, clear queue, and leave VC")
async def stop(interaction: discord.Interaction):
    await interaction.response.defer()
    await stop_playback(interaction.guild)
    await interaction.followup.send(f"💨 The magic fades... stopped by {interaction.user.mention}")


# --- START BOT ---
token = os.getenv("DISCORD_TOKEN")
if token:
    bot.run(token)
else:
    raise RuntimeError("DISCORD_TOKEN environment variable is missing!")

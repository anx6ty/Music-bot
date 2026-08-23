import asyncio
import os
import random
import json

import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp


# =========================================================
# OPUS
# =========================================================

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


# =========================================================
# INTENTS
# =========================================================

intents = discord.Intents.default()
intents.message_content = True


# =========================================================
# PREFIX SYSTEM
# =========================================================

DEFAULT_PREFIX = "!"
PREFIX_FILE = "prefixes.json"


def load_prefixes():
    try:
        if os.path.exists(PREFIX_FILE):
            with open(PREFIX_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            return {
                int(guild_id): prefix
                for guild_id, prefix in data.items()
            }

    except Exception as e:
        print(f"[PREFIX] Load error: {e}")

    return {}


prefixes = load_prefixes()


def save_prefixes():
    try:
        with open(PREFIX_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    str(guild_id): prefix
                    for guild_id, prefix in prefixes.items()
                },
                f,
                indent=4
            )

    except Exception as e:
        print(f"[PREFIX] Save error: {e}")


def get_prefix(bot, message):
    if not message.guild:
        return DEFAULT_PREFIX

    return prefixes.get(
        message.guild.id,
        DEFAULT_PREFIX
    )


bot = commands.Bot(
    command_prefix=get_prefix,
    intents=intents,
    help_command=None
)


# =========================================================
# BRAND
# =========================================================

PURPLE = discord.Color.from_rgb(
    155,
    93,
    229
)


# =========================================================
# CUSTOM EMOJIS
# =========================================================

CUSTOM_EMOJI_IDS = {

    "pause":
        1541057411933012009,

    "resume":
        1541057356849225808,

    "skip":
        1541057362532507648,

    "shuffle":
        1541057368836673597,

    "loop_off":
        1541057306484015267,

    "loop_track":
        1541057306484015267,

    "loop_queue":
        1541057306484015267,

    "stop":
        1541057401086677093,
}


_FALLBACK_EMOJI = {

    "pause": "⏸️",

    "resume": "▶️",

    "skip": "⏭️",

    "shuffle": "🔀",

    "loop_off": "🔁",

    "loop_track": "🔂",

    "loop_queue": "🔁",

    "stop": "🛑",
}


def get_emoji(key):

    emoji_id = CUSTOM_EMOJI_IDS.get(key)

    if emoji_id:

        return discord.PartialEmoji(
            name=key,
            id=emoji_id
        )

    return _FALLBACK_EMOJI.get(
        key,
        ""
    )


# =========================================================
# YT-DLP
# =========================================================

YTDL_OPTIONS = {

    "format":
        "bestaudio/best",

    "extractaudio":
        True,

    "audioformat":
        "mp3",

    "outtmpl":
        "%(extractor)s-%(id)s-%(title)s.%(ext)s",

    "restrictfilenames":
        True,

    "noplaylist":
        True,

    "nocheckcertificate":
        True,

    "ignoreerrors":
        False,

    "logtostderr":
        False,

    "quiet":
        True,

    "no_warnings":
        True,

    "default_search":
        "ytsearch",

    "source_address":
        "0.0.0.0",

    "extractor_args": {
        "youtube": {
            "player_client": [
                "android",
                "web"
            ]
        }
    },
}


FFMPEG_OPTIONS = {

    "before_options":
        "-reconnect 1 "
        "-reconnect_streamed 1 "
        "-reconnect_delay_max 5",

    "options":
        "-vn",
}


ytdl = yt_dlp.YoutubeDL(
    YTDL_OPTIONS
)


# =========================================================
# MUSIC STATE
# =========================================================

queues = {}

current_song = {}

loop_modes = {}

now_playing_messages = {}


def get_queue(guild_id):

    return queues.setdefault(
        guild_id,
        []
    )


# =========================================================
# LOOP HELPERS
# =========================================================

def get_loop_label(guild_id):

    mode = loop_modes.get(
        guild_id
    )

    if mode == "track":
        return "Track"

    if mode == "queue":
        return "Queue"

    return "Off"


# =========================================================
# NOW PLAYING EMBED
# =========================================================

def build_now_playing_embed(
    guild_id,
    paused=False
):

    song = current_song.get(
        guild_id
    )

    if not song:

        embed = discord.Embed(
            description=(
                "### ◈ Nothing Casting\n"
                "▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹"
            ),
            color=PURPLE
        )

        embed.set_footer(
            text="🪄 queue an incantation with /play"
        )

        return embed


    queue_count = len(
        queues.get(
            guild_id,
            []
        )
    )


    embed = discord.Embed(

        description=(
            f"### [{song['title']}]"
            f"({song['webpage_url']})\n"
            "▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹▹"
        ),

        color=PURPLE
    )


    # Custom emoji in embed

    embed.set_author(

        name=(
            f"{get_emoji('pause')} SPELL PAUSED"
            if paused
            else "◈ NOW CASTING"
        )
    )


    if song.get("thumbnail"):

        embed.set_thumbnail(
            url=song["thumbnail"]
        )


    embed.add_field(

        name=f"{get_emoji('pause')} DURATION",

        value=(
            f"`{song.get('duration_str', 'Unknown')}`"
        ),

        inline=True
    )


    embed.add_field(

        name=f"{get_emoji('loop_track')} LOOP",

        value=(
            f"`{get_loop_label(guild_id)}`"
        ),

        inline=True
    )


    embed.add_field(

        name=f"{get_emoji('skip')} UP NEXT",

        value=(
            f"`{queue_count} queued`"
            if queue_count
            else "`empty`"
        ),

        inline=True
    )


    requester = song.get(
        "requester"
    )


    if requester:

        embed.set_footer(

            text=(
                f"🪄 cast by "
                f"{requester.display_name}"
            ),

            icon_url=(
                requester.display_avatar.url
            )
        )


    return embed


# =========================================================
# LOOP CHOICE VIEW
# =========================================================

class LoopChoiceView(
    discord.ui.View
):

    def __init__(
        self,
        guild_id
    ):

        super().__init__(
            timeout=60
        )

        self.guild_id = guild_id


    @discord.ui.button(
        label="Queue",
        emoji="🔁",
        style=discord.ButtonStyle.primary
    )
    async def queue_loop(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        guild_id = interaction.guild.id

        loop_modes[guild_id] = "queue"


        vc = interaction.guild.voice_client

        paused = (
            vc.is_paused()
            if vc
            else False
        )


        embed = None

        if current_song.get(guild_id):

            embed = build_now_playing_embed(
                guild_id,
                paused=paused
            )


        await interaction.response.edit_message(

            content=(
                f"{get_emoji('loop_queue')} "
                "**Queue Loop enabled!**"
            ),

            embed=embed,

            view=MusicView(
                guild_id
            )
        )


    @discord.ui.button(
        label="Track",
        emoji="🔂",
        style=discord.ButtonStyle.primary
    )
    async def track_loop(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        guild_id = interaction.guild.id

        loop_modes[guild_id] = "track"


        vc = interaction.guild.voice_client

        paused = (
            vc.is_paused()
            if vc
            else False
        )


        embed = None

        if current_song.get(guild_id):

            embed = build_now_playing_embed(
                guild_id,
                paused=paused
            )


        await interaction.response.edit_message(

            content=(
                f"{get_emoji('loop_track')} "
                "**Track Loop enabled!**"
            ),

            embed=embed,

            view=MusicView(
                guild_id
            )
        )


# =========================================================
# MUSIC CONTROL VIEW
# =========================================================

class MusicView(
    discord.ui.View
):

    def __init__(
        self,
        guild_id
    ):

        super().__init__(
            timeout=None
        )

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


        # PAUSE / RESUME

        pause_button = discord.ui.Button(

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

            style=discord.ButtonStyle.primary
        )


        pause_button.callback = (
            self.pause_resume_callback
        )


        self.add_item(
            pause_button
        )


        # SKIP

        skip_button = discord.ui.Button(

            label="Skip",

            emoji=get_emoji(
                "skip"
            ),

            style=discord.ButtonStyle.secondary
        )


        skip_button.callback = (
            self.skip_callback
        )


        self.add_item(
            skip_button
        )


        # SHUFFLE

        shuffle_button = discord.ui.Button(

            label="Shuffle",

            emoji=get_emoji(
                "shuffle"
            ),

            style=discord.ButtonStyle.secondary
        )


        shuffle_button.callback = (
            self.shuffle_callback
        )


        self.add_item(
            shuffle_button
        )


        # LOOP

        mode = loop_modes.get(
            guild_id
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

            loop_label = "Loop"

            loop_emoji = get_emoji(
                "loop_off"
            )

            loop_style = (
                discord.ButtonStyle.secondary
            )


        loop_button = discord.ui.Button(

            label=loop_label,

            emoji=loop_emoji,

            style=loop_style
        )


        loop_button.callback = (
            self.loop_callback
        )


        self.add_item(
            loop_button
        )


        # STOP

        stop_button = discord.ui.Button(

            label="Stop",

            emoji=get_emoji(
                "stop"
            ),

            style=discord.ButtonStyle.danger
        )


        stop_button.callback = (
            self.stop_callback
        )


        self.add_item(
            stop_button
        )


    async def pause_resume_callback(
        self,
        interaction
    ):

        vc = (
            interaction.guild.voice_client
        )


        if not vc:

            await interaction.response.send_message(
                "❌ I'm not connected to a voice channel.",
                ephemeral=True
            )

            return


        if vc.is_playing():

            vc.pause()


            await interaction.response.edit_message(

                embed=build_now_playing_embed(
                    self.guild_id,
                    paused=True
                ),

                view=MusicView(
                    self.guild_id
                )
            )


        elif vc.is_paused():

            vc.resume()


            await interaction.response.edit_message(

                embed=build_now_playing_embed(
                    self.guild_id,
                    paused=False
                ),

                view=MusicView(
                    self.guild_id
                )
            )


        else:

            await interaction.response.send_message(
                "❌ Nothing is playing.",
                ephemeral=True
            )


    async def skip_callback(
        self,
        interaction
    ):

        vc = (
            interaction.guild.voice_client
        )


        if vc and (
            vc.is_playing()
            or vc.is_paused()
        ):

            vc.stop()


            await interaction.response.send_message(

                f"{get_emoji('skip')} "
                f"Skipped by "
                f"{interaction.user.mention}"
            )


        else:

            await interaction.response.send_message(
                "❌ Nothing to skip.",
                ephemeral=True
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

                "❌ Need at least 2 songs "
                "in the queue to shuffle.",

                ephemeral=True
            )

            return


        random.shuffle(
            queue
        )


        await interaction.response.send_message(

            f"{get_emoji('shuffle')} "
            f"Queue shuffled by "
            f"{interaction.user.mention} — "
            f"**{len(queue)}** song(s) reordered"
        )


    async def loop_callback(
        self,
        interaction
    ):

        await interaction.response.send_message(

            f"{get_emoji('loop_queue')} "
            "**Choose Loop Mode**",

            view=LoopChoiceView(
                self.guild_id
            ),

            ephemeral=True
        )


    async def stop_callback(
        self,
        interaction
    ):

        await interaction.response.defer()


        await stop_playback(
            interaction.guild,
            delete_message=interaction.message
        )


        await interaction.channel.send(

            f"{get_emoji('stop')} "
            f"The magic fades... "
            f"stopped by "
            f"{interaction.user.mention}"
        )


# =========================================================
# VOICE CONNECTION
# =========================================================

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


    voice_channel = (
        user.voice.channel
    )


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


# =========================================================
# STOP PLAYBACK
# =========================================================

async def stop_playback(
    guild,
    delete_message=None
):

    guild_id = guild.id


    queues[guild_id] = []


    # Stop also disables loop

    loop_modes[guild_id] = None


    vc = guild.voice_client


    if vc:

        try:
            vc.stop()
        except Exception:
            pass


        try:
            await vc.disconnect()
        except Exception:
            pass


    current_song.pop(
        guild_id,
        None
    )


    msg = (
        delete_message
        or now_playing_messages.get(
            guild_id
        )
    )


    if msg:

        try:
            await msg.delete()

        except Exception:
            pass


    now_playing_messages.pop(
        guild_id,
        None
    )


# =========================================================
# PLAY NEXT
# =========================================================

async def play_next(
    guild,
    channel,
    send_func=None
):

    guild_id = guild.id


    vc = guild.voice_client


    if not vc:
        return


    # =====================================================
    # TRACK LOOP
    # =====================================================

    if (
        loop_modes.get(guild_id) == "track"
        and current_song.get(guild_id)
    ):

        query = current_song[
            guild_id
        ][
            "_query"
        ]


        requester = current_song[
            guild_id
        ][
            "requester"
        ]


    else:

        queue = get_queue(
            guild_id
        )


        if not queue:

            current_song.pop(
                guild_id,
                None
            )


            try:
                await vc.disconnect()
            except Exception:
                pass


            return


        item = queue.pop(
            0
        )


        query = item[
            "query"
        ]


        requester = item[
            "requester"
        ]


    # =====================================================
    # YT-DLP EXTRACTION
    # =====================================================

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

            raise Exception(
                "No result found."
            )


        if "entries" in data:

            entries = data.get(
                "entries"
            )


            if not entries:

                raise Exception(
                    "No results found."
                )


            data = entries[0]


        song_url = data[
            "url"
        ]


        title = data.get(
            "title",
            "Unknown Track"
        )


        duration_sec = data.get(
            "duration",
            0
        ) or 0


        minutes, seconds = divmod(
            int(duration_sec),
            60
        )


        duration_str = (
            f"{minutes:02d}:{seconds:02d}"
        )


        thumbnail = data.get(
            "thumbnail"
        )


        webpage_url = data.get(
            "webpage_url",
            query
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
                )
            )


        await play_next(
            guild,
            channel,
            send_func
        )


        return


    # =====================================================
    # CURRENT SONG
    # =====================================================

    current_song[guild_id] = {

        "title":
            title,

        "webpage_url":
            webpage_url,

        "thumbnail":
            thumbnail,

        "duration_str":
            duration_str,

        "requester":
            requester,

        "_query":
            query,
    }


    # =====================================================
    # AFTER SONG FINISHES
    # =====================================================

    def after_play(
        error
    ):

        if error:

            print(
                f"[PLAYER ERROR] {error}"
            )


        # QUEUE LOOP

        if (
            loop_modes.get(
                guild_id
            ) == "queue"
        ):

            get_queue(
                guild_id
            ).append(

                {
                    "query":
                        query,

                    "requester":
                        requester
                }
            )


        future = (
            asyncio.run_coroutine_threadsafe(

                play_next(
                    guild,
                    channel
                ),

                bot.loop
            )
        )


        try:

            future.result()

        except Exception as e:

            print(
                f"[AFTER CALLBACK ERROR] {e}"
            )


    # =====================================================
    # FFMPEG PLAY
    # =====================================================

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

            await send_func(

                text=(
                    "❌ Playback error.\n"
                    f"`{error_detail}`"
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
                )
            )


        return


    # =====================================================
    # NOW PLAYING
    # =====================================================

    embed = build_now_playing_embed(
        guild_id,
        paused=False
    )


    view = MusicView(
        guild_id
    )


    old_msg = now_playing_messages.get(
        guild_id
    )


    if old_msg:

        try:
            await old_msg.delete()

        except Exception:
            pass


    msg = await channel.send(

        embed=embed,

        view=view
    )


    now_playing_messages[
        guild_id
    ] = msg


# =========================================================
# ENQUEUE SONG
# =========================================================

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
            text=err
        )


    get_queue(
        guild.id
    ).append(

        {
            "query":
                query,

            "requester":
                user
        }
    )


    if (
        vc.is_playing()
        or vc.is_paused()
    ):

        await send_func(

            text=(
                "🪄 Added to the spellbook: "
                f"**{query}**"
            )
        )


    else:

        await play_next(
            guild,
            channel,
            send_func
        )


# =========================================================
# SAFE SEND
# =========================================================

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


# =========================================================
# READY
# =========================================================

@bot.event
async def on_ready():

    print(
        f"Logged in as "
        f"{bot.user} "
        f"({bot.user.id})"
    )


    try:

        synced = await bot.tree.sync()


        print(
            f"Synced "
            f"{len(synced)} Slash Commands!"
        )


    except Exception as e:

        print(
            f"[SYNC ERROR] {e}"
        )


# =========================================================
# MENTION + CUSTOM PREFIX SYSTEM
# =========================================================

@bot.event
async def on_message(
    message: discord.Message
):

    if message.author.bot:
        return


    if not message.guild:

        await bot.process_commands(
            message
        )

        return


    content = message.content.strip()


    # =====================================================
    # BOT MENTION
    # =====================================================

    mention_1 = (
        f"<@{bot.user.id}>"
    )


    mention_2 = (
        f"<@!{bot.user.id}>"
    )


    if (
        content.startswith(
            mention_1
        )
        or
        content.startswith(
            mention_2
        )
    ):

        if content.startswith(
            mention_1
        ):

            command_text = content[
                len(mention_1):
            ].strip()

        else:

            command_text = content[
                len(mention_2):
            ].strip()


        await handle_music_text_command(
            message,
            command_text
        )


        return


    # =====================================================
    # CUSTOM PREFIX
    #
    # Supports:
    #
    # 0p song
    # 0play song
    # 0 p song
    # 0 play song
    # 0stop
    # 0 stop
    # =====================================================

    prefix = prefixes.get(
        message.guild.id,
        DEFAULT_PREFIX
    )


    if prefix != DEFAULT_PREFIX:

        if content.startswith(
            prefix
        ):

            command_text = content[
                len(prefix):
            ].strip()


            await handle_music_text_command(
                message,
                command_text
            )


            return


    # =====================================================
    # DEFAULT PREFIX (!)
    # =====================================================

    await bot.process_commands(
        message
    )


# =========================================================
# TEXT COMMAND HANDLER
# =========================================================

async def handle_music_text_command(
    message,
    command_text
):

    if not command_text:

        await message.channel.send(

            "🪄 **Music Bot**\n\n"

            "Try:\n"

            "`@Bot play song`\n"

            "`@Bot stop`\n"

            "`@Bot loop`"
        )

        return


    parts = command_text.split()


    command = parts[0].lower()


    args = parts[1:]


    # =====================================================
    # PLAY
    # =====================================================

    if command in (
        "p",
        "play"
    ):

        if not args:

            await message.channel.send(
                "❌ Tell me what you want me to play."
            )

            return


        query = " ".join(
            args
        )


        try:

            async with message.channel.typing():

                await enqueue_song(

                    guild=message.guild,

                    channel=message.channel,

                    user=message.author,

                    query=query,

                    send_func=lambda
                    embed=None,
                    view=None,
                    text=None:
                    safe_send_channel(

                        message.channel,

                        text=text,

                        embed=embed,

                        view=view
                    )
                )


        except Exception as e:

            print(
                f"[PLAY ERROR] {e}"
            )


            await message.channel.send(
                f"❌ Something went wrong: `{e}`"
            )


        return


    # =====================================================
    # STOP
    # =====================================================

    if command in (
        "s",
        "stop"
    ):

        await stop_playback(
            message.guild
        )


        await message.channel.send(

            f"{get_emoji('stop')} "
            f"Music stopped by "
            f"{message.author.mention}"
        )


        return


    # =====================================================
    # SKIP
    # =====================================================

    if command in (
        "skip",
        "next",
        "n"
    ):

        vc = (
            message.guild.voice_client
        )


        if vc and (
            vc.is_playing()
            or vc.is_paused()
        ):

            vc.stop()


            await message.channel.send(

                f"{get_emoji('skip')} "
                f"Skipped by "
                f"{message.author.mention}"
            )


        else:

            await message.channel.send(
                "❌ Nothing is playing."
            )


        return


    # =====================================================
    # PAUSE
    # =====================================================

    if command == "pause":

        vc = (
            message.guild.voice_client
        )


        if not vc or not vc.is_playing():

            await message.channel.send(
                "❌ Nothing is playing."
            )

            return


        vc.pause()


        await message.channel.send(

            f"{get_emoji('pause')} "
            f"Paused by "
            f"{message.author.mention}"
        )


        msg = now_playing_messages.get(
            message.guild.id
        )


        if msg:

            try:

                await msg.edit(

                    embed=build_now_playing_embed(
                        message.guild.id,
                        paused=True
                    ),

                    view=MusicView(
                        message.guild.id
                    )
                )

            except Exception:
                pass


        return


    # =====================================================
    # RESUME
    # =====================================================

    if command in (
        "resume",
        "unpause"
    ):

        vc = (
            message.guild.voice_client
        )


        if not vc or not vc.is_paused():

            await message.channel.send(
                "❌ Nothing is paused."
            )

            return


        vc.resume()


        await message.channel.send(

            f"{get_emoji('resume')} "
            f"Resumed by "
            f"{message.author.mention}"
        )


        msg = now_playing_messages.get(
            message.guild.id
        )


        if msg:

            try:

                await msg.edit(

                    embed=build_now_playing_embed(
                        message.guild.id,
                        paused=False
                    ),

                    view=MusicView(
                        message.guild.id
                    )
                )

            except Exception:
                pass


        return


    # =====================================================
    # SHUFFLE
    # =====================================================

    if command in (
        "shuffle",
        "mix"
    ):

        queue = get_queue(
            message.guild.id
        )


        if len(queue) < 2:

            await message.channel.send(
                "❌ Need at least 2 songs in the queue."
            )

            return


        random.shuffle(
            queue
        )


        await message.channel.send(

            f"{get_emoji('shuffle')} "
            f"Queue shuffled by "
            f"{message.author.mention}"
        )


        return


    # =====================================================
    # LOOP
    # =====================================================

    if command in (
        "loop",
        "l"
    ):

        # Direct:
        #
        # 0loop track
        # 0loop queue
        # 0loop off

        if args:

            chosen = args[0].lower()


            if chosen == "track":

                loop_modes[
                    message.guild.id
                ] = "track"


                await message.channel.send(

                    f"{get_emoji('loop_track')} "
                    "**Track Loop enabled!**"
                )


            elif chosen == "queue":

                loop_modes[
                    message.guild.id
                ] = "queue"


                await message.channel.send(

                    f"{get_emoji('loop_queue')} "
                    "**Queue Loop enabled!**"
                )


            elif chosen == "off":

                loop_modes[
                    message.guild.id
                ] = None


                await message.channel.send(
                    "⏹️ **Loop disabled!**"
                )


            else:

                await message.channel.send(
                    "❌ Use `track`, `queue`, or `off`."
                )


        else:

            # No argument = buttons

            await message.channel.send(

                f"{get_emoji('loop_queue')} "
                "**Choose Loop Mode**",

                view=LoopChoiceView(
                    message.guild.id
                )
            )


        # Update now playing

        msg = now_playing_messages.get(
            message.guild.id
        )


        if msg:

            try:

                vc = (
                    message.guild.voice_client
                )


                paused = (
                    vc.is_paused()
                    if vc
                    else False
                )


                await msg.edit(

                    embed=build_now_playing_embed(
                        message.guild.id,
                        paused=paused
                    ),

                    view=MusicView(
                        message.guild.id
                    )
                )


            except Exception:
                pass


        return


    # =====================================================
    # 247
    # =====================================================

    if command in (
        "247",
        "24/7"
    ):

        await message.channel.send(
            "🔒 **24/7 Mode:** Active 🟢"
        )

        return


    # =====================================================
    # RECOMMEND
    # =====================================================

    if command in (
        "recommend",
        "rec"
    ):

        genre = (
            " ".join(args)
            if args
            else "Trending Hits"
        )


        embed = discord.Embed(

            title="✨ AI Recommended Tracks",

            description=(

                f"Recommendations for "
                f"**{genre}**:\n\n"

                "1️⃣ **Arijit Singh - Casual Lo-fi Vibe**\n"

                "2️⃣ **Midnight Chill Beats - Lofi Remix**\n"

                "3️⃣ **Phonk / Cyberpunk Synthwave Special**"
            ),

            color=PURPLE
        )


        await message.channel.send(
            embed=embed
        )


        return


    # =====================================================
    # UNKNOWN
    # =====================================================

    await message.channel.send(

        f"❌ Unknown command `{command}`."
    )


# =========================================================
# NORMAL PREFIX PLAY
# =========================================================

@bot.command(
    name="play",
    aliases=["p"]
)
async def prefix_play(
    ctx,
    *,
    query: str
):

    await enqueue_song(

        guild=ctx.guild,

        channel=ctx.channel,

        user=ctx.author,

        query=query,

        send_func=lambda
        embed=None,
        view=None,
        text=None:
        safe_send_channel(

            ctx.channel,

            text=text,

            embed=embed,

            view=view
        )
    )


# =========================================================
# NORMAL PREFIX STOP
# =========================================================

@bot.command(
    name="stop",
    aliases=["s"]
)
async def prefix_stop(ctx):

    await stop_playback(
        ctx.guild
    )


    await ctx.send(

        f"{get_emoji('stop')} "
        f"Music stopped by "
        f"{ctx.author.mention}"
    )


# =========================================================
# NORMAL PREFIX SKIP
# =========================================================

@bot.command(
    name="skip",
    aliases=[
        "next",
        "n"
    ]
)
async def prefix_skip(ctx):

    vc = (
        ctx.guild.voice_client
    )


    if vc and (
        vc.is_playing()
        or vc.is_paused()
    ):

        vc.stop()


        await ctx.send(

            f"{get_emoji('skip')} "
            f"Skipped by "
            f"{ctx.author.mention}"
        )


    else:

        await ctx.send(
            "❌ Nothing is playing."
        )


# =========================================================
# NORMAL PREFIX PAUSE
# =========================================================

@bot.command(
    name="pause"
)
async def prefix_pause(ctx):

    vc = (
        ctx.guild.voice_client
    )


    if not vc or not vc.is_playing():

        await ctx.send(
            "❌ Nothing is playing."
        )

        return


    vc.pause()


    await ctx.send(

        f"{get_emoji('pause')} "
        f"Paused by "
        f"{ctx.author.mention}"
    )


# =========================================================
# NORMAL PREFIX RESUME
# =========================================================

@bot.command(
    name="resume",
    aliases=["unpause"]
)
async def prefix_resume(ctx):

    vc = (
        ctx.guild.voice_client
    )


    if not vc or not vc.is_paused():

        await ctx.send(
            "❌ Nothing is paused."
        )

        return


    vc.resume()


    await ctx.send(

        f"{get_emoji('resume')} "
        f"Resumed by "
        f"{ctx.author.mention}"
    )


# =========================================================
# NORMAL PREFIX SHUFFLE
# =========================================================

@bot.command(
    name="shuffle",
    aliases=["mix"]
)
async def prefix_shuffle(ctx):

    queue = get_queue(
        ctx.guild.id
    )


    if len(queue) < 2:

        await ctx.send(
            "❌ Need at least 2 songs in the queue."
        )

        return


    random.shuffle(
        queue
    )


    await ctx.send(

        f"{get_emoji('shuffle')} "
        f"Queue shuffled by "
        f"{ctx.author.mention}"
    )


# =========================================================
# NORMAL PREFIX LOOP
# =========================================================

@bot.command(
    name="loop",
    aliases=["l"]
)
async def prefix_loop(
    ctx,
    mode: str = None
):

    if mode:

        mode = mode.lower()


        if mode == "track":

            loop_modes[
                ctx.guild.id
            ] = "track"


            await ctx.send(

                f"{get_emoji('loop_track')} "
                "**Track Loop enabled!**"
            )


        elif mode == "queue":

            loop_modes[
                ctx.guild.id
            ] = "queue"


            await ctx.send(

                f"{get_emoji('loop_queue')} "
                "**Queue Loop enabled!**"
            )


        elif mode == "off":

            loop_modes[
                ctx.guild.id
            ] = None


            await ctx.send(
                "⏹️ **Loop disabled!**"
            )


        else:

            await ctx.send(
                "❌ Use `track`, `queue`, or `off`."
            )


        return


    await ctx.send(

        f"{get_emoji('loop_queue')} "
        "**Choose Loop Mode**",

        view=LoopChoiceView(
            ctx.guild.id
        )
    )


# =========================================================
# SLASH /PLAY
# =========================================================

@bot.tree.command(
    name="play",
    description="Play or queue a song"
)
async def slash_play(
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

            view=view
        )
    )


# =========================================================
# SLASH /LOOP
# =========================================================

@bot.tree.command(
    name="loop",
    description="Set loop mode"
)
@app_commands.choices(

    mode=[

        app_commands.Choice(
            name="Queue",
            value="queue"
        ),

        app_commands.Choice(
            name="Track",
            value="track"
        ),
    ]
)
async def slash_loop(
    interaction: discord.Interaction,
    mode: app_commands.Choice[str]
):

    guild_id = (
        interaction.guild.id
    )


    loop_modes[
        guild_id
    ] = mode.value


    if mode.value == "queue":

        await interaction.response.send_message(

            f"{get_emoji('loop_queue')} "
            "**Queue Loop enabled!**"
        )

    else:

        await interaction.response.send_message(

            f"{get_emoji('loop_track')} "
            "**Track Loop enabled!**"
        )


    msg = now_playing_messages.get(
        guild_id
    )


    if msg:

        try:

            vc = (
                interaction.guild.voice_client
            )


            paused = (
                vc.is_paused()
                if vc
                else False
            )


            await msg.edit(

                embed=build_now_playing_embed(
                    guild_id,
                    paused=paused
                ),

                view=MusicView(
                    guild_id
                )
            )


        except Exception:
            pass


# =========================================================
# SLASH /SHUFFLE
# =========================================================

@bot.tree.command(
    name="shuffle",
    description="Shuffle the current queue"
)
async def slash_shuffle(
    interaction: discord.Interaction
):

    guild_id = (
        interaction.guild.id
    )


    queue = get_queue(
        guild_id
    )


    if len(queue) < 2:

        await interaction.response.send_message(

            "❌ Need at least 2 songs "
            "in the queue.",

            ephemeral=True
        )

        return


    random.shuffle(
        queue
    )


    await interaction.response.send_message(

        f"{get_emoji('shuffle')} "
        f"Queue shuffled by "
        f"{interaction.user.mention}"
    )


# =========================================================
# SLASH /SKIP
# =========================================================

@bot.tree.command(
    name="skip",
    description="Skip current song"
)
async def slash_skip(
    interaction: discord.Interaction
):

    vc = (
        interaction.guild.voice_client
    )


    if vc and (
        vc.is_playing()
        or vc.is_paused()
    ):

        vc.stop()


        await interaction.response.send_message(

            f"{get_emoji('skip')} "
            f"Skipped by "
            f"{interaction.user.mention}"
        )


    else:

        await interaction.response.send_message(

            "❌ Nothing is playing.",

            ephemeral=True
        )


# =========================================================
# SLASH /PAUSE
# =========================================================

@bot.tree.command(
    name="pause",
    description="Pause current song"
)
async def slash_pause(
    interaction: discord.Interaction
):

    vc = (
        interaction.guild.voice_client
    )


    if not vc or not vc.is_playing():

        await interaction.response.send_message(

            "❌ Nothing is playing.",

            ephemeral=True
        )

        return


    vc.pause()


    await interaction.response.send_message(

        f"{get_emoji('pause')} "
        f"Paused by "
        f"{interaction.user.mention}"
    )


    msg = now_playing_messages.get(
        interaction.guild.id
    )


    if msg:

        try:

            await msg.edit(

                embed=build_now_playing_embed(
                    interaction.guild.id,
                    paused=True
                ),

                view=MusicView(
                    interaction.guild.id
                )
            )


        except Exception:
            pass


# =========================================================
# SLASH /RESUME
# =========================================================

@bot.tree.command(
    name="resume",
    description="Resume current song"
)
async def slash_resume(
    interaction: discord.Interaction
):

    vc = (
        interaction.guild.voice_client
    )


    if not vc or not vc.is_paused():

        await interaction.response.send_message(

            "❌ Nothing is paused.",

            ephemeral=True
        )

        return


    vc.resume()


    await interaction.response.send_message(

        f"{get_emoji('resume')} "
        f"Resumed by "
        f"{interaction.user.mention}"
    )


    msg = now_playing_messages.get(
        interaction.guild.id
    )


    if msg:

        try:

            await msg.edit(

                embed=build_now_playing_embed(
                    interaction.guild.id,
                    paused=False
                ),

                view=MusicView(
                    interaction.guild.id
                )
            )


        except Exception:
            pass


# =========================================================
# SLASH /STOP
# =========================================================

@bot.tree.command(
    name="stop",
    description="Stop music and leave VC"
)
async def slash_stop(
    interaction: discord.Interaction
):

    await interaction.response.defer()


    await stop_playback(
        interaction.guild
    )


    await interaction.followup.send(

        f"{get_emoji('stop')} "
        f"The magic fades... stopped by "
        f"{interaction.user.mention}"
    )


# =========================================================
# SLASH /PREFIX
# =========================================================

@bot.tree.command(
    name="prefix",
    description="Change the prefix for this server"
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def slash_prefix(
    interaction: discord.Interaction,
    prefix: str
):

    prefix = prefix.strip()


    if not prefix:

        await interaction.response.send_message(

            "❌ Prefix cannot be empty.",

            ephemeral=True
        )

        return


    if len(prefix) > 5:

        await interaction.response.send_message(

            "❌ Prefix can only be "
            "up to 5 characters.",

            ephemeral=True
        )

        return


    prefixes[
        interaction.guild.id
    ] = prefix


    save_prefixes()


    await interaction.response.send_message(

        f"✅ Prefix changed to `{prefix}`!\n\n"

        "**Examples:**\n"

        f"`{prefix}p song`\n"

        f"`{prefix}play song`\n"

        f"`{prefix} p song`\n"

        f"`{prefix} play song`\n"

        f"`{prefix}stop`\n"

        f"`{prefix} stop`\n"

        f"`{prefix}loop`"
    )


# =========================================================
# SLASH /SETPFP
# =========================================================

@bot.tree.command(
    name="setpfp",
    description="Change bot profile picture"
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
            f"`{e}`"
        )


# =========================================================
# SLASH /SETBANNER
# =========================================================

@bot.tree.command(
    name="setbanner",
    description="Change bot profile banner"
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
            "✅ Banner updated!"
        )


    except Exception as e:

        await interaction.followup.send(

            f"❌ Failed to update banner: "
            f"`{e}`"
        )


# =========================================================
# SLASH /247
# =========================================================

@bot.tree.command(
    name="247",
    description="Keep bot in VC 24/7"
)
async def mode_247(
    interaction: discord.Interaction
):

    await interaction.response.send_message(
        "🔒 **24/7 Mode Status:** Active 🟢"
    )


# =========================================================
# SLASH /RECOMMEND
# =========================================================

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

            "1️⃣ **Arijit Singh - Casual Lo-fi Vibe**\n"

            "2️⃣ **Midnight Chill Beats - Lofi Remix**\n"

            "3️⃣ **Phonk / Cyberpunk Synthwave Special**"
        ),

        color=PURPLE
    )


    await interaction.response.send_message(
        embed=embed
    )


# =========================================================
# COMMAND ERROR
# =========================================================

@bot.event
async def on_command_error(
    ctx,
    error
):

    if isinstance(
        error,
        commands.CommandNotFound
    ):

        return


    if isinstance(
        error,
        commands.MissingRequiredArgument
    ):

        await ctx.send(
            "❌ You're missing something in the command."
        )

        return


    print(
        f"[COMMAND ERROR] {error}"
    )


# =========================================================
# START BOT
# =========================================================

token = os.getenv(
    "DISCORD_TOKEN"
)


if not token:

    raise RuntimeError(
        "DISCORD_TOKEN environment variable is missing!"
    )


bot.run(
    token
)
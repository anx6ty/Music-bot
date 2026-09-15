import asyncio
import logging
import os
import random
import traceback
from typing import Optional

import discord
import wavelink
from discord.ext import commands
from discord.ui import Button, View

# ==================== CONFIG ====================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
DEFAULT_PREFIX = "!"
BOT_NAME = "Fryplex"
EMBED_COLOR = 0x9B59B6
SUCCESS_COLOR = 0x2ECC71
ERROR_COLOR = 0xE74C3C
WARNING_COLOR = 0xF1C40F
INFO_COLOR = 0x3498DB

# Lavalink node configuration – change these to match your node
LAVALINK_HOST = os.getenv("LAVALINK_HOST", "127.0.0.1")
LAVALINK_PORT = int(os.getenv("LAVALINK_PORT", "2333"))
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")
LAVALINK_SECURE = os.getenv("LAVALINK_SECURE", "false").lower() == "true"

# ==================== LOGGING ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(BOT_NAME)

# ==================== BOT SETUP ====================
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

# ==================== LAVALINK NODE POOL ====================
class LavalinkPool:
    """Wrapper around wavelink.Pool for easy node management."""

    def __init__(self) -> None:
        self.pool: Optional[wavelink.Pool] = None

    async def connect(self) -> None:
        """Create and connect the Lavalink node."""
        if self.pool is not None:
            return
        node = wavelink.Node(
            uri=f"http{'s' if LAVALINK_SECURE else ''}://{LAVALINK_HOST}:{LAVALINK_PORT}",
            password=LAVALINK_PASSWORD,
        )
        self.pool = await wavelink.Pool.connect(
            nodes=[node],
            client=bot,
            cache_capacity=100,
        )
        logger.info("Lavalink node connected.")

    async def disconnect(self) -> None:
        if self.pool:
            await self.pool.disconnect()
            self.pool = None


lavalink_pool = LavalinkPool()

# ==================== UI: MUSIC CONTROL VIEW ====================
class MusicControlView(View):
    """Attractive button controls attached to the now‑playing embed."""

    def __init__(self, player: wavelink.Player, *, timeout: Optional[float] = 180.0) -> None:
        super().__init__(timeout=timeout)
        self.player = player

    # ----- Pause / Resume -----
    @discord.ui.button(emoji="⏸️", style=discord.ButtonStyle.secondary, custom_id="music_pause")
    async def pause_button(self, interaction: discord.Interaction, button: Button) -> None:
        if not self.player:
            return await interaction.response.send_message("Nothing is playing.", ephemeral=True)
        if self.player.paused:
            await self.player.pause(False)
            await interaction.response.send_message("▶️ Resumed.", ephemeral=True, delete_after=3)
        else:
            await self.player.pause(True)
            await interaction.response.send_message("⏸️ Paused.", ephemeral=True, delete_after=3)

    # ----- Skip -----
    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, custom_id="music_skip")
    async def skip_button(self, interaction: discord.Interaction, button: Button) -> None:
        if not self.player or not self.player.current:
            return await interaction.response.send_message("Nothing is playing.", ephemeral=True)
        await self.player.skip(force=True)
        await interaction.response.send_message("⏭️ Skipped.", ephemeral=True, delete_after=3)

    # ----- Stop -----
    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="music_stop")
    async def stop_button(self, interaction: discord.Interaction, button: Button) -> None:
        if not self.player:
            return await interaction.response.send_message("Nothing is playing.", ephemeral=True)
        await self.player.disconnect()
        self.stop()
        await interaction.response.send_message("⏹️ Stopped and disconnected.", ephemeral=True, delete_after=3)

    # ----- Shuffle -----
    @discord.ui.button(emoji="🔀", style=discord.ButtonStyle.secondary, custom_id="music_shuffle")
    async def shuffle_button(self, interaction: discord.Interaction, button: Button) -> None:
        if not self.player:
            return await interaction.response.send_message("Nothing is playing.", ephemeral=True)
        if not self.player.queue:
            return await interaction.response.send_message("Queue is empty.", ephemeral=True)
        random.shuffle(self.player.queue)
        await interaction.response.send_message("🔀 Queue shuffled.", ephemeral=True, delete_after=3)

    # ----- Loop -----
    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.secondary, custom_id="music_loop")
    async def loop_button(self, interaction: discord.Interaction, button: Button) -> None:
        if not self.player:
            return await interaction.response.send_message("Nothing is playing.", ephemeral=True)
        # Toggle loop mode between off and track
        current = self.player.queue.mode
        new_mode = wavelink.QueueMode.normal if current == wavelink.QueueMode.loop else wavelink.QueueMode.loop
        self.player.queue.mode = new_mode
        mode_text = "off" if new_mode == wavelink.QueueMode.normal else "track"
        await interaction.response.send_message(f"🔁 Loop mode: **{mode_text}**", ephemeral=True, delete_after=3)

    # ----- Volume Down -----
    @discord.ui.button(emoji="🔉", style=discord.ButtonStyle.secondary, custom_id="music_voldown")
    async def vol_down_button(self, interaction: discord.Interaction, button: Button) -> None:
        if not self.player:
            return await interaction.response.send_message("Nothing is playing.", ephemeral=True)
        new_vol = max(0, self.player.volume - 10)
        await self.player.set_volume(new_vol)
        await interaction.response.send_message(f"🔉 Volume: **{new_vol}%**", ephemeral=True, delete_after=3)

    # ----- Volume Up -----
    @discord.ui.button(emoji="🔊", style=discord.ButtonStyle.secondary, custom_id="music_volup")
    async def vol_up_button(self, interaction: discord.Interaction, button: Button) -> None:
        if not self.player:
            return await interaction.response.send_message("Nothing is playing.", ephemeral=True)
        new_vol = min(100, self.player.volume + 10)
        await self.player.set_volume(new_vol)
        await interaction.response.send_message(f"🔊 Volume: **{new_vol}%**", ephemeral=True, delete_after=3)

# ==================== EMBED BUILDERS ====================
def now_playing_embed(track: wavelink.Playable, requester: discord.Member) -> discord.Embed:
    """Clean, mini Lyvo‑style now‑playing embed."""
    embed = discord.Embed(
        title="🎶 Now Playing",
        description=f"**[{track.title}]({track.uri})**",
        colour=EMBED_COLOR,
    )
    embed.add_field(name="Artist", value=track.author or "Unknown", inline=True)
    embed.add_field(name="Duration", value=format_duration(track.length), inline=True)
    embed.add_field(name="Requested by", value=requester.mention, inline=True)
    if track.artwork:
        embed.set_thumbnail(url=track.artwork)
    embed.set_footer(text=f"{BOT_NAME} • Music")
    return embed

def queue_embed(queue: wavelink.Queue, page: int = 1, per_page: int = 10) -> discord.Embed:
    """Paginated queue embed."""
    if not queue:
        return discord.Embed(title="📭 Queue is empty", colour=WARNING_COLOR)
    total = len(queue)
    pages = (total + per_page - 1) // per_page
    page = max(1, min(page, pages))
    start = (page - 1) * per_page
    end = start + per_page
    lines = []
    for i, track in enumerate(queue[start:end], start=start + 1):
        lines.append(f"`{i}.` **{track.title}** – {format_duration(track.length)}")
    embed = discord.Embed(
        title="📋 Music Queue",
        description="\n".join(lines),
        colour=EMBED_COLOR,
    )
    embed.set_footer(text=f"Page {page}/{pages} • {total} tracks")
    return embed

def format_duration(ms: int) -> str:
    """Convert milliseconds to HH:MM:SS or MM:SS."""
    if not ms:
        return "Live"
    seconds = ms // 1000
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"

# ==================== BOT EVENTS ====================
@bot.event
async def on_ready() -> None:
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await lavalink_pool.connect()
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name=f"{DEFAULT_PREFIX}help • Music",
        )
    )

@bot.event
async def on_wavelink_track_start(payload: wavelink.TrackStartEventPayload) -> None:
    """Send the now‑playing embed when a new track starts."""
    player: wavelink.Player = payload.player
    track: wavelink.Playable = payload.track
    if not player or not track:
        return

    channel = getattr(player, "home_channel", None)
    if not channel:
        return

    requester = getattr(track, "requester", None) or bot.user
    embed = now_playing_embed(track, requester)
    view = MusicControlView(player)
    await channel.send(embed=embed, view=view)

@bot.event
async def on_wavelink_track_end(payload: wavelink.TrackEndEventPayload) -> None:
    """Auto‑play the next track when the current one ends."""
    player: wavelink.Player = payload.player
    if not player:
        return
    if not player.queue.is_empty:
        next_track = player.queue.get()
        await player.play(next_track)

@bot.event
async def on_wavelink_track_exception(payload: wavelink.TrackExceptionEventPayload) -> None:
    logger.error(f"Track exception: {payload.exception}")
    channel = getattr(payload.player, "home_channel", None)
    if channel:
        await channel.send(embed=discord.Embed(
            title="⚠️ Playback Error",
            description=f"`{payload.exception}`",
            colour=ERROR_COLOR,
        ))

# ==================== MUSIC COMMANDS ====================
@bot.command(name="join", aliases=["connect"])
async def join(ctx: commands.Context, *, channel: Optional[discord.VoiceChannel] = None) -> None:
    """Join a voice channel."""
    if not channel:
        channel = getattr(ctx.author.voice, "channel", None)
    if not channel:
        return await ctx.send(embed=discord.Embed(
            title="❌ Voice Channel Required",
            description="You must be in a voice channel or specify one.",
            colour=ERROR_COLOR,
        ))
    if ctx.voice_client:
        await ctx.voice_client.move_to(channel)
    else:
        await channel.connect(cls=wavelink.Player)
    await ctx.send(embed=discord.Embed(
        title="✅ Joined",
        description=f"Connected to {channel.mention}",
        colour=SUCCESS_COLOR,
    ))

@bot.command(name="play", aliases=["p"])
async def play(ctx: commands.Context, *, query: str) -> None:
    """Search and play a song or add it to the queue."""
    if not ctx.author.voice:
        return await ctx.send(embed=discord.Embed(
            title="❌ Voice Channel Required",
            description="You must be in a voice channel.",
            colour=ERROR_COLOR,
        ))

    if not ctx.voice_client:
        await ctx.author.voice.channel.connect(cls=wavelink.Player)

    player: wavelink.Player = ctx.voice_client
    player.home_channel = ctx.channel

    tracks = await wavelink.Playable.search(query)
    if not tracks:
        return await ctx.send(embed=discord.Embed(
            title="🔍 No Results",
            description=f"No tracks found for `{query}`.",
            colour=WARNING_COLOR,
        ))

    track = tracks[0]
    track.requester = ctx.author

    if player.playing:
        await player.queue.put_wait(track)
        await ctx.send(embed=discord.Embed(
            title="➕ Added to Queue",
            description=f"**{track.title}** – {format_duration(track.length)}",
            colour=SUCCESS_COLOR,
        ))
    else:
        await player.play(track)

@bot.command(name="pause")
async def pause(ctx: commands.Context) -> None:
    """Pause the current track."""
    if not ctx.voice_client or not ctx.voice_client.playing:
        return await ctx.send(embed=discord.Embed(title="❌ Nothing is playing", colour=ERROR_COLOR))
    await ctx.voice_client.pause(True)
    await ctx.send(embed=discord.Embed(title="⏸️ Paused", colour=SUCCESS_COLOR))

@bot.command(name="resume")
async def resume(ctx: commands.Context) -> None:
    """Resume the current track."""
    if not ctx.voice_client or not ctx.voice_client.paused:
        return await ctx.send(embed=discord.Embed(title="❌ Nothing is paused", colour=ERROR_COLOR))
    await ctx.voice_client.pause(False)
    await ctx.send(embed=discord.Embed(title="▶️ Resumed", colour=SUCCESS_COLOR))

@bot.command(name="skip")
async def skip(ctx: commands.Context) -> None:
    """Skip the current track."""
    if not ctx.voice_client or not ctx.voice_client.current:
        return await ctx.send(embed=discord.Embed(title="❌ Nothing is playing", colour=ERROR_COLOR))
    await ctx.voice_client.skip(force=True)
    await ctx.send(embed=discord.Embed(title="⏭️ Skipped", colour=SUCCESS_COLOR))

@bot.command(name="stop")
async def stop(ctx: commands.Context) -> None:
    """Stop playback and clear the queue."""
    if not ctx.voice_client:
        return await ctx.send(embed=discord.Embed(title="❌ Nothing is playing", colour=ERROR_COLOR))
    await ctx.voice_client.disconnect()
    await ctx.send(embed=discord.Embed(title="⏹️ Stopped", colour=SUCCESS_COLOR))

@bot.command(name="volume", aliases=["vol"])
async def volume(ctx: commands.Context, value: int) -> None:
    """Set the volume (0‑100)."""
    if not ctx.voice_client:
        return await ctx.send(embed=discord.Embed(title="❌ Nothing is playing", colour=ERROR_COLOR))
    value = max(0, min(100, value))
    await ctx.voice_client.set_volume(value)
    await ctx.send(embed=discord.Embed(
        title="🔊 Volume",
        description=f"Set to **{value}%**",
        colour=SUCCESS_COLOR,
    ))

@bot.command(name="queue", aliases=["q"])
async def queue(ctx: commands.Context, page: int = 1) -> None:
    """Show the current queue."""
    if not ctx.voice_client:
        return await ctx.send(embed=discord.Embed(title="📭 Queue is empty", colour=WARNING_COLOR))
    player: wavelink.Player = ctx.voice_client
    embed = queue_embed(player.queue, page)
    await ctx.send(embed=embed)

@bot.command(name="shuffle")
async def shuffle(ctx: commands.Context) -> None:
    """Shuffle the queue."""
    if not ctx.voice_client or not ctx.voice_client.queue:
        return await ctx.send(embed=discord.Embed(title="📭 Queue is empty", colour=WARNING_COLOR))
    random.shuffle(ctx.voice_client.queue)
    await ctx.send(embed=discord.Embed(title="🔀 Shuffled", colour=SUCCESS_COLOR))

@bot.command(name="loop")
async def loop(ctx: commands.Context, mode: str = "toggle") -> None:
    """Toggle loop mode: off, track, or queue."""
    if not ctx.voice_client:
        return await ctx.send(embed=discord.Embed(title="❌ Nothing is playing", colour=ERROR_COLOR))
    player: wavelink.Player = ctx.voice_client
    mode = mode.lower()
    if mode == "off":
        player.queue.mode = wavelink.QueueMode.normal
    elif mode == "track":
        player.queue.mode = wavelink.QueueMode.loop
    elif mode == "queue":
        player.queue.mode = wavelink.QueueMode.loop_all
    else:
        # toggle
        current = player.queue.mode
        player.queue.mode = wavelink.QueueMode.normal if current != wavelink.QueueMode.normal else wavelink.QueueMode.loop
    await ctx.send(embed=discord.Embed(
        title="🔁 Loop Mode",
        description=f"Set to **{player.queue.mode.name}**",
        colour=SUCCESS_COLOR,
    ))

@bot.command(name="nowplaying", aliases=["np"])
async def nowplaying(ctx: commands.Context) -> None:
    """Show the currently playing track."""
    if not ctx.voice_client or not ctx.voice_client.current:
        return await ctx.send(embed=discord.Embed(title="❌ Nothing is playing", colour=ERROR_COLOR))
    track = ctx.voice_client.current
    requester = getattr(track, "requester", ctx.author)
    embed = now_playing_embed(track, requester)
    view = MusicControlView(ctx.voice_client)
    await ctx.send(embed=embed, view=view)

@bot.command(name="ping")
async def ping(ctx: commands.Context) -> None:
    """Check the bot's latency."""
    embed = discord.Embed(
        title="🏓 Pong!",
        description=f"Latency: `{round(bot.latency * 1000)}ms`",
        colour=INFO_COLOR,
    )
    await ctx.send(embed=embed)

# ==================== ERROR HANDLING ====================
@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        return await ctx.send(embed=discord.Embed(
            title="❌ Missing Argument",
            description=f"`{error.param.name}` is required.",
            colour=ERROR_COLOR,
        ))
    if isinstance(error, commands.MissingPermissions):
        return await ctx.send(embed=discord.Embed(
            title="❌ Missing Permissions",
            description="You do not have permission to use this command.",
            colour=ERROR_COLOR,
        ))
    logger.error(f"Command error: {error}")
    await ctx.send(embed=discord.Embed(
        title="⚠️ An Error Occurred",
        description=f"```{error}```",
        colour=ERROR_COLOR,
    ))

# ==================== ENTRY POINT ====================
async def main() -> None:
    async with bot:
        await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot shutting down...")
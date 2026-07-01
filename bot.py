import discord
from discord import app_commands
import asyncpg
import os
from datetime import datetime, timedelta, timezone

TOKEN = os.environ["DISCORD_BOT_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]

# ── Database ───────────────────────────────────────────────────────────────────

async def init_db(pool):
    """Create tables if they don't exist."""
    async with pool.acquire() as conn:
        # Each row is one S4S channel — a guild can have many
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS s4s_channels (
                guild_id   TEXT NOT NULL,
                channel_id BIGINT NOT NULL,
                PRIMARY KEY (guild_id, channel_id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS cooldowns (
                guild_id  TEXT,
                user_id   TEXT,
                last_ping TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS warnings (
                guild_id TEXT,
                user_id  TEXT,
                count    INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            )
        """)

# ── Bot setup ──────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.guilds = True
intents.members = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)
pool: asyncpg.Pool = None  # set in on_ready

# ── Events ─────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL)
    await init_db(pool)
    await tree.sync()
    print(f"Logged in as {bot.user}")


@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    gid = str(message.guild.id)
    uid = str(message.author.id)

    async with pool.acquire() as conn:
        # Check if this channel is a registered S4S channel
        row = await conn.fetchrow(
            "SELECT channel_id FROM s4s_channels WHERE guild_id = $1 AND channel_id = $2",
            gid, message.channel.id
        )
        if not row:
            return

        if "@everyone" not in message.content:
            return

        now = datetime.now(timezone.utc)

        # Get the user's last ping
        cd_row = await conn.fetchrow(
            "SELECT last_ping FROM cooldowns WHERE guild_id = $1 AND user_id = $2", gid, uid
        )
        last = cd_row["last_ping"] if cd_row else None

        # First ping or cooldown expired — allow it
        if last is None or (now - last) >= timedelta(hours=24):
            await conn.execute("""
                INSERT INTO cooldowns (guild_id, user_id, last_ping)
                VALUES ($1, $2, $3)
                ON CONFLICT (guild_id, user_id) DO UPDATE SET last_ping = $3
            """, gid, uid, now)

            embed = discord.Embed(title="✅ Ping Used (1/1)", color=discord.Color.green())
            embed.add_field(name="Next ping available", value="In **24h 0m**")
            await message.channel.send(embed=embed)
            return

        # Cooldown still active — delete message and warn
        try:
            await message.delete()
        except discord.Forbidden:
            pass

        # Time remaining
        ends = last + timedelta(hours=24)
        mins = max(0, int((ends - now).total_seconds() // 60))
        h, m = mins // 60, mins % 60

        # Increment warnings
        await conn.execute("""
            INSERT INTO warnings (guild_id, user_id, count)
            VALUES ($1, $2, 1)
            ON CONFLICT (guild_id, user_id) DO UPDATE SET count = warnings.count + 1
        """, gid, uid)
        warns_row = await conn.fetchrow(
            "SELECT count FROM warnings WHERE guild_id = $1 AND user_id = $2", gid, uid
        )
        warns = warns_row["count"]

        # 3 warnings — delete only the channel the violation happened in
        if warns >= 3:
            embed = discord.Embed(
                title="🚨 S4S Channel Deleted",
                description=f"{message.author.mention} reached 3 warnings. The S4S channel has been deleted.",
                color=discord.Color.dark_red()
            )
            log_ch = message.guild.system_channel or next(
                (c for c in message.guild.text_channels if c.permissions_for(message.guild.me).send_messages), None
            )
            if log_ch:
                await log_ch.send(embed=embed)

            # Delete the specific channel and remove it from the DB
            s4s_ch = message.guild.get_channel(message.channel.id)
            if s4s_ch:
                await s4s_ch.delete()

            await conn.execute(
                "DELETE FROM s4s_channels WHERE guild_id = $1 AND channel_id = $2",
                gid, message.channel.id
            )
            return

        # Regular warning embed
        embed = discord.Embed(title="⚠️ S4S Warning", color=discord.Color.orange())
        embed.add_field(name="User", value=message.author.mention, inline=True)
        embed.add_field(name="Warnings", value=f"**{warns}/3**", inline=True)
        embed.add_field(name="Reason", value="More than one @everyone ping within 24 hours.", inline=False)
        embed.add_field(name="Time Remaining", value=f"**{h}h {m}m** until next ping", inline=False)
        await message.channel.send(embed=embed)


# ── Admin check ────────────────────────────────────────────────────────────────

def admin_only():
    async def check(interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admins only.", ephemeral=True)
            return False
        return True
    return app_commands.check(check)


# ── Slash commands ─────────────────────────────────────────────────────────────

@tree.command(name="sets4s", description="Add this channel as an S4S channel. (Admin)")
@admin_only()
async def sets4s(interaction: discord.Interaction):
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO s4s_channels (guild_id, channel_id)
            VALUES ($1, $2)
            ON CONFLICT DO NOTHING
        """, str(interaction.guild_id), interaction.channel_id)

    embed = discord.Embed(
        title="✅ S4S Channel Added",
        description=f"{interaction.channel.mention} is now an S4S channel.",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed)


@tree.command(name="removes4s", description="Remove this channel from S4S monitoring. (Admin)")
@admin_only()
async def removes4s(interaction: discord.Interaction):
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM s4s_channels WHERE guild_id = $1 AND channel_id = $2",
            str(interaction.guild_id), interaction.channel_id
        )
    if result == "DELETE 0":
        embed = discord.Embed(
            title="❌ Not an S4S Channel",
            description=f"{interaction.channel.mention} is not a registered S4S channel.",
            color=discord.Color.red()
        )
    else:
        embed = discord.Embed(
            title="✅ S4S Channel Removed",
            description=f"{interaction.channel.mention} is no longer monitored.",
            color=discord.Color.orange()
        )
    await interaction.response.send_message(embed=embed)


@tree.command(name="s4swarn", description="Check a user's S4S warnings. (Admin)")
@app_commands.describe(user="User to check")
@admin_only()
async def s4swarn(interaction: discord.Interaction, user: discord.Member):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT count FROM warnings WHERE guild_id = $1 AND user_id = $2",
            str(interaction.guild_id), str(user.id)
        )
    count = row["count"] if row else 0
    embed = discord.Embed(title="📋 S4S Warnings", color=discord.Color.blue())
    embed.add_field(name="User", value=user.mention, inline=True)
    embed.add_field(name="Warnings", value=f"**{count}/3**", inline=True)
    await interaction.response.send_message(embed=embed)


@tree.command(name="resets4swarns", description="Reset a user's S4S warnings. (Admin)")
@app_commands.describe(user="User to reset")
@admin_only()
async def resets4swarns(interaction: discord.Interaction, user: discord.Member):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE warnings SET count = 0 WHERE guild_id = $1 AND user_id = $2",
            str(interaction.guild_id), str(user.id)
        )
    embed = discord.Embed(
        title="✅ Warnings Reset",
        description=f"{user.mention} warnings reset to **0/3**.",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed)


@tree.command(name="s4sreset", description="Reset a user's ping cooldown. (Admin)")
@app_commands.describe(user="User to reset")
@admin_only()
async def s4sreset(interaction: discord.Interaction, user: discord.Member):
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM cooldowns WHERE guild_id = $1 AND user_id = $2",
            str(interaction.guild_id), str(user.id)
        )
    embed = discord.Embed(
        title="✅ Cooldown Reset",
        description=f"{user.mention} can ping immediately.",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed)


@tree.command(name="s4sinfo", description="Show S4S config for this server. (Admin)")
@admin_only()
async def s4sinfo(interaction: discord.Interaction):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT channel_id FROM s4s_channels WHERE guild_id = $1", str(interaction.guild_id)
        )
    if rows:
        channels = "\n".join(f"<#{r['channel_id']}>" for r in rows)
    else:
        channels = "None — use `/sets4s` in a channel to add one."
    embed = discord.Embed(title="ℹ️ S4S Info", color=discord.Color.blurple())
    embed.add_field(name="S4S Channels", value=channels, inline=False)
    embed.add_field(name="Ping Limit", value="1 @everyone per 24h", inline=True)
    embed.add_field(name="Warning Limit", value="3 warnings → channel deleted", inline=True)
    await interaction.response.send_message(embed=embed)


bot.run(TOKEN)

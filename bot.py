import discord
from discord import app_commands
import aiosqlite
import os
from datetime import datetime, timedelta, timezone

TOKEN = os.environ["DISCORD_BOT_TOKEN"]
DB_FILE = "s4s.db"

# ── Database ───────────────────────────────────────────────────────────────────

async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS s4s_channels (
                guild_id   TEXT NOT NULL,
                channel_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, channel_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS cooldowns (
                guild_id  TEXT NOT NULL,
                user_id   TEXT NOT NULL,
                last_ping TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS warnings (
                guild_id TEXT NOT NULL,
                user_id  TEXT NOT NULL,
                count    INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            )
        """)
        await db.commit()

# ── Bot setup ──────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.guilds = True
intents.members = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# ── Events ─────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    await init_db()
    await tree.sync()
    print(f"Logged in as {bot.user}")


@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    gid = str(message.guild.id)
    uid = str(message.author.id)

    async with aiosqlite.connect(DB_FILE) as db:
        # Only watch registered S4S channels
        async with db.execute(
            "SELECT channel_id FROM s4s_channels WHERE guild_id = ? AND channel_id = ?",
            (gid, message.channel.id)
        ) as cur:
            row = await cur.fetchone()

        if not row:
            return

        if "@everyone" not in message.content:
            return

        now = datetime.now(timezone.utc)

        # Get last ping time
        async with db.execute(
            "SELECT last_ping FROM cooldowns WHERE guild_id = ? AND user_id = ?",
            (gid, uid)
        ) as cur:
            cd_row = await cur.fetchone()

        last = datetime.fromisoformat(cd_row[0]) if cd_row else None

        # First ping or cooldown expired — allow it
        if last is None or (now - last) >= timedelta(hours=24):
            await db.execute(
                "INSERT INTO cooldowns (guild_id, user_id, last_ping) VALUES (?, ?, ?) "
                "ON CONFLICT(guild_id, user_id) DO UPDATE SET last_ping = excluded.last_ping",
                (gid, uid, now.isoformat())
            )
            await db.commit()

            embed = discord.Embed(title="✅ Ping Used (1/1)", color=discord.Color.green())
            embed.add_field(name="Next ping available", value="In **24h 0m**")
            await message.channel.send(embed=embed)
            return

        # Cooldown active — delete message and warn
        try:
            await message.delete()
        except discord.Forbidden:
            pass

        ends = last + timedelta(hours=24)
        mins = max(0, int((ends - now).total_seconds() // 60))
        h, m = mins // 60, mins % 60

        # Increment warnings
        await db.execute(
            "INSERT INTO warnings (guild_id, user_id, count) VALUES (?, ?, 1) "
            "ON CONFLICT(guild_id, user_id) DO UPDATE SET count = count + 1",
            (gid, uid)
        )
        await db.commit()

        async with db.execute(
            "SELECT count FROM warnings WHERE guild_id = ? AND user_id = ?",
            (gid, uid)
        ) as cur:
            warns = (await cur.fetchone())[0]

        # 3 warnings — delete the channel
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

            s4s_ch = message.guild.get_channel(message.channel.id)
            if s4s_ch:
                await s4s_ch.delete()

            await db.execute(
                "DELETE FROM s4s_channels WHERE guild_id = ? AND channel_id = ?",
                (gid, message.channel.id)
            )
            await db.commit()
            return

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
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT INTO s4s_channels (guild_id, channel_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
            (str(interaction.guild_id), interaction.channel_id)
        )
        await db.commit()

    embed = discord.Embed(
        title="✅ S4S Channel Added",
        description=f"{interaction.channel.mention} is now an S4S channel.",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed)


@tree.command(name="removes4s", description="Remove this channel from S4S monitoring. (Admin)")
@admin_only()
async def removes4s(interaction: discord.Interaction):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "DELETE FROM s4s_channels WHERE guild_id = ? AND channel_id = ?",
            (str(interaction.guild_id), interaction.channel_id)
        )
        await db.commit()

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
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT count FROM warnings WHERE guild_id = ? AND user_id = ?",
            (str(interaction.guild_id), str(user.id))
        ) as cur:
            row = await cur.fetchone()
    count = row[0] if row else 0
    embed = discord.Embed(title="📋 S4S Warnings", color=discord.Color.blue())
    embed.add_field(name="User", value=user.mention, inline=True)
    embed.add_field(name="Warnings", value=f"**{count}/3**", inline=True)
    await interaction.response.send_message(embed=embed)


@tree.command(name="resets4swarns", description="Reset a user's S4S warnings. (Admin)")
@app_commands.describe(user="User to reset")
@admin_only()
async def resets4swarns(interaction: discord.Interaction, user: discord.Member):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "UPDATE warnings SET count = 0 WHERE guild_id = ? AND user_id = ?",
            (str(interaction.guild_id), str(user.id))
        )
        await db.commit()
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
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "DELETE FROM cooldowns WHERE guild_id = ? AND user_id = ?",
            (str(interaction.guild_id), str(user.id))
        )
        await db.commit()
    embed = discord.Embed(
        title="✅ Cooldown Reset",
        description=f"{user.mention} can ping immediately.",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed)


@tree.command(name="s4sinfo", description="Show S4S config for this server. (Admin)")
@admin_only()
async def s4sinfo(interaction: discord.Interaction):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT channel_id FROM s4s_channels WHERE guild_id = ?",
            (str(interaction.guild_id),)
        ) as cur:
            rows = await cur.fetchall()
    channels = "\n".join(f"<#{r[0]}>" for r in rows) if rows else "None — use `/sets4s` in a channel."
    embed = discord.Embed(title="ℹ️ S4S Info", color=discord.Color.blurple())
    embed.add_field(name="S4S Channels", value=channels, inline=False)
    embed.add_field(name="Ping Limit", value="1 @everyone per 24h", inline=True)
    embed.add_field(name="Warning Limit", value="3 warnings → channel deleted", inline=True)
    await interaction.response.send_message(embed=embed)
@tree.command(name="makes4schannel", description="Create a new S4S channel. (Admin)")
@app_commands.describe(name="Name of the new S4S channel")
@admin_only()
async def makes4schannel(interaction: discord.Interaction, name: str):

    # Create the channel
    channel = await interaction.guild.create_text_channel(name)

    # Register it as an S4S channel
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT INTO s4s_channels (guild_id, channel_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
            (str(interaction.guild_id), channel.id)
        )
        await db.commit()

    # Optional starter message
    await channel.send(
        "# 📢 S4S Channel\n"
        "Welcome! This channel is monitored by the S4S bot.\n"
        "You may use **1 @everyone ping every 24 hours.**"
    )

    embed = discord.Embed(
        title="✅ S4S Channel Created",
        description=f"{channel.mention} has been created and registered as an S4S channel.",
        color=discord.Color.green()
    )

    await interaction.response.send_message(embed=embed)

bot.run(TOKEN)

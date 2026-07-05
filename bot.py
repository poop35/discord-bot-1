import discord
from discord import app_commands
import aiosqlite
import os
from datetime import datetime, timedelta, timezone

# ── CONFIG ─────────────────────────────────────────────────────────────────────

TOKEN = os.environ["DISCORD_BOT_TOKEN"]
DB_FILE = "s4s.db"

# Category where S4S channels will be created
S4S_CATEGORY_ID = int(os.environ.get("S4S_CATEGORY_ID", "0"))

# ── DATABASE ──────────────────────────────────────────────────────────────────

async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS s4s_channels (
                guild_id TEXT NOT NULL,
                channel_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, channel_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS cooldowns (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                last_ping TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS warnings (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            )
        """)
        await db.commit()

# ── BOT SETUP ─────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.guilds = True
intents.members = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# ── HELPERS ───────────────────────────────────────────────────────────────────

async def get_category(guild: discord.Guild):
    if S4S_CATEGORY_ID == 0:
        return None
    try:
        channel = guild.get_channel(S4S_CATEGORY_ID)
        if channel:
            return channel
        return await guild.fetch_channel(S4S_CATEGORY_ID)
    except:
        return None

# ── EVENTS ───────────────────────────────────────────────────────────────────

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

        async with db.execute(
            "SELECT last_ping FROM cooldowns WHERE guild_id = ? AND user_id = ?",
            (gid, uid)
        ) as cur:
            cd_row = await cur.fetchone()

        last = datetime.fromisoformat(cd_row[0]) if cd_row else None

        if last is None or (now - last) >= timedelta(hours=24):
            await db.execute(
                "INSERT INTO cooldowns (guild_id, user_id, last_ping) VALUES (?, ?, ?) "
                "ON CONFLICT(guild_id, user_id) DO UPDATE SET last_ping = excluded.last_ping",
                (gid, uid, now.isoformat())
            )
            await db.commit()

            embed = discord.Embed(title="✅ Ping Used (1/1)", color=discord.Color.green())
            embed.add_field(name="Next ping available", value="In 24 hours")
            await message.channel.send(embed=embed)
            return

        # cooldown active
        try:
            await message.delete()
        except:
            pass

        ends = last + timedelta(hours=24)
        mins = max(0, int((ends - now).total_seconds() // 60))
        h, m = mins // 60, mins % 60

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

        if warns >= 3:
            await message.channel.delete()

            await db.execute(
                "DELETE FROM s4s_channels WHERE guild_id = ? AND channel_id = ?",
                (gid, message.channel.id)
            )
            await db.commit()
            return

        embed = discord.Embed(title="⚠️ S4S Warning", color=discord.Color.orange())
        embed.add_field(name="Warnings", value=f"{warns}/3", inline=True)
        embed.add_field(name="Time Left", value=f"{h}h {m}m", inline=True)
        await message.channel.send(embed=embed)

# ── ADMIN CHECK ───────────────────────────────────────────────────────────────

def admin_only():
    async def check(interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Admins only.", ephemeral=True)
            return False
        return True
    return app_commands.check(check)

# ── COMMANDS ──────────────────────────────────────────────────────────────────

@tree.command(name="sets4s", description="Mark this channel as S4S monitored")
@admin_only()
async def sets4s(interaction: discord.Interaction):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR IGNORE INTO s4s_channels VALUES (?, ?)",
            (str(interaction.guild_id), interaction.channel_id)
        )
        await db.commit()

    await interaction.response.send_message("S4S channel set.")

# ⭐ NEW COMMAND YOU WANTED
@tree.command(name="makes4schannel", description="Create an S4S channel inside the category")
@admin_only()
async def makes4schannel(interaction: discord.Interaction, name: str):

    category = await get_category(interaction.guild)

    channel = await interaction.guild.create_text_channel(
        name=name,
        category=category
    )

    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT INTO s4s_channels VALUES (?, ?)",
            (str(interaction.guild_id), channel.id)
        )
        await db.commit()

    await interaction.response.send_message(f"Created S4S channel: {channel.mention}")

@tree.command(name="removes4s", description="Remove S4S monitoring")
@admin_only()
async def removes4s(interaction: discord.Interaction):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "DELETE FROM s4s_channels WHERE guild_id = ? AND channel_id = ?",
            (str(interaction.guild_id), interaction.channel_id)
        )
        await db.commit()

    await interaction.response.send_message("Removed S4S channel.")

@tree.command(name="s4sinfo", description="Show S4S settings")
@admin_only()
async def s4sinfo(interaction: discord.Interaction):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT channel_id FROM s4s_channels WHERE guild_id = ?",
            (str(interaction.guild_id),)
        ) as cur:
            rows = await cur.fetchall()

    chans = "\n".join(f"<#{r[0]}>" for r in rows) or "None"

    await interaction.response.send_message(f"S4S Channels:\n{chans}")

# ── RUN ──────────────────────────────────────────────────────────────────────

bot.run(TOKEN)

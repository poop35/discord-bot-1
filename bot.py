import discord
from discord import app_commands
from discord.ext import tasks
import aiosqlite
import os
from datetime import datetime, timedelta, timezone

# ── CONFIG ─────────────────────────────────────────────────────────────────────

TOKEN = os.environ["DISCORD_BOT_TOKEN"]
DB_FILE = "s4s.db"
S4S_CATEGORY_ID = int(os.environ.get("S4S_CATEGORY_ID", "0"))

# ── HELPERS ───────────────────────────────────────────────────────────────────

def format_time_left(seconds: int):
    if seconds < 0:
        return "0s"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}h {m}m {s}s"

async def get_category(guild: discord.Guild):
    if S4S_CATEGORY_ID == 0:
        return None
    try:
        ch = guild.get_channel(S4S_CATEGORY_ID)
        return ch or await guild.fetch_channel(S4S_CATEGORY_ID)
    except:
        return None

# ── DATABASE ──────────────────────────────────────────────────────────────────

async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS s4s_channels (
                guild_id TEXT,
                channel_id INTEGER,
                PRIMARY KEY (guild_id, channel_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS cooldowns (
                guild_id TEXT,
                user_id TEXT,
                last_ping TEXT,
                notified INTEGER DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS warnings (
                guild_id TEXT,
                user_id TEXT,
                count INTEGER DEFAULT 0,
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

# ── READY ──────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    await init_db()
    await tree.sync()
    cooldown_checker.start()
    print(f"Logged in as {bot.user}")

# ── COOLDOWN DM CHECKER ───────────────────────────────────────────────────────

@tasks.loop(seconds=30)
async def cooldown_checker():
    now = datetime.now(timezone.utc)

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT guild_id, user_id, last_ping, notified FROM cooldowns") as cur:
            rows = await cur.fetchall()

        for guild_id, user_id, last_ping, notified in rows:
            last = datetime.fromisoformat(last_ping)
            end = last + timedelta(hours=24)

            if now >= end and notified == 0:
                guild = bot.get_guild(int(guild_id))
                if guild:
                    try:
                        user = await bot.fetch_user(int(user_id))
                        await user.send("⏰ Your S4S cooldown is over — you can ping again now!")
                    except:
                        pass

                await db.execute(
                    "UPDATE cooldowns SET notified = 1 WHERE guild_id = ? AND user_id = ?",
                    (guild_id, user_id)
                )
                await db.commit()

# ── MESSAGE EVENT ─────────────────────────────────────────────────────────────

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    gid = str(message.guild.id)
    uid = str(message.author.id)

    async with aiosqlite.connect(DB_FILE) as db:

        async with db.execute(
            "SELECT channel_id FROM s4s_channels WHERE guild_id=? AND channel_id=?",
            (gid, message.channel.id)
        ) as cur:
            if not await cur.fetchone():
                return

        if "@everyone" not in message.content:
            return

        now = datetime.now(timezone.utc)

        async with db.execute(
            "SELECT last_ping FROM cooldowns WHERE guild_id=? AND user_id=?",
            (gid, uid)
        ) as cur:
            row = await cur.fetchone()

        last = datetime.fromisoformat(row[0]) if row else None

        # allowed ping
        if last is None or (now - last) >= timedelta(hours=24):
            await db.execute(
                "INSERT INTO cooldowns (guild_id, user_id, last_ping, notified) "
                "VALUES (?, ?, ?, 0) "
                "ON CONFLICT(guild_id, user_id) DO UPDATE SET last_ping=excluded.last_ping, notified=0",
                (gid, uid, now.isoformat())
            )
            await db.commit()

            embed = discord.Embed(title="✅ Ping Used", color=discord.Color.green())
            await message.channel.send(embed=embed)
            return

        # cooldown active
        try:
            await message.delete()
        except:
            pass

        end = last + timedelta(hours=24)
        remaining = int((end - now).total_seconds())
        time_left = format_time_left(remaining)

        await db.execute(
            "INSERT INTO warnings (guild_id, user_id, count) VALUES (?, ?, 1) "
            "ON CONFLICT(guild_id, user_id) DO UPDATE SET count=count+1",
            (gid, uid)
        )
        await db.commit()

        async with db.execute(
            "SELECT count FROM warnings WHERE guild_id=? AND user_id=?",
            (gid, uid)
        ) as cur:
            warns = (await cur.fetchone())[0]

        if warns >= 3:
            await message.channel.delete()
            return

        embed = discord.Embed(title="⚠️ S4S Warning", color=discord.Color.orange())
        embed.add_field(name="Warnings", value=f"{warns}/3", inline=True)
        embed.add_field(name="Time Left", value=f"⏳ {time_left}", inline=True)
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

@tree.command(name="sets4s")
@admin_only()
async def sets4s(interaction: discord.Interaction):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR IGNORE INTO s4s_channels VALUES (?, ?)",
            (str(interaction.guild_id), interaction.channel_id)
        )
        await db.commit()

    await interaction.response.send_message("S4S channel set.")

@tree.command(name="makes4schannel")
@admin_only()
async def makes4schannel(interaction: discord.Interaction, name: str):
    category = await get_category(interaction.guild)

    ch = await interaction.guild.create_text_channel(
        name=name,
        category=category
    )

    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT INTO s4s_channels VALUES (?, ?)",
            (str(interaction.guild_id), ch.id)
        )
        await db.commit()

    await interaction.response.send_message(f"Created {ch.mention}")

@tree.command(name="removes4s")
@admin_only()
async def removes4s(interaction: discord.Interaction):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "DELETE FROM s4s_channels WHERE guild_id=? AND channel_id=?",
            (str(interaction.guild_id), interaction.channel_id)
        )
        await db.commit()

    await interaction.response.send_message("Removed.")

@tree.command(name="s4sinfo")
@admin_only()
async def s4sinfo(interaction: discord.Interaction):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT channel_id FROM s4s_channels WHERE guild_id=?",
            (str(interaction.guild_id),)
        ) as cur:
            rows = await cur.fetchall()

    chans = "\n".join(f"<#{r[0]}>" for r in rows) or "None"
    await interaction.response.send_message(f"S4S Channels:\n{chans}")

# ── RUN ──────────────────────────────────────────────────────────────────────

bot.run(TOKEN)

import discord
from discord import app_commands
import aiosqlite
import os
from datetime import datetime, timedelta, timezone

TOKEN = os.environ["DISCORD_BOT_TOKEN"]
DB_FILE = "s4s.db"


# ───────────────────────── DATABASE ─────────────────────────

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
            PRIMARY KEY (guild_id, user_id)
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS warnings (
            guild_id TEXT,
            user_id TEXT,
            count INTEGER,
            PRIMARY KEY (guild_id, user_id)
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS s4s_owners (
            guild_id TEXT,
            channel_id INTEGER,
            user_id TEXT,
            role_id INTEGER,
            PRIMARY KEY (guild_id, channel_id)
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS s4s_logs (
            guild_id TEXT PRIMARY KEY,
            channel_id INTEGER
        )
        """)

        await db.commit()


# ───────────────────────── BOT SETUP ─────────────────────────

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


@bot.event
async def on_ready():
    await init_db()
    await tree.sync()
    print(f"Logged in as {bot.user}")


# ───────────────────────── S4S CHECK ─────────────────────────

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

        # allow
        if last is None or (now - last) >= timedelta(hours=24):

            await db.execute("""
            INSERT INTO cooldowns VALUES (?, ?, ?)
            ON CONFLICT(guild_id, user_id)
            DO UPDATE SET last_ping=excluded.last_ping
            """, (gid, uid, now.isoformat()))

            await db.commit()

            await message.channel.send(
                embed=discord.Embed(
                    title="✅ S4S Ping Used",
                    color=discord.Color.green()
                )
            )
            return

        # delete spam
        try:
            await message.delete()
        except:
            pass

        await db.execute("""
        INSERT INTO warnings VALUES (?, ?, 1)
        ON CONFLICT(guild_id, user_id)
        DO UPDATE SET count=count+1
        """, (gid, uid))

        await db.commit()

        async with db.execute(
            "SELECT count FROM warnings WHERE guild_id=? AND user_id=?",
            (gid, uid)
        ) as cur:
            warns = (await cur.fetchone())[0]

        if warns >= 3:
            await message.channel.delete()

            await db.execute(
                "DELETE FROM s4s_channels WHERE guild_id=? AND channel_id=?",
                (gid, message.channel.id)
            )
            await db.commit()


# ───────────────────────── ADMIN CHECK ─────────────────────────

def admin_only():
    async def check(interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Admins only.", ephemeral=True)
            return False
        return True
    return app_commands.check(check)


# ───────────────────────── CREATE S4S ─────────────────────────

@tree.command(name="makes4schannel", description="Create S4S channel")
@app_commands.describe(
    name="Channel name",
    owner="Owner",
    category="Category"
)
@admin_only()
async def makes4schannel(interaction: discord.Interaction, name: str, owner: discord.Member, category: str):

    guild = interaction.guild

    cat = discord.utils.get(guild.categories, name=category)
    if not cat:
        cat = await guild.create_category(category)

    role = await guild.create_role(name=f"S4S | {owner.display_name}")
    await owner.add_roles(role)

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(send_messages=False),
        role: discord.PermissionOverwrite(send_messages=True, view_channel=True),
        guild.me: discord.PermissionOverwrite(send_messages=True, view_channel=True),
    }

    channel = await guild.create_text_channel(
        name=name,
        category=cat,
        overwrites=overwrites
    )

    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT INTO s4s_channels VALUES (?, ?)", (str(guild.id), channel.id))
        await db.execute("INSERT INTO s4s_owners VALUES (?, ?, ?, ?)",
                         (str(guild.id), channel.id, str(owner.id), role.id))
        await db.commit()

    await interaction.response.send_message(
        embed=discord.Embed(title="Created S4S", description=channel.mention),
        ephemeral=True
    )


# ───────────────────────── S4S INFO ─────────────────────────

@tree.command(name="s4sinfo")
@admin_only()
async def s4sinfo(interaction: discord.Interaction):

    gid = str(interaction.guild.id)

    async with aiosqlite.connect(DB_FILE) as db:

        async with db.execute(
            "SELECT COUNT(*) FROM s4s_channels WHERE guild_id=?",
            (gid,)
        ) as cur:
            total = (await cur.fetchone())[0]

        async with db.execute(
            "SELECT COUNT(*) FROM warnings WHERE guild_id=?",
            (gid,)
        ) as cur:
            warns = (await cur.fetchone())[0]

    embed = discord.Embed(title="📊 S4S Stats")
    embed.add_field(name="Channels", value=str(total))
    embed.add_field(name="Total Warn Records", value=str(warns))

    await interaction.response.send_message(embed=embed)


# ───────────────────────── RESET COMMANDS ─────────────────────────

@tree.command(name="resetwarnings")
@admin_only()
async def resetwarnings(interaction: discord.Interaction, user: discord.Member):

    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "UPDATE warnings SET count=0 WHERE guild_id=? AND user_id=?",
            (str(interaction.guild.id), str(user.id))
        )
        await db.commit()

    await interaction.response.send_message("Warnings reset", ephemeral=True)


@tree.command(name="resetcooldown")
@admin_only()
async def resetcooldown(interaction: discord.Interaction, user: discord.Member):

    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "DELETE FROM cooldowns WHERE guild_id=? AND user_id=?",
            (str(interaction.guild.id), str(user.id))
        )
        await db.commit()

    await interaction.response.send_message("Cooldown reset", ephemeral=True)


# ───────────────────────── LOCK / UNLOCK ─────────────────────────

@tree.command(name="s4slock")
@admin_only()
async def s4slock(interaction: discord.Interaction, channel: discord.TextChannel):

    await channel.set_permissions(interaction.guild.default_role, send_messages=False)
    await interaction.response.send_message("Locked", ephemeral=True)


@tree.command(name="s4sunlock")
@admin_only()
async def s4sunlock(interaction: discord.Interaction, channel: discord.TextChannel):

    await channel.set_permissions(interaction.guild.default_role, send_messages=True)
    await interaction.response.send_message("Unlocked", ephemeral=True)


# ───────────────────────── LEADERBOARD ─────────────────────────

@tree.command(name="s4sleaderboard")
@admin_only()
async def leaderboard(interaction: discord.Interaction):

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT user_id, count FROM warnings WHERE guild_id=? ORDER BY count DESC LIMIT 10",
            (str(interaction.guild.id),)
        ) as cur:
            rows = await cur.fetchall()

    desc = ""
    for i, r in enumerate(rows, start=1):
        user = await interaction.guild.fetch_member(int(r[0]))
        desc += f"{i}. {user.name} - {r[1]} warns\n"

    embed = discord.Embed(title="📈 S4S Leaderboard", description=desc or "No data")
    await interaction.response.send_message(embed=embed)


# ───────────────────────── LOG CHANNEL ─────────────────────────

@tree.command(name="sets4slogs")
@admin_only()
async def sets4slogs(interaction: discord.Interaction, channel: discord.TextChannel):

    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR REPLACE INTO s4s_logs VALUES (?, ?)",
            (str(interaction.guild.id), channel.id)
        )
        await db.commit()

    await interaction.response.send_message("Logs set", ephemeral=True)


# ───────────────────────── RUN BOT ─────────────────────────

bot.run(TOKEN)

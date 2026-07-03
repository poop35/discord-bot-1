import discord
from discord import app_commands
import aiosqlite
import os
from datetime import datetime, timedelta, timezone

TOKEN = os.environ["DISCORD_BOT_TOKEN"]
DB_FILE = "s4s.db"


# ───────────────────────── DB ─────────────────────────

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

        await db.commit()


# ───────────────────────── BOT ─────────────────────────

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


# ───────────────────────── CHECK S4S MESSAGES ─────────────────────────

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

        if last is None or (now - last) >= timedelta(hours=24):
            await db.execute("""
                INSERT INTO cooldowns VALUES (?, ?, ?)
                ON CONFLICT(guild_id, user_id)
                DO UPDATE SET last_ping=excluded.last_ping
            """, (gid, uid, now.isoformat()))
            await db.commit()

            await message.channel.send(
                embed=discord.Embed(title="✅ Ping used", color=discord.Color.green())
            )
            return

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

@tree.command(name="makes4schannel", description="Create S4S channel with owner + category")
@app_commands.describe(
    name="Channel name",
    owner="Owner of this S4S channel",
    category="Category name"
)
@admin_only()
async def makes4schannel(interaction: discord.Interaction, name: str, owner: discord.Member, category: str):

    guild = interaction.guild

    # category
    cat = discord.utils.get(guild.categories, name=category)
    if not cat:
        cat = await guild.create_category(category)

    # role
    role = await guild.create_role(name=f"S4S | {owner.display_name}")
    await owner.add_roles(role)

    # permissions
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
        await db.execute(
            "INSERT INTO s4s_owners VALUES (?, ?, ?, ?)",
            (str(guild.id), channel.id, str(owner.id), role.id)
        )
        await db.commit()

    await interaction.response.send_message(
        embed=discord.Embed(
            title="📢 S4S Created",
            description=f"{channel.mention} owned by {owner.mention}",
            color=discord.Color.green()
        ),
        ephemeral=True
    )


# ───────────────────────── TRANSFER OWNER ─────────────────────────

@tree.command(name="transferowner")
@admin_only()
async def transferowner(interaction: discord.Interaction, channel: discord.TextChannel, new_owner: discord.Member):

    gid = str(interaction.guild.id)

    async with aiosqlite.connect(DB_FILE) as db:

        async with db.execute(
            "SELECT role_id FROM s4s_owners WHERE guild_id=? AND channel_id=?",
            (gid, channel.id)
        ) as cur:
            row = await cur.fetchone()

        if not row:
            return await interaction.response.send_message("Not S4S channel", ephemeral=True)

        old_role = channel.guild.get_role(row[0])
        if old_role:
            await old_role.delete()

        new_role = await interaction.guild.create_role(name=f"S4S | {new_owner.display_name}")
        await new_owner.add_roles(new_role)

        await db.execute("""
            UPDATE s4s_owners
            SET user_id=?, role_id=?
            WHERE guild_id=? AND channel_id=?
        """, (str(new_owner.id), new_role.id, gid, channel.id))

        await db.commit()

    await interaction.response.send_message("Owner transferred", ephemeral=True)


# ───────────────────────── REMOVE S4S ─────────────────────────

@tree.command(name="removes4schannel")
@admin_only()
async def removes4schannel(interaction: discord.Interaction, channel: discord.TextChannel):

    gid = str(interaction.guild.id)

    async with aiosqlite.connect(DB_FILE) as db:

        async with db.execute(
            "SELECT role_id FROM s4s_owners WHERE guild_id=? AND channel_id=?",
            (gid, channel.id)
        ) as cur:
            row = await cur.fetchone()

        if row:
            role = channel.guild.get_role(row[0])
            if role:
                await role.delete()

        await db.execute("DELETE FROM s4s_channels WHERE guild_id=? AND channel_id=?", (gid, channel.id))
        await db.execute("DELETE FROM s4s_owners WHERE guild_id=? AND channel_id=?", (gid, channel.id))
        await db.commit()

    await channel.delete()

    await interaction.response.send_message("S4S removed", ephemeral=True)


bot.run(TOKEN)

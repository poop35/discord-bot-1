# S4S Discord Bot

A Discord bot that manages S4S (Server 4 Server) channels, enforcing a strict one `@everyone` ping per 24 hours per user.

---

## Features

- Designate a channel as the S4S channel via `/sets4s`
- Automatically detects and handles `@everyone` pings
- Warns and eventually deletes the S4S channel if a user accumulates 3 warnings
- All data persists across restarts via `data.json`
- All commands are admin-only

---

## Setup Instructions

### 1. Create a Discord Application & Bot

1. Go to [https://discord.com/developers/applications](https://discord.com/developers/applications) and click **New Application**.
2. Give it a name (e.g. `S4S Manager`) and click **Create**.
3. In the left sidebar, click **Bot**.
4. Click **Add Bot** → **Yes, do it!**
5. Under the bot's username, click **Reset Token** and copy the token — you'll need it shortly.
6. Under **Privileged Gateway Intents**, enable:
   - **Message Content Intent**
   - **Server Members Intent**
7. Click **Save Changes**.

### 2. Invite the Bot to Your Server

1. In the left sidebar, click **OAuth2** → **URL Generator**.
2. Under **Scopes**, check: `bot` and `applications.commands`
3. Under **Bot Permissions**, check:
   - `Administrator` *(simplest option — or manually select the permissions below)*
   - Alternatively: `Read Messages/View Channels`, `Send Messages`, `Manage Messages`, `Embed Links`, `Manage Channels`
4. Copy the generated URL, paste it in your browser, and invite the bot to your server.

### 3. Install Dependencies

Make sure you have **Python 3.10+** installed.

```bash
cd discord-bot
pip install -r requirements.txt
```

### 4. Set Your Bot Token

Set the `DISCORD_BOT_TOKEN` environment variable before running:

**Linux / macOS:**
```bash
export DISCORD_BOT_TOKEN="your-token-here"
```

**Windows (Command Prompt):**
```cmd
set DISCORD_BOT_TOKEN=your-token-here
```

**Windows (PowerShell):**
```powershell
$env:DISCORD_BOT_TOKEN="your-token-here"
```

### 5. Run the Bot

```bash
python bot.py
```

You should see:
```
✅ Logged in as S4S Manager#XXXX (ID: ...)
   Slash commands synced globally.
```

> **Note:** Slash commands may take up to 1 hour to appear globally. To sync instantly during development, pass your guild (server) ID to `tree.sync(guild=discord.Object(id=YOUR_GUILD_ID))` in `on_ready`.

---

## Commands

All commands require the **Administrator** permission.

| Command | Description |
|---|---|
| `/sets4s` | Sets the current channel as the S4S channel |
| `/s4swarn @user` | Shows how many warnings a user has |
| `/resets4swarns @user` | Resets a user's warnings to 0 |
| `/s4sreset @user` | Clears a user's 24-hour ping cooldown |
| `/s4sinfo` | Shows current S4S config (channel, ping limit, warning limit) |

---

## How It Works

1. Run `/sets4s` in your S4S channel to register it.
2. The bot watches only that channel for `@everyone` pings.
3. **First ping in 24h:** Allowed. The bot posts a confirmation embed.
4. **Extra ping within 24h:** Message is deleted. User gets a warning embed.
5. **3rd warning:** The S4S channel is automatically deleted. A log is posted to the server's system channel. The bot stops monitoring the deleted channel.

---

## Data Storage

All data is saved to `data.json` in the same directory as `bot.py`:

- `s4s_channels` — guild ID → channel ID mapping
- `cooldowns` — per-guild, per-user last ping timestamps (ISO 8601)
- `warnings` — per-guild, per-user warning counts

The file is created automatically on first use.

# Telegram ChatBot - Direct Messaging System

A powerful Telegram bot that enables direct messaging between users and the bot owner, with support for text and media files.

## Features

### For Users
- ✅ Send direct messages to the bot owner
- 📸 Send media files (photos, videos, documents, GIFs, voice messages, etc.)
- 📋 View your conversation history
- 📬 Check for new replies
- 🔔 Receive automatic acknowledgment

### For Bot Owner
- 📨 Receive all user messages with easy reply button
- 📤 Send messages to any user
- 💬 Browse all conversations
- 📁 Send media files to users
- 🔍 Search for users by username or ID

## Commands

### User Commands
```
/start    - Welcome message and bot introduction
/status   - Check for new replies from owner
/history  - View your conversation history with owner
/help     - Show available commands
```

### Owner Commands
```
/send <user_id> <message>      - Send a message to any user
/searchuser <username or ID>   - Find a user by username or ID
/mymessages                    - View all active conversations
/reply <user_id> <message>     - Reply to a user
/ownerhelp                     - Show all owner commands
```

## Supported Media Types

Users and owner can exchange:
- 📷 Photos
- 🎬 Videos
- 📹 Video Notes (short videos)
- 🎵 Audio/Voice Messages
- 📄 Documents (any file type)
- 🎞️ Animated GIFs
- 🎨 Stickers

## Setup

### 1. Create Telegram Bot
- Open [@BotFather](https://t.me/botfather) on Telegram
- Send `/newbot` and follow the instructions
- Save your `BOT_TOKEN`

### 2. Get API Credentials
- Visit [my.telegram.org](https://my.telegram.org)
- Go to "API development tools"
- Create an application
- Save your `API_ID` and `API_HASH`

### 3. Get Your Owner ID
- Send a message to your bot
- Open your bot's logs to find your user ID (or use [@userinfobot](https://t.me/userinfobot))
- Save your `OWNER_ID`

### 4. Configure Environment Variables

Set these on your deployment platform:

**Required:**
- `API_ID` - From my.telegram.org
- `API_HASH` - From my.telegram.org
- `BOT_TOKEN` - From @BotFather
- `OWNER_ID` - Your Telegram user ID

**Optional:**
- `SESSION_STRING` - For session persistence
- `BOT_USERNAME` - Your bot's username
- `DATABASE_PATH` - Custom database location (default: telegram_bot.db)
- `PORT` - HTTP server port (default: 5000)

## Deployment

### On Render (Recommended)

1. Push this repository to GitHub
2. Create a new **Web Service** on [Render](https://render.com)
3. Connect your GitHub repository
4. Configure the service:
   - **Runtime:** Python 3.11
   - **Build Command:** (leave empty)
   - **Start Command:** `python chat_bot.py`
   - **Instance Type:** Free tier works, but Starter/Standard recommended for reliability

5. Add your environment variables in the Render dashboard
6. Deploy!

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export API_ID=your_api_id
export API_HASH=your_api_hash
export BOT_TOKEN=your_bot_token
export OWNER_ID=your_user_id

# Run the bot
python chat_bot.py
```

## How It Works

### User Sends Message
1. User sends a text message or media to the bot
2. Bot saves the message to the SQLite database
3. User receives `✅` confirmation (auto-deletes)
4. Owner receives notification with a reply button

### Owner Replies
1. Owner clicks the reply button
2. Owner types their message
3. Message is sent to the user
4. Message is saved to the database

### Conversation History
- Users can use `/history` to see recent messages
- Owner can use `/mymessages` to see all active conversations
- All messages are stored in the local SQLite database

## Technical Details

- **Framework:** Telethon (Telegram client library)
- **Database:** SQLite (local file-based)
- **Web Server:** Waitress (for cloud compatibility)
- **Async:** asyncio with uvloop for performance

## Troubleshooting

### "API ID or Hash cannot be empty"
- Make sure you've set `API_ID` and `API_HASH` environment variables
- Check that they're correct (from my.telegram.org, not from @BotFather)

### Bot doesn't respond
- Check the logs: `python chat_bot.py` (run locally first to debug)
- Make sure `BOT_TOKEN` and `OWNER_ID` are correct
- Verify the bot can access Telegram (no VPN blocks)

### Media not uploading
- Ensure the file size is within Telegram limits (up to 2GB for documents)
- Check internet connection
- Large files may take a while - be patient

## File Structure

```
├── chat_bot.py              # Main bot logic and HTTP server
├── config.py                # Configuration from environment variables
├── database_sqlite.py       # SQLite database operations
├── logger.py                # Logging setup
├── telethon_helpers.py      # Telethon utilities
├── requirements.txt         # Python dependencies
└── telegram_bot.db          # SQLite database (created on first run)
```

## Database

The bot uses SQLite for persistence. All messages and user data are stored locally in `telegram_bot.db`. The database includes:

- **users** - User information and settings
- **chat_messages** - All conversation messages
- **daily_usage** - Usage tracking
- And more tables for admin, broadcasts, etc.

## License

This project is open source and available under the MIT License.

## Support

For issues or questions:
- Check the logs for error messages
- Review the troubleshooting section above
- Create an issue on GitHub

---

**Made with ❤️ by [@Wolfy004](https://t.me/Wolfy004)**

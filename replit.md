# ChatBot - Telegram Direct Messaging Bot

## Overview
This is a Telegram chat bot built with Telethon that allows direct messaging between users and the bot owner. It enables the owner to receive and reply to messages from any user who interacts with the bot. The bot includes an HTTP server for health checks and monitoring on port 5000 (or PORT env var).

## User Preferences
None specified yet.

## System Architecture

### Technology Stack
- **Language:** Python 3.11
- **Telegram Client:** Telethon
- **Database:** SQLite (local file: telegram_bot.db)
- **Event Loop:** uvloop (for performance)
- **Web Server:** Waitress (for Render/cloud deployments)

### Core Features
1. **User Messaging:**
   - Users can send messages to the bot owner
   - Owner receives notifications with reply buttons
   - Conversation history tracking
   - Unread message status

2. **Owner Commands:**
   - `/send <user_id> <message>` - Send message to any user
   - `/searchuser <username or ID>` - Find a user
   - `/mymessages` - View all conversations
   - `/reply <user_id> <message>` - Reply to user
   - `/ownerhelp` - View owner commands

3. **User Commands:**
   - `/start` - Welcome message
   - `/status` - Check for new replies
   - `/history` - View conversation history
   - `/help` - Help menu

4. **HTTP Health Checks:**
   - `GET /` - Returns JSON status
   - `GET /health` - Health check endpoint
   - `GET /ping` - Ping endpoint
   - Listens on PORT environment variable (default: 5000)

### Core Files
- `chat_bot.py` - Main bot logic, Telethon event handlers, and HTTP server
- `config.py` - Configuration from environment variables
- `database_sqlite.py` - SQLite database operations
- `logger.py` - Logging configuration
- `telethon_helpers.py` - Helper utilities for Telethon
- `server.py` - Standalone HTTP server (optional)

## Required Environment Variables
Configure these secrets in your deployment platform:
- `API_ID` - Telegram API ID (from my.telegram.org)
- `API_HASH` - Telegram API Hash (from my.telegram.org)
- `BOT_TOKEN` - Bot token from @BotFather
- `OWNER_ID` - Telegram user ID of the bot owner

## Optional Environment Variables
- `SESSION_STRING` - Telethon session string (for persistence)
- `BOT_USERNAME` - Bot username
- `DATABASE_PATH` - Custom database file path (default: telegram_bot.db)
- `CHATBOT_PORT` - HTTP server port (default: 5001, takes priority over PORT)
- `PORT` - HTTP server port fallback (default: 5000, used if CHATBOT_PORT not set)

## Deployment on Render

1. **Push your code to GitHub**
2. **Create a new Web Service on Render**
3. **Set Environment Variables:**
   - Add API_ID, API_HASH, BOT_TOKEN, OWNER_ID as secrets
4. **Configure the service:**
   - Build Command: (leave empty)
   - Start Command: `python chat_bot.py`
   - Instance Type: Any (bot will listen on PORT 5000 for health checks)

## Running Locally
```bash
python chat_bot.py
```

The bot will:
- Start an HTTP server on port 5000 (for Render/cloud compatibility)
- Connect to Telegram using your API credentials
- Listen for messages and respond to commands

## Recent Changes
- **December 19, 2025:** Added HTTP server for Render compatibility
  - Runs HTTP server in background thread
  - Listens on PORT environment variable (default 5000)
  - Provides health check endpoints at `/`, `/health`, `/ping`
- **December 2025:** Imported from GitHub and configured for Replit environment
  - Added Python 3.11 and required dependencies
  - Fixed database helper functions for backup triggers

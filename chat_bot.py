# Copyright (C) @Wolfy004
# Chat Bot - Direct User Messaging System
# Channel: https://t.me/Wolfy004

import os
import asyncio
import re
import threading
from datetime import datetime
from typing import Optional

try:
    import uvloop
    if not isinstance(asyncio.get_event_loop_policy(), uvloop.EventLoopPolicy):
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

from telethon import TelegramClient, events
from telethon.errors import PeerIdInvalidError, BadRequestError, UserNotParticipantError
from telethon.sessions import StringSession
from telethon.tl.types import KeyboardButtonCallback
from telethon_helpers import InlineKeyboardButton, InlineKeyboardMarkup
from config import PyroConf
from logger import LOGGER
from database_sqlite import db

# Store pending replies: callback_id -> user_id
PENDING_REPLIES = {}

class ChatBot:
    def __init__(self):
        # Use StringSession if available, otherwise create new session
        session = StringSession(PyroConf.SESSION_STRING) if PyroConf.SESSION_STRING else 'chat_bot'
        
        self.bot = TelegramClient(
            session,
            PyroConf.API_ID,
            PyroConf.API_HASH
        )
        self.owner_id = PyroConf.OWNER_ID
        self.setup_handlers()
    
    async def delete_message_later(self, message, delay=3):
        """Delete a message after a delay (in seconds)"""
        try:
            await asyncio.sleep(delay)
            # Use delete_messages for more reliable deletion (works with text, media, gifs, etc)
            await self.bot.delete_messages(message.chat_id, message.id)
        except Exception as e:
            LOGGER(__name__).debug(f"Could not delete message: {e}")
    
    def setup_handlers(self):
        @self.bot.on(events.NewMessage(incoming=True, func=lambda e: e.is_private and e.text and not e.text.startswith('/') and e.sender_id != self.owner_id))
        async def handle_message(event):
            """Handle incoming text messages from users (not from owner)"""
            sender_id = event.sender_id
            sender_name = f"{event.sender.first_name or ''} {event.sender.last_name or ''}".strip() or "Unknown"
            username = f"@{event.sender.username}" if event.sender.username else "No Username"
            
            # Save message from user to owner
            db.save_chat_message(sender_id, self.owner_id, event.text, 'text')
            
            # Send and auto-delete acknowledgment
            ack_msg = await event.respond("✅ **Message Delivered**")
            asyncio.create_task(self.delete_message_later(ack_msg, delay=2))
            
            # Notify owner with reply button
            try:
                buttons = [
                    [InlineKeyboardButton.callback("📤 Reply to User", f"reply_{sender_id}")]
                ]
                
                notification_text = (
                    f"📩 **New Message Received**\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 **From:** {sender_name} ({username})\n"
                    f"🆔 **ID:** `{sender_id}`\n\n"
                    f"📝 **Content:**\n"
                    f"_{event.text}_"
                )
                
                await self.bot.send_message(
                    self.owner_id,
                    notification_text,
                    buttons=buttons
                )
            except Exception as e:
                LOGGER(__name__).error(f"Failed to notify owner: {e}")
            
            LOGGER(__name__).info(f"Message from {sender_id}: {event.text[:50]}")
        
        @self.bot.on(events.NewMessage(incoming=True, func=lambda e: e.is_private and not e.text and e.sender_id != self.owner_id))
        async def handle_media(event):
            """Handle incoming media messages from users (photos, videos, documents, etc.)"""
            sender_id = event.sender_id
            sender_name = f"{event.sender.first_name or ''} {event.sender.last_name or ''}".strip() or "Unknown"
            username = f"@{event.sender.username}" if event.sender.username else "No Username"
            
            # Determine media type
            media_type = 'media'
            media_description = '📁 Document'
            
            if event.photo:
                media_type = 'photo'
                media_description = '📷 Photo'
            elif event.video:
                media_type = 'video'
                media_description = '🎬 Video'
            elif event.audio:
                media_type = 'audio'
                media_description = '🎵 Audio'
            elif event.voice:
                media_type = 'voice'
                media_description = '🎤 Voice Message'
            elif event.video_note:
                media_type = 'video_note'
                media_description = '📹 Video Note'
            elif event.document:
                media_type = 'document'
                media_description = f'📄 {event.document.attributes[0].file_name if event.document.attributes else "Document"}'
            elif event.gif:
                media_type = 'gif'
                media_description = '🎞️ GIF'
            elif event.sticker:
                media_type = 'sticker'
                media_description = '🎨 Sticker'
            
            # Save media reference to database
            caption = event.message.text or media_description
            db.save_chat_message(sender_id, self.owner_id, caption, media_type)
            
            # Send and auto-delete acknowledgment
            ack_msg = await event.respond(f"✅ **{media_description} Delivered**")
            asyncio.create_task(self.delete_message_later(ack_msg, delay=2))
            
            # Forward media to owner with info
            try:
                notification_text = (
                    f"📦 **New {media_description.upper()} Received**\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 **From:** {sender_name} ({username})\n"
                    f"🆔 **ID:** `{sender_id}`\n"
                )
                
                if event.message.text:
                    notification_text += f"\n💬 **Caption:**\n_{event.message.text}_"
                
                buttons = [
                    [InlineKeyboardButton.callback("📤 Reply to User", f"reply_{sender_id}")]
                ]
                
                await event.forward_to(self.owner_id)
                await self.bot.send_message(
                    self.owner_id,
                    notification_text,
                    buttons=buttons
                )
            except Exception as e:
                LOGGER(__name__).error(f"Failed to forward media to owner: {e}")
            
            LOGGER(__name__).info(f"{media_type.upper()} from {sender_id}")
        
        @self.bot.on(events.NewMessage(pattern='/start', incoming=True, func=lambda e: e.is_private))
        async def handle_start(event):
            """Handle /start command"""
            sender_id = event.sender_id
            db.add_user(sender_id, event.sender.username, event.sender.first_name, event.sender.last_name)
            
            welcome_text = (
                "👋 **Welcome to the Professional Chat Support Bot!**\n\n"
                "I am here to help you communicate directly with our team. "
                "Your messages are securely delivered and handled with care.\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "📊 **HOW IT WORKS**\n"
                "1️⃣ **Send Message:** Type anything or send a file.\n"
                "2️⃣ **Review:** The owner will review and reply.\n"
                "3️⃣ **Stay Notified:** You'll get a notification when they reply!\n\n"
                "📁 **SUPPORTED MEDIA**\n"
                "• 📸 Photos & 🎥 Videos\n"
                "• 📄 Documents & 🎵 Audio\n"
                "• 🎤 Voice & 🎞️ GIFs/Stickers\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "🛠 **AVAILABLE COMMANDS**\n"
                "• `/status` - Check for new unread replies\n"
                "• `/history` - View recent conversation history\n"
                "• `/help` - View this help menu again\n\n"
                "✨ *How can we help you today?*"
            )
            
            await event.respond(welcome_text)
            LOGGER(__name__).info(f"User {sender_id} started the bot")
        
        @self.bot.on(events.NewMessage(pattern='/status', incoming=True, func=lambda e: e.is_private))
        async def check_status(event):
            """Check if there are new replies"""
            sender_id = event.sender_id
            conversations = db.get_user_conversations(sender_id, limit=50)
            
            unread = sum(1 for msg in conversations if msg['from_user_id'] == self.owner_id and msg['is_read'] == 0)
            
            if unread == 0:
                await event.respond(
                    "📭 **Inbox Clear**\n\n"
                    "You have no new unread replies from the owner.\n\n"
                    "💡 *Tip: Use /history to see previous messages.*"
                )
            else:
                await event.respond(
                    f"📬 **New Messages Detected!**\n\n"
                    f"You have **{unread}** unread reply(ies) waiting for you.\n\n"
                    "👉 Use `/history` to read them now."
                )
            
            db.mark_messages_as_read(sender_id, self.owner_id)
        
        @self.bot.on(events.NewMessage(pattern='/history', incoming=True, func=lambda e: e.is_private))
        async def view_history(event):
            """View conversation history"""
            sender_id = event.sender_id
            conversations = db.get_user_conversations(sender_id, limit=20)
            
            if not conversations:
                await event.respond("📭 **No History**\n\nYour conversation history is empty. Send a message to get started!")
                return
            
            text = "📜 **Conversation History (Last 20)**\n"
            text += "━━━━━━━━━━━━━━━━━━━━\n\n"
            
            for msg in reversed(conversations):
                is_user = msg['from_user_id'] == sender_id
                sender_label = "👤 **You**" if is_user else "👑 **Owner**"
                time_str = datetime.fromisoformat(msg['sent_date']).strftime('%b %d, %H:%M')
                
                msg_content = msg['message']
                if len(msg_content) > 150:
                    msg_content = msg_content[:147] + "..."
                
                text += f"{sender_label}  |  _{time_str}_\n"
                text += f"└ `{msg_content}`\n\n"
            
            text += "━━━━━━━━━━━━━━━━━━━━\n"
            text += "💡 *Newest messages are at the bottom.*"
            
            await event.respond(text)
            db.mark_messages_as_read(sender_id, self.owner_id)
        
        @self.bot.on(events.NewMessage(pattern='/help', incoming=True, func=lambda e: e.is_private))
        async def help_command(event):
            """Help menu"""
            await event.respond(
                "**💬 Chat Bot Help**\n\n"
                "**Commands:**\n"
                "`/start` - Welcome & introduction\n"
                "`/status` - Check for new replies\n"
                "`/history` - View conversation\n"
                "`/help` - Show this help\n\n"
                "**How to send messages:**\n"
                "Simply type any message or send media files:\n\n"
                "**Text Messages:**\n"
                "Type your message and send\n\n"
                "**Media Files:**\n"
                "📷 Send photos\n"
                "🎬 Send videos\n"
                "📄 Send documents\n"
                "🎵 Send audio\n"
                "🎤 Send voice messages\n"
                "🎞️ Send GIFs\n"
                "🎨 Send stickers\n\n"
                "**Example:**\n"
                "Type: `Hi, can you help me?`"
            )
        
        @self.bot.on(events.CallbackQuery())
        async def handle_reply_callback(event):
            """Handle reply button clicks"""
            if event.sender_id != self.owner_id:
                await event.answer("❌ You don't have permission!", alert=True)
                return
            
            if not event.data.startswith(b'reply_'):
                return
            
            user_id = int(event.data.decode().replace('reply_', ''))
            
            await event.answer("Click to reply", alert=False)
            msg = await self.bot.send_message(
                self.owner_id,
                f"📤 **Reply to User {user_id}**\n\nType your message below:"
            )
            asyncio.create_task(self.delete_message_later(msg, delay=3))
            
            PENDING_REPLIES[self.owner_id] = user_id
            LOGGER(__name__).info(f"Owner awaiting reply for user {user_id}")
        
        @self.bot.on(events.NewMessage(incoming=True, func=lambda e: e.is_private and e.sender_id == self.owner_id and (e.media or e.text) and not e.text.startswith('/') and e.sender_id in PENDING_REPLIES))
        async def handle_owner_reply(event):
            """Handle owner typing a reply or sending media after clicking Reply button"""
            user_id = PENDING_REPLIES.pop(event.sender_id)
            message = event.text
            
            try:
                if event.media:
                    await self.bot.send_file(
                        user_id,
                        event.media,
                        caption=message
                    )
                    db.save_chat_message(self.owner_id, user_id, f"[Media] {message}" if message else "[Media sent]", 'owner')
                else:
                    await self.bot.send_message(
                        user_id,
                        message
                    )
                    db.save_chat_message(self.owner_id, user_id, message, 'owner')
                
                msg = await event.respond(f"✅ Sent to {user_id}")
                asyncio.create_task(self.delete_message_later(msg, delay=3))
                LOGGER(__name__).info(f"Owner replied to user {user_id}")
            except PeerIdInvalidError:
                await event.respond(f"❌ **User {user_id} not found or blocked the bot.**")
                PENDING_REPLIES[event.sender_id] = user_id
            except Exception as e:
                await event.respond(f"❌ **Error:** `{str(e)}`")
                LOGGER(__name__).error(f"Error replying to {user_id}: {e}")
                PENDING_REPLIES[event.sender_id] = user_id
        
        @self.bot.on(events.NewMessage(pattern='/reply (.+?) (.+)', incoming=True, func=lambda e: e.is_private))
        async def reply_command(event):
            """Reply to user (owner only)"""
            if event.sender_id != self.owner_id:
                await event.respond("❌ **You don't have permission to use this command.**")
                return
            
            import re
            match = re.match(r'/reply\s+(\d+)\s+(.*)', event.text, re.DOTALL)
            
            if not match:
                await event.respond("Usage: `/reply <user_id> <message>`")
                return
            
            user_id = int(match.group(1))
            message = match.group(2)
            
            try:
                await self.bot.send_message(
                    user_id,
                    message
                )
                db.save_chat_message(self.owner_id, user_id, message, 'admin')
                
                msg = await event.respond(f"✅ Sent to {user_id}")
                asyncio.create_task(self.delete_message_later(msg, delay=3))
                LOGGER(__name__).info(f"Owner {event.sender_id} replied to user {user_id}")
            except PeerIdInvalidError:
                await event.respond(f"❌ **User {user_id} not found or blocked the bot.**")
            except Exception as e:
                await event.respond(f"❌ **Error sending reply:** `{str(e)}`")
                LOGGER(__name__).error(f"Error replying to {user_id}: {e}")
        
        @self.bot.on(events.NewMessage(pattern=r'/read (\d+)', incoming=True, func=lambda e: e.is_private and e.sender_id == self.owner_id))
        async def read_user_messages(event):
            """Owner command to read unread messages from a specific user"""
            user_id = int(event.pattern_match.group(1))
            conversations = db.get_user_conversations(user_id, limit=50)
            
            if not conversations:
                await event.respond(f"📭 **No conversation history for User {user_id}.**")
                return
            
            # Filter for unread messages sent TO owner
            unread_msgs = [m for m in conversations if m['to_user_id'] == self.owner_id and m['is_read'] == 0]
            
            if not unread_msgs:
                await event.respond(f"✅ **No new unread messages from User {user_id}.**\n\n💡 Use `/history` or check `/mymessages` again.")
                return
            
            text = f"📖 **Unread Messages from User {user_id}**\n"
            text += "━━━━━━━━━━━━━━━━━━━━\n\n"
            
            for msg in unread_msgs:
                time_str = datetime.fromisoformat(msg['sent_date']).strftime('%b %d, %H:%M')
                text += f"📅 _{time_str}_\n"
                text += f"└ `{msg['message']}`\n\n"
            
            text += "━━━━━━━━━━━━━━━━━━━━\n"
            text += f"👉 Reply with `/reply {user_id} <message>`"
            
            await event.respond(text)
            # Mark these as read
            db.mark_messages_as_read(user_id, self.owner_id)

        @self.bot.on(events.NewMessage(pattern='/ownerhelp', incoming=True, func=lambda e: e.is_private and e.sender_id == self.owner_id))
        async def owner_help(event):
            """Owner help menu"""
            help_text = (
                "👑 **Owner Control Center**\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "📊 **GENERAL COMMANDS**\n"
                "• `/mymessages` - List all active conversations\n"
                "• `/read <user_id>` - Read unread messages from a user\n"
                "• `/ownerhelp` - View this help menu\n\n"
                "✉️ **MESSAGING**\n"
                "• `/send <user_id> <text>` - Send a new message\n"
                "• `/reply <user_id> <text>` - Quick reply to user\n"
                "• *Reply to any media* with `/send <user_id>` to forward it.\n\n"
                "🔍 **TOOLS**\n"
                "• `/searchuser <username/ID>` - Find user details\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "💡 **Pro-Tip:** Click the 'Reply' button on any new message notification for the fastest response workflow."
            )
            await event.respond(help_text)

        @self.bot.on(events.NewMessage(pattern='/mymessages', incoming=True, func=lambda e: e.is_private and e.sender_id == self.owner_id))
        async def view_all_messages(event):
            """View all user conversations (owner only) with pagination"""
            await self.show_messages_page(event, page=1)

        @self.bot.on(events.CallbackQuery(data=re.compile(br'^msgs_page_(\d+)')))
        async def handle_page_callback(event):
            """Handle pagination button clicks"""
            if event.sender_id != self.owner_id:
                await event.answer("❌ You don't have permission!", alert=True)
                return
            
            page = int(event.data.decode().split('_')[-1])
            await self.show_messages_page(event, page=page, edit=True)

        self.bot.show_messages_page = self.show_messages_page # Inject into bot instance to make it accessible

    async def show_messages_page(self, event, page=1, edit=False):
        """Helper to display a specific page of messages"""
        per_page = 10
        conversations = db.get_user_conversations(self.owner_id, limit=500)
        
        if not conversations:
            msg = "📭 **No Messages**\n\nYour database is currently empty."
            if edit: await event.edit(msg)
            else: await event.respond(msg)
            return
        
        # Group by user
        grouped = {}
        for msg in conversations:
            other_user = msg['from_user_id'] if msg['to_user_id'] == self.owner_id else msg['to_user_id']
            if other_user not in grouped:
                grouped[other_user] = []
            grouped[other_user].append(msg)
        
        sorted_users = sorted(grouped.items(), key=lambda x: x[1][-1]['sent_date'], reverse=True)
        total_pages = (len(sorted_users) + per_page - 1) // per_page
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        
        current_users = sorted_users[start_idx:end_idx]
        
        text = f"📬 **Active Conversations (Page {page}/{total_pages})**\n"
        text += "━━━━━━━━━━━━━━━━━━━━\n\n"
        
        for user_id, msgs in current_users:
            unread = sum(1 for m in msgs if m['to_user_id'] == self.owner_id and m['is_read'] == 0)
            last_msg = msgs[-1]['message']
            
            if len(last_msg) > 40:
                last_msg = last_msg[:37] + "..."
            
            status_icon = "🔵" if unread > 0 else "⚪️"
            text += f"{status_icon} **User:** `{user_id}`"
            if unread > 0:
                text += f" (**{unread} new**)"
            text += f"\n└ _{last_msg}_\n\n"
        
        text += "━━━━━━━━━━━━━━━━━━━━\n"
        text += "👉 Use `/reply <user_id> <message>` to respond.\n"
        text += "👉 Use `/read <user_id>` to see all unread messages."
        
        buttons = None
        row = []
        if page > 1:
            row.append(InlineKeyboardButton.callback("⬅️ Previous", f"msgs_page_{page-1}"))
        if page < total_pages:
            row.append(InlineKeyboardButton.callback("Next ➡️", f"msgs_page_{page+1}"))
        
        if row:
            buttons = [row]
        
        if edit:
            await event.edit(text, buttons=buttons)
        else:
            await event.respond(text, buttons=buttons)

        @self.bot.on(events.NewMessage(pattern='/ownerhelp', incoming=True, func=lambda e: e.is_private and e.sender_id == self.owner_id))
        async def old_owner_help(event):
            # This is a dummy to facilitate the replacement of the original functions
            pass
        
        @self.bot.on(events.NewMessage(pattern='/send (.+?) (.+)', incoming=True, func=lambda e: e.is_private and e.sender_id == self.owner_id))
        async def send_to_user(event):
            """Send message to any user (owner only)"""
            match = re.match(r'/send\s+(\d+)\s+(.*)', event.text, re.DOTALL)
            
            if not match:
                await event.respond(
                    "**📤 Send Message to User**\n\n"
                    "Usage: `/send <user_id> <message>`\n\n"
                    "Example: `/send 123456789 Hi! How are you?`\n\n"
                    "**To send media:**\n"
                    "Reply to media with `/send <user_id> [optional caption]`"
                )
                return
            
            user_id = int(match.group(1))
            message = match.group(2)
            
            try:
                # Check if replying to media
                if event.reply_to and (event.reply_to.media):
                    # Forward the media with message
                    await self.bot.send_file(user_id, event.reply_to.media, caption=message)
                    db.save_chat_message(self.owner_id, user_id, f"[Media] {message}" if message else "[Media sent]", 'owner')
                else:
                    # Send text message
                    await self.bot.send_message(user_id, message)
                    db.save_chat_message(self.owner_id, user_id, message, 'owner')
                
                db.add_user(user_id)
                
                msg = await event.respond(f"✅ Sent to {user_id}")
                asyncio.create_task(self.delete_message_later(msg, delay=3))
                LOGGER(__name__).info(f"Owner sent message to user {user_id}")
            except PeerIdInvalidError:
                await event.respond(f"❌ **User {user_id} not found or blocked the bot.**")
            except Exception as e:
                await event.respond(f"❌ **Error sending message:** `{str(e)}`")
                LOGGER(__name__).error(f"Error sending to {user_id}: {e}")
        
        @self.bot.on(events.NewMessage(pattern='/searchuser (.+)', incoming=True, func=lambda e: e.is_private and e.sender_id == self.owner_id))
        async def search_user(event):
            """Search for a user by username or ID (owner only)"""
            query = event.pattern_match.group(1).strip()
            
            try:
                # Try to get user entity
                user = await self.bot.get_entity(query)
                
                text = (
                    f"**👤 User Found:**\n\n"
                    f"ID: `{user.id}`\n"
                    f"Username: `@{user.username if user.username else 'N/A'}`\n"
                    f"Name: `{user.first_name or ''} {user.last_name or ''}`\n"
                    f"Is Bot: `{user.bot}`\n\n"
                    f"Use `/send {user.id} <message>` to send a message."
                )
                await event.respond(text)
            except Exception as e:
                await event.respond(f"❌ **User not found:** `{query}`\n\n`{str(e)}`")
        
    
    async def run(self):
        """Start the chat bot"""
        try:
            LOGGER(__name__).info("Starting Chat Bot...")
            # Try bot token first, then fallback to phone auth
            if PyroConf.BOT_TOKEN:
                await self.bot.start(bot_token=PyroConf.BOT_TOKEN)
            else:
                await self.bot.start()
            LOGGER(__name__).info("Chat Bot Started!")
            await self.bot.run_until_disconnected()
        except KeyboardInterrupt:
            pass
        except Exception as e:
            LOGGER(__name__).error(f"Chat Bot Error: {e}")
        finally:
            LOGGER(__name__).info("Chat Bot Stopped")

def start_http_server():
    """Start HTTP server in a separate thread"""
    try:
        from waitress import serve
        port = int(os.getenv('CHATBOT_PORT', os.getenv('PORT', 5001)))
        LOGGER(__name__).info(f"Starting HTTP server on port {port}")
        serve(
            lambda environ, start_response: app(environ, start_response),
            host='0.0.0.0',
            port=port,
            _quiet=True
        )
    except Exception as e:
        LOGGER(__name__).error(f"Error starting HTTP server: {e}")

def app(environ, start_response):
    """Simple WSGI app for health checks"""
    path = environ.get('PATH_INFO', '/')
    
    if path in ['/', '/health', '/ping']:
        status = '200 OK'
        response_headers = [('Content-Type', 'application/json')]
        start_response(status, response_headers)
        return [b'{"status": "ok", "message": "ChatBot is running"}']
    
    status = '404 Not Found'
    response_headers = [('Content-Type', 'text/plain')]
    start_response(status, response_headers)
    return [b'Not Found']

async def main():
    """Main entry point"""
    # Start HTTP server in background thread
    server_thread = threading.Thread(target=start_http_server, daemon=True)
    server_thread.start()
    LOGGER(__name__).info("HTTP server started in background thread")
    
    # Run the bot
    chat_bot = ChatBot()
    await chat_bot.run()

if __name__ == "__main__":
    asyncio.run(main())

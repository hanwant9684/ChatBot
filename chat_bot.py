# Copyright (C) @Wolfy004
# Chat Bot - Direct User Messaging System
# Channel: https://t.me/Wolfy004

import os
import asyncio
import re
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
            """Handle incoming messages from users (not from owner)"""
            sender_id = event.sender_id
            
            # Save message from user to owner
            db.save_chat_message(sender_id, self.owner_id, event.text, 'user')
            
            await event.respond("✅")
            
            # Notify owner with reply button
            try:
                buttons = [
                    [InlineKeyboardButton.callback("📤 Reply", f"reply_{sender_id}")]
                ]
                
                await self.bot.send_message(
                    self.owner_id,
                    f"{sender_id}\n\n{event.text}",
                    buttons=buttons
                )
            except Exception as e:
                LOGGER(__name__).error(f"Failed to notify owner: {e}")
            
            LOGGER(__name__).info(f"Message from {sender_id}: {event.text[:50]}")
        
        @self.bot.on(events.NewMessage(pattern='/start', incoming=True, func=lambda e: e.is_private))
        async def handle_start(event):
            """Handle /start command"""
            sender_id = event.sender_id
            db.add_user(sender_id, event.sender.username, event.sender.first_name, event.sender.last_name)
            
            await event.respond(
                "👋 **Welcome to the Chat Bot!**\n\n"
                "This bot allows you to send messages directly to the owner.\n\n"
                "**How to use:**\n"
                "1️⃣ Send any message and it will be delivered to the owner\n"
                "2️⃣ Use `/status` to check for replies\n"
                "3️⃣ Use `/history` to see your conversation\n\n"
                "**Available Commands:**\n"
                "`/start` - Welcome message\n"
                "`/status` - Check unread replies\n"
                "`/history` - View your conversation\n"
                "`/help` - Help menu"
            )
            LOGGER(__name__).info(f"User {sender_id} started the bot")
        
        @self.bot.on(events.NewMessage(pattern='/status', incoming=True, func=lambda e: e.is_private))
        async def check_status(event):
            """Check if there are new replies"""
            sender_id = event.sender_id
            conversations = db.get_user_conversations(sender_id, limit=50)
            
            unread = sum(1 for msg in conversations if msg['from_user_id'] == self.owner_id and msg['is_read'] == 0)
            
            if unread == 0:
                await event.respond("📭 **No new replies yet.**\n\nUse `/history` to see your conversation.")
            else:
                await event.respond(f"📬 **You have {unread} new reply(ies)!**\n\nUse `/history` to read them.")
            
            db.mark_messages_as_read(sender_id, self.owner_id)
        
        @self.bot.on(events.NewMessage(pattern='/history', incoming=True, func=lambda e: e.is_private))
        async def view_history(event):
            """View conversation history"""
            sender_id = event.sender_id
            conversations = db.get_user_conversations(sender_id, limit=20)
            
            if not conversations:
                await event.respond("📭 **No messages yet.**\n\nSend a message to start a conversation!")
                return
            
            text = "**📨 Your Conversation History:**\n\n"
            for msg in reversed(conversations):
                sender_tag = "📤 You" if msg['from_user_id'] == sender_id else "📥 Owner"
                time_str = datetime.fromisoformat(msg['sent_date']).strftime('%d/%m %H:%M')
                text += f"{sender_tag} ({time_str})\n`{msg['message'][:80]}{'...' if len(msg['message']) > 80 else ''}`\n\n"
            
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
                "**How to send a message:**\n"
                "Simply type any message and it will be sent to the owner.\n\n"
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
        
        @self.bot.on(events.NewMessage(incoming=True, func=lambda e: e.is_private and e.sender_id == self.owner_id and e.text and not e.text.startswith('/') and e.sender_id in PENDING_REPLIES))
        async def handle_owner_reply(event):
            """Handle owner typing a reply after clicking Reply button"""
            user_id = PENDING_REPLIES.pop(event.sender_id)
            message = event.text
            
            try:
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
        
        @self.bot.on(events.NewMessage(pattern='/mymessages', incoming=True, func=lambda e: e.is_private and e.sender_id == self.owner_id))
        async def view_all_messages(event):
            """View all user conversations (owner only)"""
            conversations = db.get_user_conversations(self.owner_id, limit=50)
            
            if not conversations:
                await event.respond("📭 **No messages yet.**")
                return
            
            # Group by user
            grouped = {}
            for msg in conversations:
                other_user = msg['from_user_id'] if msg['to_user_id'] == self.owner_id else msg['to_user_id']
                if other_user not in grouped:
                    grouped[other_user] = []
                grouped[other_user].append(msg)
            
            text = "**📨 All Conversations:**\n\n"
            for user_id, msgs in sorted(grouped.items(), key=lambda x: x[1][-1]['sent_date'], reverse=True)[:10]:
                unread = sum(1 for m in msgs if m['to_user_id'] == self.owner_id and m['is_read'] == 0)
                text += f"👤 **User {user_id}:** {len(msgs)} message(s)"
                if unread > 0:
                    text += f" 📬 {unread} unread"
                text += f"\n"
            
            text += "\nUse `/reply <user_id> <message>` to respond."
            await event.respond(text)
        
        @self.bot.on(events.NewMessage(pattern='/send (.+?) (.+)', incoming=True, func=lambda e: e.is_private and e.sender_id == self.owner_id))
        async def send_to_user(event):
            """Send message to any user (owner only)"""
            match = re.match(r'/send\s+(\d+)\s+(.*)', event.text, re.DOTALL)
            
            if not match:
                await event.respond(
                    "**📤 Send Message to User**\n\n"
                    "Usage: `/send <user_id> <message>`\n\n"
                    "Example: `/send 123456789 Hi! How are you?`"
                )
                return
            
            user_id = int(match.group(1))
            message = match.group(2)
            
            try:
                await self.bot.send_message(
                    user_id,
                    message
                )
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
        
        @self.bot.on(events.NewMessage(pattern='/ownerhelp', incoming=True, func=lambda e: e.is_private and e.sender_id == self.owner_id))
        async def owner_help(event):
            """Owner help menu"""
            await event.respond(
                "**👑 Owner Commands**\n\n"
                "**Message Management:**\n"
                "`/send <user_id> <message>` - Send message to any user\n"
                "`/searchuser <username or ID>` - Find a user\n"
                "`/mymessages` - View all conversations\n"
                "`/reply <user_id> <message>` - Reply to user\n\n"
                "**User Features:**\n"
                "`/start` - Welcome message\n"
                "`/status` - Check for replies\n"
                "`/history` - View conversation\n"
                "`/help` - User help"
            )
    
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

async def main():
    """Main entry point"""
    chat_bot = ChatBot()
    await chat_bot.run()

if __name__ == "__main__":
    asyncio.run(main())

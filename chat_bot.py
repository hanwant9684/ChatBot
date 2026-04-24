# Copyright (C) @Wolfy004
# Chat Bot - Direct User Messaging System
# Channel: https://t.me/Wolfy004

import os
import re
import asyncio
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
from telethon.errors import PeerIdInvalidError, FloodWaitError, UserIsBlockedError
from telethon.sessions import StringSession

from telethon_helpers import InlineKeyboardButton
from config import PyroConf
from logger import LOGGER
from database_sqlite import db


# ────────────────────────────────────────────────────────────────────────────────
# Constants & shared state
# ────────────────────────────────────────────────────────────────────────────────

# username of the companion "Restricted Content Saver" bot
SAVER_BOT_USERNAME = os.getenv("SAVER_BOT_USERNAME", "@restrictedcontent_save_bot")

# Pending reply state: owner_id -> target user_id awaiting reply
PENDING_REPLIES: dict[int, int] = {}

# Detect any URL in a message
URL_REGEX = re.compile(
    r'(?i)\b((?:https?://|t\.me/|telegram\.me/|www\.)[^\s<>"\']+)'
)


def message_has_link(text: Optional[str]) -> bool:
    """Return True if the text contains any URL we recognise."""
    if not text:
        return False
    return bool(URL_REGEX.search(text))


def link_choice_buttons():
    """Inline keyboard offered to a user who sent a link message."""
    return [
        [
            InlineKeyboardButton.callback("📂 Public Link", "link_public"),
            InlineKeyboardButton.callback("🔒 Private Link", "link_private"),
        ],
        [
            InlineKeyboardButton.callback("💬 Send Message to Owner", "link_owner"),
        ],
    ]


PUBLIC_LINK_REPLY = (
    "📂 **Public Channel Link Detected**\n"
    "━━━━━━━━━━━━━━━━━━━━\n\n"
    f"For **public** channel content, please use our dedicated saver bot:\n"
    f"👉 {SAVER_BOT_USERNAME}\n\n"
    "**How to use:**\n"
    "1️⃣ Open the bot above (tap the username).\n"
    "2️⃣ Press **Start**.\n"
    "3️⃣ Paste your public channel link.\n"
    "4️⃣ The bot will instantly extract and send the media to you.\n\n"
    "⚠️ *I have multiple bots — some may be temporarily down. "
    f"Please always use {SAVER_BOT_USERNAME} for public links.*"
)

PRIVATE_LINK_REPLY = (
    "🔒 **Private Channel Link Detected**\n"
    "━━━━━━━━━━━━━━━━━━━━\n\n"
    f"Private content needs a one-time login. Please open {SAVER_BOT_USERNAME} "
    "and follow these steps:\n\n"
    "1️⃣ Send `/login` to the saver bot.\n"
    "2️⃣ Send your phone number in **international format** "
    "(e.g. `+11234567890`).\n"
    "3️⃣ Complete the verification flow shown by the bot "
    "(OTP code, and 2FA password if you have one).\n"
    "4️⃣ Once logged in, paste your private channel link.\n"
    "5️⃣ The bot will fetch and deliver the media to you.\n\n"
    "🔐 *Your session is encrypted and used only to download "
    "the content you request.*"
)


# ────────────────────────────────────────────────────────────────────────────────
# WSGI health-check app (kept for cloud platform health probes)
# ────────────────────────────────────────────────────────────────────────────────

def health_app(environ, start_response):
    path = environ.get('PATH_INFO', '/')
    if path in ('/', '/health', '/ping'):
        start_response('200 OK', [('Content-Type', 'application/json')])
        return [b'{"status": "ok", "message": "ChatBot is running"}']
    start_response('404 Not Found', [('Content-Type', 'text/plain')])
    return [b'Not Found']


def start_http_server():
    try:
        from waitress import serve
        port = int(os.getenv('CHATBOT_PORT', os.getenv('PORT', 5000)))
        LOGGER(__name__).info(f"Starting HTTP server on port {port}")
        serve(health_app, host='0.0.0.0', port=port, _quiet=True)
    except Exception as e:
        LOGGER(__name__).error(f"Error starting HTTP server: {e}")


# ────────────────────────────────────────────────────────────────────────────────
# ChatBot
# ────────────────────────────────────────────────────────────────────────────────

class ChatBot:
    def __init__(self):
        session = (
            StringSession(PyroConf.SESSION_STRING)
            if PyroConf.SESSION_STRING else 'chat_bot'
        )
        self.bot = TelegramClient(
            session,
            PyroConf.API_ID,
            PyroConf.API_HASH,
        )
        self.owner_id = PyroConf.OWNER_ID
        # user_id → last link message text, used by "Send Message to Owner"
        self.pending_links: dict[int, str] = {}
        self.setup_handlers()

    # ── helpers ────────────────────────────────────────────────────────────────

    async def delete_message_later(self, message, delay: int = 3):
        """Delete a message after `delay` seconds (best-effort)."""
        try:
            await asyncio.sleep(delay)
            await self.bot.delete_messages(message.chat_id, message.id)
        except Exception as e:
            LOGGER(__name__).debug(f"Could not delete message: {e}")

    @staticmethod
    def _user_label(event) -> tuple[str, str]:
        sender = event.sender
        full_name = (
            f"{getattr(sender, 'first_name', '') or ''} "
            f"{getattr(sender, 'last_name', '') or ''}"
        ).strip() or "Unknown"
        username = (
            f"@{sender.username}"
            if getattr(sender, 'username', None) else "No Username"
        )
        return full_name, username

    def _is_owner(self, user_id: int) -> bool:
        return user_id == self.owner_id

    # ── handlers ───────────────────────────────────────────────────────────────

    def setup_handlers(self):

        # Gate every incoming private message from non-owners. Banned users
        # see the standard ban notice and are told about `/appeal`. Only
        # `/appeal <message>` is accepted from them, and only ONCE per ban.
        @self.bot.on(events.NewMessage(
            incoming=True, func=lambda e: e.is_private and e.sender_id != self.owner_id
        ))
        async def block_banned(event):
            sender_id = event.sender_id
            if not db.is_banned(sender_id):
                return  # not banned → fall through to other handlers

            text = (event.text or "").strip()
            appeal_match = re.match(r'^/appeal(?:@\w+)?(?:\s+(.+))?$', text, re.DOTALL)

            if appeal_match:
                # User is trying to submit an appeal.
                if db.has_used_appeal(sender_id):
                    try:
                        await event.respond(
                            "🚫 **You are banned from using this bot.**\n\n"
                            "You have already used your **one** appeal. "
                            "No further messages can be sent until the owner unbans you."
                        )
                    except Exception:
                        pass
                    raise events.StopPropagation

                appeal_text = (appeal_match.group(1) or "").strip()
                if not appeal_text:
                    try:
                        await event.respond(
                            "📝 **How to appeal**\n\n"
                            "You must explain **why** you should be unbanned. "
                            "Send your appeal like this:\n\n"
                            "`/appeal <your reason here>`\n\n"
                            "⚠️ This is your **only** appeal — make it count. "
                            "After it's submitted you cannot send anything else "
                            "until the owner unbans you."
                        )
                    except Exception:
                        pass
                    raise events.StopPropagation

                # Submit the appeal.
                full_name, username = self._user_label(event)
                db.add_user(
                    sender_id,
                    event.sender.username,
                    event.sender.first_name,
                    event.sender.last_name,
                )
                db.mark_appeal_used(sender_id)
                db.save_chat_message(sender_id, self.owner_id, appeal_text, 'appeal')

                try:
                    buttons = [[
                        InlineKeyboardButton.callback("✅ Unban", f"appeal_unban_{sender_id}"),
                        InlineKeyboardButton.callback("❌ Keep Banned", f"appeal_keep_{sender_id}"),
                    ], [
                        InlineKeyboardButton.callback("📤 Reply to User", f"reply_{sender_id}"),
                    ]]
                    await self.bot.send_message(
                        self.owner_id,
                        (
                            "🚨 **Ban Appeal Ticket**\n"
                            "━━━━━━━━━━━━━━━━━━━━\n"
                            f"👤 **From:** {full_name} ({username})\n"
                            f"🆔 **ID:** `{sender_id}`\n\n"
                            f"📝 **Reason:**\n{appeal_text}\n\n"
                            "This is the user's **only** allowed appeal. "
                            "Choose an action below."
                        ),
                        buttons=buttons,
                    )
                except Exception as e:
                    LOGGER(__name__).error(f"Could not notify owner of appeal: {e}")

                try:
                    await event.respond(
                        "✅ **Your appeal has been submitted.**\n\n"
                        "The owner will review it. You cannot send any more "
                        "messages until you are unbanned."
                    )
                except Exception:
                    pass

                LOGGER(__name__).info(f"Appeal submitted by banned user {sender_id}")
                raise events.StopPropagation

            # Any other message from a banned user → standard ban notice.
            if db.has_used_appeal(sender_id):
                notice = (
                    "🚫 **You are banned from using this bot.**\n\n"
                    "You have already used your **one** appeal. "
                    "No further messages can be sent until the owner unbans you."
                )
            else:
                notice = (
                    "🚫 **You are banned from using this bot.**\n\n"
                    "You may submit **one** appeal explaining why you should be "
                    "unbanned. Send it like this:\n\n"
                    "`/appeal <your reason here>`\n\n"
                    "⚠️ This is a **one-time** appeal. After it's submitted you "
                    "cannot send any more messages until the owner unbans you."
                )
            try:
                await event.respond(notice)
            except Exception:
                pass
            raise events.StopPropagation

        # ── Text messages (no command) ────────────────────────────────────────
        @self.bot.on(events.NewMessage(
            incoming=True,
            func=lambda e: (
                e.is_private
                and e.text
                and not e.text.startswith('/')
                and e.sender_id != self.owner_id
            )
        ))
        async def handle_text(event):
            sender_id = event.sender_id
            full_name, username = self._user_label(event)

            db.add_user(
                sender_id,
                event.sender.username,
                event.sender.first_name,
                event.sender.last_name,
            )

            # Link message → canned reply with choice buttons. Do NOT bother
            # the owner with these unless the user explicitly asks to.
            if message_has_link(event.text):
                db.save_chat_message(sender_id, self.owner_id, event.text, 'link')
                # Remember the original link so the user can forward it to the
                # owner via the "💬 Send Message to Owner" button below.
                self.pending_links[sender_id] = event.text
                await event.respond(
                    "🔗 **Link Received**\n\n"
                    "I see you sent a link. Tap the button below that matches "
                    "your link type and I'll show you exactly what to do.\n\n"
                    "💬 If you'd rather **explain something to the owner** "
                    "about this link, tap **Send Message to Owner** instead.",
                    buttons=link_choice_buttons(),
                )
                LOGGER(__name__).info(f"Link from {sender_id}: {event.text[:60]}")
                return

            # Regular text → save, ack the user, notify owner.
            db.save_chat_message(sender_id, self.owner_id, event.text, 'text')
            await event.respond("✅ **Message Delivered**")

            try:
                buttons = [[InlineKeyboardButton.callback(
                    "📤 Reply to User", f"reply_{sender_id}"
                )]]
                await self.bot.send_message(
                    self.owner_id,
                    (
                        f"📩 **New Message Received**\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"👤 **From:** {full_name} ({username})\n"
                        f"🆔 **ID:** `{sender_id}`\n\n"
                        f"📝 **Content:**\n_{event.text}_"
                    ),
                    buttons=buttons,
                )
            except Exception as e:
                LOGGER(__name__).error(f"Failed to notify owner: {e}")

            LOGGER(__name__).info(f"Message from {sender_id}: {event.text[:50]}")

        # ── Media messages ────────────────────────────────────────────────────
        @self.bot.on(events.NewMessage(
            incoming=True,
            func=lambda e: (
                e.is_private
                and not e.text
                and e.sender_id != self.owner_id
            )
        ))
        async def handle_media(event):
            sender_id = event.sender_id
            full_name, username = self._user_label(event)

            db.add_user(
                sender_id,
                event.sender.username,
                event.sender.first_name,
                event.sender.last_name,
            )

            # Detect media type
            media_type = 'media'
            media_description = '📁 Document'
            if event.photo:
                media_type, media_description = 'photo', '📷 Photo'
            elif event.video:
                media_type, media_description = 'video', '🎬 Video'
            elif event.audio:
                media_type, media_description = 'audio', '🎵 Audio'
            elif event.voice:
                media_type, media_description = 'voice', '🎤 Voice Message'
            elif event.video_note:
                media_type, media_description = 'video_note', '📹 Video Note'
            elif event.gif:
                media_type, media_description = 'gif', '🎞️ GIF'
            elif event.sticker:
                media_type, media_description = 'sticker', '🎨 Sticker'
            elif event.document:
                media_type = 'document'
                try:
                    fname = next(
                        (a.file_name for a in event.document.attributes
                         if hasattr(a, 'file_name')),
                        None
                    )
                except Exception:
                    fname = None
                media_description = f'📄 {fname}' if fname else '📄 Document'

            caption = event.message.text or media_description
            db.save_chat_message(sender_id, self.owner_id, caption, media_type)

            await event.respond(f"✅ **{media_description} Delivered**")

            try:
                await event.forward_to(self.owner_id)
                buttons = [[InlineKeyboardButton.callback(
                    "📤 Reply to User", f"reply_{sender_id}"
                )]]
                notification = (
                    f"📦 **New {media_description.upper()} Received**\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 **From:** {full_name} ({username})\n"
                    f"🆔 **ID:** `{sender_id}`"
                )
                if event.message.text:
                    notification += f"\n\n💬 **Caption:**\n_{event.message.text}_"
                await self.bot.send_message(
                    self.owner_id, notification, buttons=buttons
                )
            except Exception as e:
                LOGGER(__name__).error(f"Failed to forward media to owner: {e}")

            LOGGER(__name__).info(f"{media_type.upper()} from {sender_id}")

        # ── /start ────────────────────────────────────────────────────────────
        @self.bot.on(events.NewMessage(pattern=r'^/start(?:@\w+)?$',
                                        incoming=True,
                                        func=lambda e: e.is_private))
        async def handle_start(event):
            sender_id = event.sender_id
            db.add_user(
                sender_id,
                event.sender.username,
                event.sender.first_name,
                event.sender.last_name,
            )
            await event.respond(
                "👋 **Welcome to the Professional Chat Support Bot!**\n\n"
                "I am here to help you communicate directly with our team. "
                "Your messages are securely delivered and handled with care.\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "📊 **HOW IT WORKS**\n"
                "1️⃣ **Send Message:** Type anything or send a file.\n"
                "2️⃣ **Review:** The owner will review and reply.\n"
                "3️⃣ **Stay Notified:** You'll get a notification when they reply!\n\n"
                "🔗 **HAVE A LINK?**\n"
                f"For public channel content, use {SAVER_BOT_USERNAME}.\n"
                "For private channels, send `/login` to that bot first, "
                "then paste your link.\n\n"
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
            LOGGER(__name__).info(f"User {sender_id} started the bot")

        # ── /status ───────────────────────────────────────────────────────────
        @self.bot.on(events.NewMessage(pattern=r'^/status(?:@\w+)?$',
                                        incoming=True,
                                        func=lambda e: e.is_private))
        async def check_status(event):
            sender_id = event.sender_id
            conversations = db.get_user_conversations(sender_id, limit=50)
            unread = sum(
                1 for m in conversations
                if m['from_user_id'] == self.owner_id and m['is_read'] == 0
            )
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

        # ── /history ──────────────────────────────────────────────────────────
        @self.bot.on(events.NewMessage(pattern=r'^/history(?:@\w+)?$',
                                        incoming=True,
                                        func=lambda e: e.is_private))
        async def view_history(event):
            sender_id = event.sender_id
            conversations = db.get_user_conversations(sender_id, limit=20)
            if not conversations:
                await event.respond("📭 **No History**\n\nYour conversation history is empty.")
                return

            header = (
                "📜 **Your Recent Activity (Global)**"
                if self._is_owner(sender_id)
                else "📜 **Conversation History (Last 20)**"
            )
            text = f"{header}\n━━━━━━━━━━━━━━━━━━━━\n\n"

            for msg in reversed(conversations):
                if self._is_owner(sender_id):
                    other_party = (
                        msg['to_user_id']
                        if msg['from_user_id'] == self.owner_id
                        else msg['from_user_id']
                    )
                    sender_label = (
                        f"👑 **You** (to `{other_party}`)"
                        if msg['from_user_id'] == self.owner_id
                        else f"👤 **User** `{other_party}`"
                    )
                else:
                    is_user = msg['from_user_id'] == sender_id
                    sender_label = "👤 **You**" if is_user else "👑 **Owner**"

                try:
                    time_str = datetime.fromisoformat(msg['sent_date']).strftime('%b %d, %H:%M')
                except Exception:
                    time_str = msg['sent_date']

                content = msg['message']
                if len(content) > 150:
                    content = content[:147] + "..."

                text += f"{sender_label}  |  _{time_str}_\n└ `{content}`\n\n"

            text += "━━━━━━━━━━━━━━━━━━━━\n"
            text += (
                "💡 *Tip: Use /read <user_id> for a specific user's chat.*"
                if self._is_owner(sender_id)
                else "💡 *Newest messages are at the bottom.*"
            )

            await event.respond(text)
            if not self._is_owner(sender_id):
                db.mark_messages_as_read(sender_id, self.owner_id)

        # ── /help ─────────────────────────────────────────────────────────────
        @self.bot.on(events.NewMessage(pattern=r'^/help(?:@\w+)?$',
                                        incoming=True,
                                        func=lambda e: e.is_private))
        async def help_command(event):
            await event.respond(
                "💬 **Chat Bot — Full Help**\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "🛠 **COMMANDS**\n"
                "• `/start` — welcome & quick intro\n"
                "• `/status` — see how many unread replies you have\n"
                "• `/history` — view your last 20 messages with the owner\n"
                "• `/help` — open this help menu\n\n"
                "💌 **SENDING A MESSAGE**\n"
                "Just type whatever you want and hit send. "
                "I'll deliver it to the owner and confirm with "
                "**✅ Message Delivered**. The owner can reply at any time — "
                "use `/status` or `/history` to see their reply.\n\n"
                "📎 **SENDING MEDIA**\n"
                "I accept all common formats:\n"
                "• 📷 Photos  • 🎬 Videos  • 📄 Documents\n"
                "• 🎵 Audio  • 🎤 Voice notes\n"
                "• 🎞️ GIFs  • 🎨 Stickers  • 📹 Video notes\n"
                "Add a caption if you want to explain the file.\n\n"
                "🔗 **SENDING A LINK**\n"
                "If your message contains a Telegram link, I'll automatically "
                "show you three buttons:\n"
                "• **📂 Public Link** — instructions for public-channel content "
                f"(use {SAVER_BOT_USERNAME}, just paste the link).\n"
                "• **🔒 Private Link** — instructions for private channels "
                "(send `/login` to that bot, share your phone number in "
                "international format, complete OTP/2FA, then paste the link).\n"
                "• **💬 Send Message to Owner** — if you actually want to "
                "*talk to the owner* about the link instead of using the "
                "saver bot, tap this. Your message will be delivered "
                "directly and you can keep chatting normally afterwards.\n\n"
                "🚫 **IF YOU GET BANNED**\n"
                "You'll see a ban notice instead of normal replies. You may "
                "submit **one** appeal explaining why you should be unbanned "
                "by sending:\n"
                "`/appeal <your reason here>`\n"
                "After that one appeal, no further messages can be sent until "
                "the owner unbans you.\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "✨ *Tip: keep your message clear and respectful — that's the "
                "fastest way to get a reply.*"
            )

        # ── Callback: link choice ─────────────────────────────────────────────
        @self.bot.on(events.CallbackQuery(data=re.compile(br'^link_(public|private|owner)$')))
        async def handle_link_choice(event):
            choice = event.data.decode().split('_', 1)[1]
            if choice == 'public':
                await event.answer("Showing public-link instructions")
                await event.edit(PUBLIC_LINK_REPLY, buttons=link_choice_buttons())
                return
            if choice == 'private':
                await event.answer("Showing private-link instructions")
                await event.edit(PRIVATE_LINK_REPLY, buttons=link_choice_buttons())
                return

            # choice == 'owner' → forward the user's link to the owner so they
            # can have a real conversation about it.
            sender_id = event.sender_id
            link_text = self.pending_links.pop(sender_id, None)
            if not link_text:
                await event.answer(
                    "This link is no longer available. Please send your "
                    "message again.",
                    alert=True,
                )
                return

            full_name, username = self._user_label(event)
            db.add_user(
                sender_id,
                event.sender.username,
                getattr(event.sender, 'first_name', None),
                getattr(event.sender, 'last_name', None),
            )
            db.save_chat_message(sender_id, self.owner_id, link_text, 'text')

            try:
                buttons = [[InlineKeyboardButton.callback(
                    "📤 Reply to User", f"reply_{sender_id}"
                )]]
                await self.bot.send_message(
                    self.owner_id,
                    (
                        "💬 **New Message (with link)**\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"👤 **From:** {full_name} ({username})\n"
                        f"🆔 **ID:** `{sender_id}`\n\n"
                        f"📝 **Message:**\n{link_text}\n\n"
                        "_The user chose to send this directly to you instead "
                        "of using the public/private link helper._"
                    ),
                    buttons=buttons,
                )
            except Exception as e:
                LOGGER(__name__).error(f"Could not deliver link message to owner: {e}")
                await event.answer(
                    "Could not deliver your message right now. Please try again.",
                    alert=True,
                )
                return

            await event.answer("Sent to owner")
            try:
                await event.edit(
                    "✅ **Message Delivered to Owner**\n\n"
                    "Your link and any extra context have been forwarded. "
                    "You can keep messaging here — the owner will reply soon."
                )
            except Exception:
                pass
            LOGGER(__name__).info(f"User {sender_id} forwarded link message to owner")

        # ── Callback: Appeal decision (owner only) ────────────────────────────
        @self.bot.on(events.CallbackQuery(data=re.compile(br'^appeal_(unban|keep)_(\d+)$')))
        async def handle_appeal_decision(event):
            if event.sender_id != self.owner_id:
                await event.answer("❌ You don't have permission!", alert=True)
                return
            decision, uid = event.data.decode().split('_')[1], int(event.data.decode().split('_')[2])
            if decision == 'unban':
                if db.unban_user(uid):
                    await event.answer("User unbanned")
                    try:
                        await event.edit(
                            f"✅ **Appeal accepted — user `{uid}` unbanned.**"
                        )
                    except Exception:
                        pass
                    try:
                        await self.bot.send_message(
                            uid,
                            "✅ **Your appeal was accepted. You have been unbanned.**\n\n"
                            "You can now use the bot normally."
                        )
                    except Exception:
                        pass
                    LOGGER(__name__).info(f"Owner accepted appeal for user {uid}")
                else:
                    await event.answer("Could not unban", alert=True)
            else:
                await event.answer("Decision saved")
                try:
                    await event.edit(
                        f"❌ **Appeal rejected — user `{uid}` remains banned.**"
                    )
                except Exception:
                    pass
                LOGGER(__name__).info(f"Owner rejected appeal for user {uid}")

        # ── Callback: Reply to user ───────────────────────────────────────────
        @self.bot.on(events.CallbackQuery(data=re.compile(br'^reply_(\d+)$')))
        async def handle_reply_callback(event):
            if event.sender_id != self.owner_id:
                await event.answer("❌ You don't have permission!", alert=True)
                return
            user_id = int(event.data.decode().split('_', 1)[1])
            await event.answer("Now type your reply")
            msg = await self.bot.send_message(
                self.owner_id,
                f"📤 **Reply to User {user_id}**\n\n"
                "Type your message (or send media) below.\n"
                "Send `/cancel` to abort."
            )
            asyncio.create_task(self.delete_message_later(msg, delay=4))
            PENDING_REPLIES[self.owner_id] = user_id
            LOGGER(__name__).info(f"Owner awaiting reply for user {user_id}")

        # ── /cancel pending reply ─────────────────────────────────────────────
        @self.bot.on(events.NewMessage(pattern=r'^/cancel(?:@\w+)?$',
                                        incoming=True,
                                        func=lambda e: e.is_private and e.sender_id == self.owner_id))
        async def cancel_reply(event):
            if PENDING_REPLIES.pop(event.sender_id, None) is not None:
                await event.respond("✅ Pending reply cancelled.")
            else:
                await event.respond("ℹ️ No pending reply to cancel.")

        # ── Owner reply (text/media follow-up after pressing the button) ──────
        @self.bot.on(events.NewMessage(
            incoming=True,
            func=lambda e: (
                e.is_private
                and e.sender_id == self.owner_id
                and e.sender_id in PENDING_REPLIES
                and (e.media or e.text)
                and not (e.text and e.text.startswith('/'))
            )
        ))
        async def handle_owner_reply(event):
            user_id = PENDING_REPLIES.pop(event.sender_id)
            text = event.text or ""
            try:
                if event.media:
                    await self.bot.send_file(user_id, event.media, caption=text)
                    db.save_chat_message(
                        self.owner_id, user_id,
                        f"[Media] {text}" if text else "[Media sent]",
                        'owner'
                    )
                else:
                    await self.bot.send_message(user_id, text)
                    db.save_chat_message(self.owner_id, user_id, text, 'owner')

                msg = await event.respond(f"✅ Sent to {user_id}")
                asyncio.create_task(self.delete_message_later(msg, delay=3))
                LOGGER(__name__).info(f"Owner replied to user {user_id}")
            except (PeerIdInvalidError, UserIsBlockedError):
                await event.respond(f"❌ **User {user_id} not found or blocked the bot.**")
            except Exception as e:
                await event.respond(f"❌ **Error:** `{e}`")
                LOGGER(__name__).error(f"Error replying to {user_id}: {e}")
                PENDING_REPLIES[event.sender_id] = user_id

        # ── /reply <id> <text> (legacy quick reply) ───────────────────────────
        @self.bot.on(events.NewMessage(pattern=r'^/reply\s+(\d+)\s+(.+)',
                                        incoming=True,
                                        func=lambda e: e.is_private))
        async def reply_command(event):
            if event.sender_id != self.owner_id:
                await event.respond("❌ **You don't have permission to use this command.**")
                return
            match = re.match(r'^/reply\s+(\d+)\s+(.*)', event.text, re.DOTALL)
            if not match:
                await event.respond("Usage: `/reply <user_id> <message>`")
                return
            user_id = int(match.group(1))
            message = match.group(2)
            try:
                await self.bot.send_message(user_id, message)
                db.save_chat_message(self.owner_id, user_id, message, 'admin')
                msg = await event.respond(f"✅ Sent to {user_id}")
                asyncio.create_task(self.delete_message_later(msg, delay=3))
                LOGGER(__name__).info(f"Owner replied to user {user_id}")
            except (PeerIdInvalidError, UserIsBlockedError):
                await event.respond(f"❌ **User {user_id} not found or blocked the bot.**")
            except Exception as e:
                await event.respond(f"❌ **Error sending reply:** `{e}`")
                LOGGER(__name__).error(f"Error replying to {user_id}: {e}")

        # ── /read <user_id> (owner) ───────────────────────────────────────────
        @self.bot.on(events.NewMessage(pattern=r'^/read\s+(\d+)$',
                                        incoming=True,
                                        func=lambda e: e.is_private and e.sender_id == self.owner_id))
        async def read_user_messages(event):
            user_id = int(event.pattern_match.group(1))
            conversations = db.get_user_conversations(user_id, limit=50)
            if not conversations:
                await event.respond(f"📭 **No conversation history for User {user_id}.**")
                return

            all_msgs = [
                m for m in conversations
                if m['from_user_id'] == user_id or m['to_user_id'] == user_id
            ]
            all_msgs.sort(key=lambda x: x['sent_date'])
            if not all_msgs:
                await event.respond(f"📭 **No conversation history for User {user_id}.**")
                return

            display_msgs = all_msgs[-15:]
            unread_count = sum(
                1 for m in all_msgs
                if m['to_user_id'] == self.owner_id and m['is_read'] == 0
            )

            header = f"📖 **Conversation with User {user_id}**"
            if unread_count:
                header += f" ({unread_count} New)"
            text = f"{header}\n━━━━━━━━━━━━━━━━━━━━\n\n"

            for msg in display_msgs:
                try:
                    time_str = datetime.fromisoformat(msg['sent_date']).strftime('%H:%M')
                except Exception:
                    time_str = msg['sent_date']
                is_owner = msg['from_user_id'] == self.owner_id
                sender_icon = "👑 You" if is_owner else "👤 User"
                text += f"_{time_str}_ | **{sender_icon}**\n└ `{msg['message']}`\n\n"

            text += "━━━━━━━━━━━━━━━━━━━━\n"
            text += f"👉 Reply with `/reply {user_id} <message>`"
            await event.respond(text)
            db.mark_messages_as_read(user_id, self.owner_id)

        # ── /ownerhelp ────────────────────────────────────────────────────────
        @self.bot.on(events.NewMessage(pattern=r'^/ownerhelp(?:@\w+)?$',
                                        incoming=True,
                                        func=lambda e: e.is_private and e.sender_id == self.owner_id))
        async def owner_help(event):
            await event.respond(
                "👑 **Owner Control Center**\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "📊 **CONVERSATIONS**\n"
                "• `/mymessages` — list all active conversations (paged)\n"
                "• `/read <user_id>` — read a user's recent messages\n"
                "• `/cancel` — cancel a pending reply\n\n"
                "✉️ **MESSAGING**\n"
                "• `/send <user_id> <text>` — send any user a new message\n"
                "• `/reply <user_id> <text>` — quick reply to a user\n"
                "• Reply to media with `/send <user_id>` to forward it.\n"
                "• `/broadcast <text>` — send a message to **all** users\n\n"
                "🛡 **MODERATION**\n"
                "• `/ban <user_id>` — block a user from messaging the bot\n"
                "• `/unban <user_id>` — unblock a user\n"
                "• `/banned` — list banned users\n"
                "  ↳ Each banned user can submit **one** appeal message. "
                "You'll receive it with **Unban / Keep Banned** buttons.\n\n"
                "🔍 **TOOLS**\n"
                "• `/searchuser <username|id>` — find a user's details\n"
                "• `/users` — total number of users\n"
                "• `/stats` — bot statistics\n"
                "• `/ownerhelp` — show this menu\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "💡 *Tip:* tap **Reply to User** on any notification for the "
                "fastest workflow."
            )

        # ── /mymessages (owner) ───────────────────────────────────────────────
        @self.bot.on(events.NewMessage(pattern=r'^/mymessages(?:@\w+)?$',
                                        incoming=True,
                                        func=lambda e: e.is_private and e.sender_id == self.owner_id))
        async def view_all_messages(event):
            await self.show_messages_page(event, page=1)

        @self.bot.on(events.CallbackQuery(data=re.compile(br'^msgs_page_(\d+)$')))
        async def handle_page_callback(event):
            if event.sender_id != self.owner_id:
                await event.answer("❌ You don't have permission!", alert=True)
                return
            page = int(event.data.decode().split('_')[-1])
            await self.show_messages_page(event, page=page, edit=True)

        # ── /send (owner) ─────────────────────────────────────────────────────
        @self.bot.on(events.NewMessage(pattern=r'^/send\s+(\d+)\s+(.+)',
                                        incoming=True,
                                        func=lambda e: e.is_private and e.sender_id == self.owner_id))
        async def send_to_user(event):
            match = re.match(r'^/send\s+(\d+)\s+(.*)', event.text, re.DOTALL)
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
                if event.reply_to and event.reply_to.media:
                    await self.bot.send_file(user_id, event.reply_to.media, caption=message)
                    db.save_chat_message(
                        self.owner_id, user_id,
                        f"[Media] {message}" if message else "[Media sent]",
                        'owner'
                    )
                else:
                    await self.bot.send_message(user_id, message)
                    db.save_chat_message(self.owner_id, user_id, message, 'owner')

                db.add_user(user_id)
                msg = await event.respond(f"✅ Sent to {user_id}")
                asyncio.create_task(self.delete_message_later(msg, delay=3))
                LOGGER(__name__).info(f"Owner sent message to user {user_id}")
            except (PeerIdInvalidError, UserIsBlockedError):
                await event.respond(f"❌ **User {user_id} not found or blocked the bot.**")
            except Exception as e:
                await event.respond(f"❌ **Error sending message:** `{e}`")
                LOGGER(__name__).error(f"Error sending to {user_id}: {e}")

        # ── /searchuser <username|id> (owner) ─────────────────────────────────
        @self.bot.on(events.NewMessage(pattern=r'^/searchuser\s+(.+)',
                                        incoming=True,
                                        func=lambda e: e.is_private and e.sender_id == self.owner_id))
        async def search_user(event):
            query = event.pattern_match.group(1).strip()
            try:
                target = int(query) if query.lstrip('-').isdigit() else query
                user = await self.bot.get_entity(target)
                username = f"@{user.username}" if getattr(user, 'username', None) else "N/A"
                first = getattr(user, 'first_name', '') or ''
                last = getattr(user, 'last_name', '') or ''
                full_name = (first + " " + last).strip() or "Unknown"
                await event.respond(
                    "**👤 User Found:**\n\n"
                    f"• ID: `{user.id}`\n"
                    f"• Username: `{username}`\n"
                    f"• Name: `{full_name}`\n"
                    f"• Is Bot: `{getattr(user, 'bot', False)}`\n\n"
                    f"Use `/send {user.id} <message>` to send a message."
                )
            except Exception as e:
                await event.respond(f"❌ **User not found:** `{query}`\n\n`{e}`")

        # ── /ban <user_id> (owner) ────────────────────────────────────────────
        @self.bot.on(events.NewMessage(pattern=r'^/ban\s+(\d+)$',
                                        incoming=True,
                                        func=lambda e: e.is_private and e.sender_id == self.owner_id))
        async def ban_user_cmd(event):
            user_id = int(event.pattern_match.group(1))
            if user_id == self.owner_id:
                await event.respond("❌ You can't ban yourself.")
                return
            db.add_user(user_id)
            ok = db.ban_user(user_id)
            if ok:
                await event.respond(
                    f"🚫 **User `{user_id}` has been banned.**\n"
                    "They can no longer send messages to this bot."
                )
                try:
                    await self.bot.send_message(
                        user_id,
                        "🚫 **You have been banned from using this bot.**"
                    )
                except Exception:
                    pass
                LOGGER(__name__).info(f"Owner banned user {user_id}")
            else:
                await event.respond(f"❌ Failed to ban user `{user_id}` (no such user?).")

        # ── /unban <user_id> (owner) ──────────────────────────────────────────
        @self.bot.on(events.NewMessage(pattern=r'^/unban\s+(\d+)$',
                                        incoming=True,
                                        func=lambda e: e.is_private and e.sender_id == self.owner_id))
        async def unban_user_cmd(event):
            user_id = int(event.pattern_match.group(1))
            ok = db.unban_user(user_id)
            if ok:
                await event.respond(f"✅ **User `{user_id}` has been unbanned.**")
                try:
                    await self.bot.send_message(
                        user_id,
                        "✅ **You have been unbanned. You can use the bot again.**"
                    )
                except Exception:
                    pass
                LOGGER(__name__).info(f"Owner unbanned user {user_id}")
            else:
                await event.respond(f"❌ Failed to unban user `{user_id}` (no such user?).")

        # ── /banned (owner) ───────────────────────────────────────────────────
        @self.bot.on(events.NewMessage(pattern=r'^/banned(?:@\w+)?$',
                                        incoming=True,
                                        func=lambda e: e.is_private and e.sender_id == self.owner_id))
        async def banned_list(event):
            try:
                conn = db._get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    'SELECT user_id, username, first_name FROM users '
                    'WHERE is_banned = 1 ORDER BY user_id'
                )
                rows = cursor.fetchall()
                conn.close()
            except Exception as e:
                await event.respond(f"❌ Error: `{e}`")
                return
            if not rows:
                await event.respond("✅ **No banned users.**")
                return
            text = "🚫 **Banned Users**\n━━━━━━━━━━━━━━━━━━━━\n\n"
            for r in rows:
                uname = f"@{r['username']}" if r['username'] else "—"
                fname = r['first_name'] or "Unknown"
                text += f"• `{r['user_id']}`  {fname}  ({uname})\n"
            await event.respond(text)

        # ── /users (owner) ────────────────────────────────────────────────────
        @self.bot.on(events.NewMessage(pattern=r'^/users(?:@\w+)?$',
                                        incoming=True,
                                        func=lambda e: e.is_private and e.sender_id == self.owner_id))
        async def users_count(event):
            ids = db.get_all_users()
            await event.respond(
                "👥 **Active Users**\n"
                f"Total non-banned users: **{len(ids)}**"
            )

        # ── /stats (owner) ────────────────────────────────────────────────────
        @self.bot.on(events.NewMessage(pattern=r'^/stats(?:@\w+)?$',
                                        incoming=True,
                                        func=lambda e: e.is_private and e.sender_id == self.owner_id))
        async def stats_cmd(event):
            try:
                stats = db.get_stats() or {}
            except Exception as e:
                await event.respond(f"❌ Error fetching stats: `{e}`")
                return
            uptime = ""
            try:
                from time import time as _now
                seconds = int(_now() - PyroConf.BOT_START_TIME)
                from telethon_helpers import format_time
                uptime = format_time(seconds)
            except Exception:
                pass
            text = "📊 **Bot Statistics**\n━━━━━━━━━━━━━━━━━━━━\n\n"
            for key, value in stats.items():
                pretty = key.replace('_', ' ').title()
                text += f"• **{pretty}:** `{value}`\n"
            if uptime:
                text += f"\n⏱ **Uptime:** `{uptime}`"
            await event.respond(text)

        # ── /broadcast <text> (owner) ─────────────────────────────────────────
        @self.bot.on(events.NewMessage(pattern=r'^/broadcast\s+(.+)',
                                        incoming=True,
                                        func=lambda e: e.is_private and e.sender_id == self.owner_id))
        async def broadcast_cmd(event):
            match = re.match(r'^/broadcast\s+(.*)', event.text, re.DOTALL)
            if not match:
                await event.respond("Usage: `/broadcast <message>`")
                return
            text = match.group(1).strip()
            if not text:
                await event.respond("Usage: `/broadcast <message>`")
                return

            user_ids = db.get_all_users()
            if not user_ids:
                await event.respond("ℹ️ No users to broadcast to.")
                return

            status = await event.respond(
                f"📣 **Broadcasting to {len(user_ids)} users…**"
            )

            sent, failed = 0, 0
            for uid in user_ids:
                if uid == self.owner_id:
                    continue
                try:
                    await self.bot.send_message(uid, text)
                    sent += 1
                except FloodWaitError as fw:
                    LOGGER(__name__).warning(f"FloodWait {fw.seconds}s during broadcast")
                    await asyncio.sleep(fw.seconds + 1)
                    try:
                        await self.bot.send_message(uid, text)
                        sent += 1
                    except Exception:
                        failed += 1
                except Exception:
                    failed += 1
                # gentle pacing to avoid hitting limits
                await asyncio.sleep(0.05)

            db.save_broadcast(text, self.owner_id, len(user_ids), sent)
            await status.edit(
                "📣 **Broadcast Complete**\n"
                f"• Sent: `{sent}`\n"
                f"• Failed: `{failed}`\n"
                f"• Total: `{len(user_ids)}`"
            )

    # ── helper used by /mymessages ────────────────────────────────────────────

    async def show_messages_page(self, event, page: int = 1, edit: bool = False):
        per_page = 10
        conversations = db.get_user_conversations(self.owner_id, limit=500)

        if not conversations:
            msg = "📭 **No Messages**\n\nYour database is currently empty."
            if edit:
                await event.edit(msg)
            else:
                await event.respond(msg)
            return

        grouped: dict[int, list] = {}
        for m in conversations:
            other = m['to_user_id'] if m['from_user_id'] == self.owner_id else m['from_user_id']
            grouped.setdefault(other, []).append(m)

        for uid in grouped:
            grouped[uid].sort(key=lambda x: x['sent_date'])

        sorted_users = sorted(
            grouped.items(), key=lambda x: x[1][-1]['sent_date'], reverse=True
        )
        total_pages = max(1, (len(sorted_users) + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        current_users = sorted_users[start_idx:end_idx]

        text = f"📬 **Active Conversations (Page {page}/{total_pages})**\n"
        text += "━━━━━━━━━━━━━━━━━━━━\n\n"

        for user_id, msgs in current_users:
            unread = sum(
                1 for m in msgs
                if m['to_user_id'] == self.owner_id and m['is_read'] == 0
            )
            last = msgs[-1]
            last_msg = last['message']
            last_sender_id = last['from_user_id']
            sender_prefix = "👑 You: " if last_sender_id == self.owner_id else "👤 "
            display_msg = last_msg if len(last_msg) <= 40 else last_msg[:37] + "..."
            status_icon = "🔵" if unread > 0 else "⚪️"

            text += f"{status_icon} **User:** `{user_id}`"
            if unread > 0:
                text += f" (**{unread} new**)"
            text += f"\n└ {sender_prefix}_{display_msg}_\n\n"

        text += "━━━━━━━━━━━━━━━━━━━━\n"
        text += "👉 Use `/reply <user_id> <message>` to respond.\n"
        text += "👉 Use `/read <user_id>` to see all unread messages."

        row = []
        if page > 1:
            row.append(InlineKeyboardButton.callback("⬅️ Previous", f"msgs_page_{page-1}"))
        if page < total_pages:
            row.append(InlineKeyboardButton.callback("Next ➡️", f"msgs_page_{page+1}"))
        buttons = [row] if row else None

        if edit:
            await event.edit(text, buttons=buttons)
        else:
            await event.respond(text, buttons=buttons)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def cleanup_task(self):
        """Background task to clean up old messages every 6 hours."""
        while True:
            try:
                LOGGER(__name__).info("Starting scheduled database cleanup…")
                count = db.cleanup_old_messages(days=7)
                LOGGER(__name__).info(f"Scheduled cleanup finished. Deleted {count} messages.")
            except Exception as e:
                LOGGER(__name__).error(f"Error in background cleanup task: {e}")
            await asyncio.sleep(6 * 3600)

    async def run(self):
        try:
            LOGGER(__name__).info("Starting Chat Bot…")
            if PyroConf.BOT_TOKEN:
                await self.bot.start(bot_token=PyroConf.BOT_TOKEN)
            else:
                await self.bot.start()
            LOGGER(__name__).info("Chat Bot Started!")
            asyncio.create_task(self.cleanup_task())
            await self.bot.run_until_disconnected()
        except KeyboardInterrupt:
            pass
        except Exception as e:
            LOGGER(__name__).error(f"Chat Bot Error: {e}")
        finally:
            LOGGER(__name__).info("Chat Bot Stopped")


# ────────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ────────────────────────────────────────────────────────────────────────────────

async def main():
    server_thread = threading.Thread(target=start_http_server, daemon=True)
    server_thread.start()
    LOGGER(__name__).info("HTTP server started in background thread")

    chat_bot = ChatBot()
    await chat_bot.run()


if __name__ == "__main__":
    asyncio.run(main())

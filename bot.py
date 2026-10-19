import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path

from aiohttp import web
from telegram import (
    BotCommand,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonCommands,
    Update,
)
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# -------------------------
# تنظیمات
# -------------------------
OWNER_ID = 6073046430
OWNER_DISPLAY_NAME = "Ả̸̱̻͕̜͔̗̹͗̐̏̄͜͝͝M̶͋̅͠"
DATA_FILE = Path(os.environ.get("DATA_FILE", "bot_data.json"))
ACTIVE_WINDOW_SECONDS = 3600

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# داده‌ها
shared_music = {}
pending_uploads = {}
anon_conversations = {}
users_in_anon_mode = set()
admin_reply_target = {}
msg_to_sender_map = {}
active_dialogs = {}

# وب‌سرور Render
web_runner = None


# -------------------------
# ذخیره‌سازی محلی
# -------------------------
def load_data():
    global shared_music, anon_conversations, active_dialogs

    if not DATA_FILE.exists():
        return

    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        shared_music = data.get("shared_music", {})
        anon_conversations = data.get("anon_conversations", {})
        active_dialogs = data.get("active_dialogs", {})
    except Exception:
        logger.exception("خواندن فایل داده ناموفق بود؛ با داده خالی ادامه می‌دهیم.")


def save_data():
    data = {
        "shared_music": shared_music,
        "anon_conversations": anon_conversations,
        "active_dialogs": active_dialogs,
    }
    try:
        DATA_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        logger.exception("ذخیره اطلاعات ناموفق بود.")


# -------------------------
# وب‌سرور
# -------------------------
async def health_check(request):
    return web.Response(text="Bot is running!")


async def post_init(application: Application):
    global web_runner

    # راه‌اندازی وب‌سرور در همان حلقهٔ asyncio ربات
    app = web.Application()
    app.router.add_get("/", health_check)
    web_runner = web.AppRunner(app)
    await web_runner.setup()

    port = int(os.environ.get("PORT", "8080"))
    site = web.TCPSite(web_runner, "0.0.0.0", port)
    await site.start()
    logger.info("Health server is listening on port %s", port)

    owner_commands = [
        BotCommand("start", "شروع / منو"),
        BotCommand("anon_inbox", "صندوق پیام ناشناس"),
        BotCommand("anon_link", "لینک ناشناس من"),
        BotCommand("active_chats", "گفتگوهای فعال"),
        BotCommand("my_musics", "موزیک‌های من"),
        BotCommand("help", "راهنما"),
    ]
    public_commands = [
        BotCommand("start", "شروع / منو"),
        BotCommand("my_link", "لینک ناشناس من"),
        BotCommand("help", "راهنما"),
    ]

    try:
        await application.bot.set_my_commands(public_commands)
        await application.bot.set_my_commands(
            owner_commands,
            scope=BotCommandScopeChat(chat_id=OWNER_ID),
        )
        await application.bot.set_chat_menu_button(
            chat_id=OWNER_ID,
            menu_button=MenuButtonCommands(),
        )
    except Exception:
        logger.exception("تنظیم منوی فرمان‌های تلگرام ناموفق بود.")


async def post_shutdown(application: Application):
    global web_runner
    if web_runner:
        await web_runner.cleanup()
        web_runner = None


# -------------------------
# صفحهٔ شروع و دکمه‌ها
# -------------------------
def public_start_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎭 ارسال پیام ناشناس", callback_data="go_anon_owner")],
        [
            InlineKeyboardButton("🔗 دریافت لینک ناشناس من", callback_data="btn_get_link"),
            InlineKeyboardButton("📩 پیام‌های من", callback_data="btn_my_messages"),
        ],
    ])


def owner_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 صندوق پیام‌های ناشناس", callback_data="owner_inbox")],
        [InlineKeyboardButton("🟢 گفتگوهای فعال", callback_data="owner_active")],
        [InlineKeyboardButton("🔗 لینک ناشناس من", callback_data="btn_get_link")],
    ])


def username_text(username):
    return f"@{username}" if username else "ندارد"


def format_remaining(seconds):
    if seconds is None:
        return "♾ همیشگی"
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days} روز و {hours} ساعت"
    if hours:
        return f"{hours} ساعت و {minutes} دقیقه"
    if minutes:
        return f"{minutes} دقیقه و {secs} ثانیه"
    return f"{secs} ثانیه"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    # /start لغو حالت ارسال یا پاسخ قبلی
    users_in_anon_mode.discard(user.id)
    if user.id == OWNER_ID:
        admin_reply_target.pop(OWNER_ID, None)

    args = context.args

    # لینک ارسال پیام ناشناس به مالک
    if args and args[0] == "anon_chat":
        if user.id == OWNER_ID:
            await message.reply_text("این لینک متعلق به خود شماست؛ آن را با دیگران به اشتراک بگذارید.")
            return

        users_in_anon_mode.add(user.id)
        await message.reply_text(
            f"شما در حال ارسال پیام ناشناس به {OWNER_DISPLAY_NAME} هستید! 🎭\n\n"
            "پیام متنی یا فایل خود را همین‌جا بفرستید.\n"
            "برای لغو، /start را بزنید.",
            reply_markup=public_start_keyboard(),
        )
        return

    # لینک موزیک
    if args and args[0].startswith("music_"):
        music_id = args[0]
        item = shared_music.get(music_id)

        if not item:
            await message.reply_text("❌ این لینک منقضی، حذف‌شده یا نامعتبر است.")
            return

        expires_at = item.get("expires_at")
        if expires_at and time.time() >= expires_at:
            shared_music.pop(music_id, None)
            save_data()
            await message.reply_text("❌ اعتبار این لینک تمام شده است.")
            return

        try:
            sent = await send_music_to_user(context, user.id, item)
            if expires_at:
                context.job_queue.run_once(
                    delete_message_job,
                    max(1, expires_at - time.time()),
                    data={"chat_id": user.id, "message_id": sent.message_id},
                )
        except Exception:
            logger.exception("ارسال موزیک ناموفق بود.")
            await message.reply_text("❌ ارسال فایل ممکن نشد. لطفاً کمی بعد دوباره تلاش کنید.")
        return

    if user.id == OWNER_ID:
        await message.reply_text(
            f"سلام، {OWNER_DISPLAY_NAME}.\n\n"
            "از منو یا دکمه‌های زیر برای مدیریت پیام‌ها و موزیک‌ها استفاده کنید.",
            reply_markup=owner_keyboard(),
        )
    else:
        await message.reply_text(
            "سلام! به ربات پیام ناشناس و موزیک خوش آمدید.\n\n"
            "از دکمه‌های زیر استفاده کنید:",
            reply_markup=public_start_keyboard(),
        )


async def send_music_to_user(context, user_id, item):
    file_type = item.get("file_type")
    file_id = item["file_id"]
    caption = item.get("caption") or f"🎵 {item.get('name', 'موزیک')}"

    if file_type == "audio":
        return await context.bot.send_audio(user_id, audio=file_id, caption=caption)
    if file_type == "voice":
        return await context.bot.send_voice(user_id, voice=file_id, caption=caption)
    return await context.bot.send_document(user_id, document=file_id, caption=caption)


async def send_anon_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    bot = await context.bot.get_me()
    link = f"https://t.me/{bot.username}?start=anon_chat"
    await update.effective_message.reply_text(
        f"🔗 لینک ارسال پیام ناشناس:\n{link}\n\n"
        "هرکسی این لینک را باز کند می‌تواند برای مالک ربات پیام بفرستد."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id == OWNER_ID:
        text = (
            "راهنمای مالک:\n"
            "• /anon_inbox — صندوق پیام‌ها\n"
            "• /active_chats — گفتگوهای فعال در یک ساعت اخیر\n"
            "• /anon_link — لینک پیام ناشناس\n"
            "• /my_musics — مدیریت موزیک‌ها\n"
            "• برای پاسخ، از دکمهٔ پاسخ در پیام دریافتی استفاده کنید."
        )
    else:
        text = (
            "راهنمای استفاده:\n"
            "• «ارسال پیام ناشناس» را بزنید و پیام‌تان را بفرستید.\n"
            "• «دریافت لینک ناشناس من» لینک ارسال پیام به مالک ربات را می‌دهد.\n"
            "• برای خروج از حالت ارسال پیام، /start را بزنید."
        )
    await update.effective_message.reply_text(text)


# -------------------------
# صندوق و گفتگوهای فعال مالک
# -------------------------
async def anon_inbox(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    if not anon_conversations:
        await update.effective_message.reply_text("📭 صندوق پیام‌ها خالی است.")
        return

    keyboard = []
    for uid, item in anon_conversations.items():
        name = item.get("name", "کاربر")
        username = username_text(item.get("username"))
        count = len(item.get("messages", []))
        keyboard.append([
            InlineKeyboardButton(
                f"👤 {name} ({username}) — {count} پیام",
                callback_data=f"view_anon_{uid}",
            )
        ])

    await update.effective_message.reply_text(
        "📥 صندوق پیام‌های ناشناس:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def active_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    active = [
        (uid, item)
        for uid, item in active_dialogs.items()
        if time.time() - float(item.get("last_ts", 0)) <= ACTIVE_WINDOW_SECONDS
    ]
    active.sort(key=lambda pair: float(pair[1].get("last_ts", 0)), reverse=True)

    if not active:
        await update.effective_message.reply_text(
            "🟢 در یک ساعت اخیر گفتگوی فعالی ثبت نشده است."
        )
        return

    keyboard = []
    for uid, item in active:
        name = item.get("name", "کاربر")
        username = username_text(item.get("username"))
        count = item.get("count", 0)
        keyboard.append([
            InlineKeyboardButton(
                f"🟢 {name} ({username}) — {count} پیام",
                callback_data=f"view_anon_{uid}",
            )
        ])

    await update.effective_message.reply_text(
        "🟢 گفتگوهای فعال در یک ساعت اخیر:\n"
        "برای دیدن پیام‌های ثبت‌شده روی نام کاربر بزنید.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def view_conversation(query, target_id):
    item = anon_conversations.get(target_id)
    if not item:
        await query.edit_message_text("❌ سابقهٔ این گفتگو پیدا نشد.")
        return

    messages = item.get("messages", [])[-10:]
    lines = []
    for entry in messages:
        if isinstance(entry, dict):
            direction = "کاربر" if entry.get("from") == "user" else OWNER_DISPLAY_NAME
            lines.append(f"• {direction}: {entry.get('text', '[پیام چندرسانه‌ای]')}")
        else:
            lines.append(f"• {entry}")

    body = "\n".join(lines) if lines else "هنوز پیامی ثبت نشده است."
    text = (
        f"نام: {item.get('name', 'کاربر')}\n"
        f"یوزرنیم: {username_text(item.get('username'))}\n"
        f"شناسهٔ تلگرام: {target_id}\n\n"
        f"پیام‌های اخیر:\n{body}"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ پاسخ به گفتگو", callback_data=f"prep_reply_{target_id}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_to_inbox")],
    ])
    await query.edit_message_text(text, reply_markup=keyboard)


# -------------------------
# دکمه‌های تعاملی
# -------------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    data = query.data or ""

    if data == "go_anon_owner":
        if user.id == OWNER_ID:
            await query.message.reply_text("برای تست ارسال پیام ناشناس از یک حساب دیگر استفاده کنید.")
            return
        users_in_anon_mode.add(user.id)
        await query.message.reply_text(
            f"شما در حال ارسال پیام ناشناس به {OWNER_DISPLAY_NAME} هستید. 🎭\n"
            "پیام متنی یا فایل خود را بفرستید.\n"
            "برای لغو، /start را بزنید."
        )
        return

    if data == "btn_get_link":
        bot = await context.bot.get_me()
        link = f"https://t.me/{bot.username}?start=anon_chat"
        await query.message.reply_text(f"🔗 لینک ارسال پیام ناشناس:\n{link}")
        return

    if data == "btn_my_messages":
        if user.id == OWNER_ID:
            await query.message.reply_text("پیام‌های دریافتی را از «صندوق پیام‌های ناشناس» ببینید.")
        else:
            item = anon_conversations.get(str(user.id)) or anon_conversations.get(user.id)
            if not item or not item.get("messages"):
                await query.message.reply_text("پیامی برای نمایش پیدا نشد.")
            else:
                entries = item["messages"][-5:]
                lines = [
                    f"• {m.get('text', '[پیام چندرسانه‌ای]') if isinstance(m, dict) else m}"
                    for m in entries
                ]
                await query.message.reply_text("📩 پیام‌های ثبت‌شدهٔ شما:\n" + "\n".join(lines))
        return

    if user.id != OWNER_ID:
        return

    if data == "owner_inbox":
        await anon_inbox_from_query(query)
        return

    if data == "owner_active":
        await active_chats_from_query(query)
        return

    if data.startswith("view_anon_"):
        try:
            target_id = int(data.removeprefix("view_anon_"))
        except ValueError:
            await query.edit_message_text("شناسهٔ کاربر نامعتبر است.")
            return
        await view_conversation(query, target_id)
        return

    if data.startswith("prep_reply_"):
        try:
            target_id = int(data.removeprefix("prep_reply_"))
        except ValueError:
            await query.message.reply_text("شناسهٔ کاربر نامعتبر است.")
            return
        admin_reply_target[OWNER_ID] = target_id
        await query.message.reply_text(
            "✍️ پاسخ خود را به‌صورت پیام متنی یا فایل بفرستید.\n"
            "برای لغو، /start را بزنید."
        )
        return

    if data == "back_to_inbox":
        await anon_inbox_from_query(query)
        return

    if data.startswith("duration_"):
        await save_music_duration(query, context)
        return

    if data.startswith("delete_music_"):
        await delete_music(query, context)
        return


async def anon_inbox_from_query(query):
    if not anon_conversations:
        await query.message.reply_text("📭 صندوق پیام‌ها خالی است.")
        return

    keyboard = []
    for uid, item in anon_conversations.items():
        keyboard.append([
            InlineKeyboardButton(
                f"👤 {item.get('name', 'کاربر')} ({username_text(item.get('username'))})",
                callback_data=f"view_anon_{uid}",
            )
        ])
    await query.message.reply_text(
        "📥 صندوق پیام‌های ناشناس:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def active_chats_from_query(query):
    active = [
        (uid, item)
        for uid, item in anon_conversations.items()
        if time.time() - float(item.get("last_ts", 0)) <= ACTIVE_WINDOW_SECONDS
    ]
    if not active:
        await query.message.reply_text("🟢 در یک ساعت اخیر گفتگوی فعالی ثبت نشده است.")
        return

    keyboard = []
    for uid, item in active:
        keyboard.append([
            InlineKeyboardButton(
                f"🟢 {item.get('name', 'کاربر')} ({username_text(item.get('username'))})",
                callback_data=f"view_anon_{uid}",
            )
        ])
    await query.message.reply_text(
        "🟢 گفتگوهای فعال در یک ساعت اخیر:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# -------------------------
# پیام‌های ورودی و پاسخ مالک
# -------------------------
def get_message_description(message):
    if message.text:
        return message.text[:1000]
    if message.caption:
        return f"[رسانه] {message.caption[:900]}"
    if message.photo:
        return "[عکس]"
    if message.voice:
        return "[پیام صوتی]"
    if message.audio:
        return "[فایل صوتی]"
    if message.video:
        return "[ویدیو]"
    if message.document:
        return f"[فایل: {message.document.file_name or 'بدون نام'}]"
    if message.sticker:
        return "[استیکر]"
    return "[پیام چندرسانه‌ای]"


def ensure_conversation(user):
    key = str(user.id)
    if key not in anon_conversations:
        anon_conversations[key] = {
            "name": user.full_name,
            "username": user.username,
            "messages": [],
            "last_ts": time.time(),
            "count": 0,
        }
    item = anon_conversations[key]
    item["name"] = user.full_name
    item["username"] = user.username
    return key, item


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    # مالک در حال پاسخ دادن
    if user.id == OWNER_ID:
        target_id = admin_reply_target.get(OWNER_ID)

        if message.reply_to_message:
            mapped = msg_to_sender_map.get(message.reply_to_message.message_id)
            if mapped:
                target_id = mapped

        if target_id:
            key = str(target_id)
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text=f"💬 پیام جدید از طرف {OWNER_DISPLAY_NAME}:",
                )
                await context.bot.copy_message(
                    chat_id=target_id,
                    from_chat_id=message.chat_id,
                    message_id=message.message_id,
                )
                item = anon_conversations.get(key)
                if item:
                    item.setdefault("messages", []).append({
                        "from": "owner",
                        "text": get_message_description(message),
                        "ts": time.time(),
                    })
                    item["last_ts"] = time.time()
                    item["count"] = item.get("count", 0) + 1
                    save_data()
                admin_reply_target.pop(OWNER_ID, None)
                await message.reply_text("✅ پاسخ ارسال شد.")
            except Exception:
                logger.exception("ارسال پاسخ مالک ناموفق بود.")
                await message.reply_text("❌ پاسخ ارسال نشد؛ ممکن است کاربر ربات را مسدود کرده باشد.")
            return

        # فایل صوتی مالک برای ساخت لینک موزیک
        if message.audio or message.voice or (
            message.document
            and message.document.mime_type
            and message.document.mime_type.startswith("audio/")
        ):
            await receive_music(message, context)
        return

    # پیام کاربر بدون فعال‌کردن حالت ناشناس نادیده گرفته می‌شود
    if user.id not in users_in_anon_mode:
        return

    key, item = ensure_conversation(user)
    description = get_message_description(message)
    item.setdefault("messages", []).append({
        "from": "user",
        "text": description,
        "ts": time.time(),
    })
    item["last_ts"] = time.time()
    item["count"] = item.get("count", 0) + 1
    save_data()

    header = (
        "📩 پیام ناشناس جدید\n\n"
        f"👤 نام فرستنده: {user.full_name}\n"
        f"🔗 یوزرنیم: {username_text(user.username)}\n"
        f"🆔 شناسهٔ تلگرام: {user.id}"
    )
    await context.bot.send_message(chat_id=OWNER_ID, text=header)

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✍️ پاسخ به این گفتگو", callback_data=f"prep_reply_{user.id}")
    ]])
    copied = await context.bot.copy_message(
        chat_id=OWNER_ID,
        from_chat_id=message.chat_id,
        message_id=message.message_id,
        reply_markup=keyboard,
    )
    msg_to_sender_map[copied.message_id] = user.id

    await message.reply_text("✅ پیام شما ارسال شد. می‌توانید پیام دیگری هم بفرستید.")


# -------------------------
# موزیک
# -------------------------
async def receive_music(message, context):
    if message.audio:
        file_id = message.audio.file_id
        file_type = "audio"
        name = message.audio.title or message.audio.file_name or "فایل صوتی"
    elif message.voice:
        file_id = message.voice.file_id
        file_type = "voice"
        name = "پیام صوتی"
    elif message.document and message.document.mime_type and message.document.mime_type.startswith("audio/"):
        file_id = message.document.file_id
        file_type = "document"
        name = message.document.file_name or "فایل صوتی"
    else:
        return

    temp_id = uuid.uuid4().hex[:8]
    pending_uploads[temp_id] = {
        "file_id": file_id,
        "file_type": file_type,
        "name": name,
        "caption": message.caption,
        "owner_chat_id": message.chat_id,
        "owner_message_id": message.message_id,
    }

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("۱ ساعت", callback_data=f"duration_{temp_id}_3600"),
            InlineKeyboardButton("۶ ساعت", callback_data=f"duration_{temp_id}_21600"),
        ],
        [
            InlineKeyboardButton("۱۲ ساعت", callback_data=f"duration_{temp_id}_43200"),
            InlineKeyboardButton("۲۴ ساعت", callback_data=f"duration_{temp_id}_86400"),
        ],
        [
            InlineKeyboardButton("۳ روز", callback_data=f"duration_{temp_id}_259200"),
            InlineKeyboardButton("دائمی", callback_data=f"duration_{temp_id}_0"),
        ],
    ])
    await message.reply_text(
        f"🎵 فایل «{name}» دریافت شد. مدت اعتبار لینک را انتخاب کنید:",
        reply_markup=keyboard,
    )


async def save_music_duration(query, context):
    if query.from_user.id != OWNER_ID:
        return

    try:
        _, temp_id, seconds_text = query.data.split("_")
        seconds = int(seconds_text)
    except (ValueError, AttributeError):
        await query.message.reply_text("درخواست نامعتبر است.")
        return

    info = pending_uploads.pop(temp_id, None)
    if not info:
        await query.message.reply_text("⚠️ اطلاعات این فایل پیدا نشد؛ لطفاً دوباره فایل را بفرستید.")
        return

    music_id = f"music_{uuid.uuid4().hex[:10]}"
    expires_at = time.time() + seconds if seconds else None
    shared_music[music_id] = {
        **info,
        "created_at": time.time(),
        "duration_sec": seconds or None,
        "expires_at": expires_at,
    }
    save_data()

    bot = await context.bot.get_me()
    link = f"https://t.me/{bot.username}?start={music_id}"
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🗑 حذف و ابطال لینک", callback_data=f"delete_music_{music_id}")
    ]])

    await query.message.reply_text(
        f"✅ لینک ساخته شد\n"
        f"🎵 نام: {info['name']}\n"
        f"⏳ اعتبار: {format_remaining(seconds or None)}\n"
        f"🔗 {link}",
        reply_markup=keyboard,
    )

    if seconds:
        context.job_queue.run_once(
            expire_music_job,
            seconds,
            data={"music_id": music_id},
        )


async def expire_music_job(context: ContextTypes.DEFAULT_TYPE):
    music_id = context.job.data["music_id"]
    item = shared_music.get(music_id)
    if not item:
        return

    if item.get("expires_at") and time.time() >= item["expires_at"]:
        shared_music.pop(music_id, None)
        save_data()
        try:
            await context.bot.delete_message(
                chat_id=item["owner_chat_id"],
                message_id=item["owner_message_id"],
            )
        except Exception:
            pass


async def delete_message_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    try:
        await context.bot.delete_message(
            chat_id=data["chat_id"],
            message_id=data["message_id"],
        )
    except Exception:
        pass


async def delete_music(query, context):
    if query.from_user.id != OWNER_ID:
        return

    music_id = query.data.removeprefix("delete_music_")
    item = shared_music.pop(music_id, None)
    if not item:
        await query.edit_message_text("این لینک قبلاً حذف یا باطل شده است.")
        return

    save_data()
    try:
        await context.bot.delete_message(
            chat_id=item["owner_chat_id"],
            message_id=item["owner_message_id"],
        )
    except Exception:
        pass
    await query.edit_message_text("🗑 موزیک حذف شد و لینک آن باطل گردید.")


async def my_musics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    now = time.time()
    active = []
    for music_id, item in list(shared_music.items()):
        expires_at = item.get("expires_at")
        if expires_at and now >= expires_at:
            shared_music.pop(music_id, None)
            continue
        active.append((music_id, item))

    save_data()
    if not active:
        await update.effective_message.reply_text("📭 موزیک فعالی وجود ندارد.")
        return

    bot = await context.bot.get_me()
    for music_id, item in active:
        expires_at = item.get("expires_at")
        remaining = format_remaining(expires_at - now) if expires_at else "♾ همیشگی"
        link = f"https://t.me/{bot.username}?start={music_id}"
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑 حذف و ابطال", callback_data=f"delete_music_{music_id}")
        ]])
        await update.effective_message.reply_text(
            f"🎵 {item.get('name', 'موزیک')}\n"
            f"⏳ زمان باقی‌مانده: {remaining}\n"
            f"🔗 {link}",
            reply_markup=keyboard,
        )


# -------------------------
# راه‌اندازی
# -------------------------
def main():
    load_data()

    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("متغیر محیطی BOT_TOKEN در Render تنظیم نشده است.")

    application = (
        ApplicationBuilder()
        .token(token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # فرمان‌ها
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("anon_link", send_anon_link))
    application.add_handler(CommandHandler("my_link", send_anon_link))
    application.add_handler(CommandHandler("anon_inbox", anon_inbox))
    application.add_handler(CommandHandler("active_chats", active_chats))
    application.add_handler(CommandHandler("my_musics", my_musics))

    # callback ها
    application.add_handler(CallbackQueryHandler(button_handler))

    # پیام‌ها: یک handler عمومی که هم متن و هم رسانه را می‌گیرد
    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            message_handler,
        )
    )

    logger.info("Starting bot polling.")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

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
ANON_LABEL = "🎭 ناشناس"
DATA_FILE = Path(os.environ.get("DATA_FILE", "bot_data.json"))
ACTIVE_WINDOW_SECONDS = 3600

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

shared_music = {}
pending_uploads = {}
anon_conversations = {}          # کلید: "گیرنده:فرستنده"
anon_mode = {}                   # user_id -> target_id (گیرنده‌ی پیام‌هایش)
reply_target = {}                # user_id -> user_id (پاسخ به این نفر)
msg_to_sender_map = {}           # message_id -> (فرستنده، گیرنده)

web_runner = None


# -------------------------
# ذخیره‌سازی
# -------------------------
def load_data():
    global shared_music, anon_conversations
    if not DATA_FILE.exists():
        return
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        shared_music = data.get("shared_music", {})
        anon_conversations = data.get("anon_conversations", {})
    except Exception:
        logger.exception("خواندن فایل داده ناموفق بود.")


def save_data():
    try:
        DATA_FILE.write_text(
            json.dumps(
                {"shared_music": shared_music, "anon_conversations": anon_conversations},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        logger.exception("ذخیره اطلاعات ناموفق بود.")


# -------------------------
# وب‌سرور سلامت (برای Render و UptimeRobot)
# -------------------------
async def health_check(request):
    return web.Response(text="Bot is running!")


async def post_init(application: Application):
    global web_runner
    app = web.Application()
    app.router.add_get("/", health_check)
    web_runner = web.AppRunner(app)
    await web_runner.setup()
    site = web.TCPSite(web_runner, "0.0.0.0", int(os.environ.get("PORT", "8080")))
    await site.start()
    logger.info("Health server on port %s", os.environ.get("PORT", "8080"))

    owner_commands = [
        BotCommand("start", "شروع / منو"),
        BotCommand("anon_inbox", "صندوق پیام ناشناس"),
        BotCommand("my_link", "لینک ناشناس من"),
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
            owner_commands, scope=BotCommandScopeChat(chat_id=OWNER_ID)
        )
        await application.bot.set_chat_menu_button(
            chat_id=OWNER_ID, menu_button=MenuButtonCommands()
        )
    except Exception:
        logger.exception("تنظیم منو ناموفق بود.")


async def post_shutdown(application: Application):
    global web_runner
    if web_runner:
        await web_runner.cleanup()
        web_runner = None


# -------------------------
# ابزارها
# -------------------------
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


def conv_key(target_id, sender_id):
    return f"{target_id}:{sender_id}"


async def send_my_link(update_or_query, context, user_id):
    bot = await context.bot.get_me()
    link = f"https://t.me/{bot.username}?start=anon_{user_id}"
    text = (
        f"🔗 لینک ناشناس شخصی شما:\n{link}\n\n"
        "هر کس این لینک را باز کند می‌تواند بدون لو دادن هویت برای شما پیام بفرستد،\n"
        "و شما می‌توانید به او پاسخ دهید."
    )
    if hasattr(update_or_query, "message") and getattr(update_or_query.message, "reply_text", None):
        await update_or_query.message.reply_text(text)
    else:
        await update_or_query.reply_text(text)


# -------------------------
# /start
# -------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    # خروج از حالت‌های قبلی
    anon_mode.pop(user.id, None)
    reply_target.pop(user.id, None)

    args = context.args

    # لینک ناشناس شخصی: anon_<شناسه گیرنده>
    if args and (args[0].startswith("anon_") or args[0] == "anon_chat"):
        raw = args[0].replace("anon_", "")
        target_id = OWNER_ID if raw == "chat" else (int(raw) if raw.isdigit() else None)

        if not target_id:
            await message.reply_text("❌ لینک نامعتبر است.")
            return
        if target_id == user.id:
            await message.reply_text("😅 این لینک خودِ شماست! آن را برای دوستانت بفرست، نه خودت.")
            return

        try:
            target_user = await context.bot.get_chat(target_id)
            target_name = target_user.full_name
        except Exception:
            await message.reply_text("❌ صاحب این لینک در دسترس نیست.")
            return

        anon_mode[user.id] = target_id
        display = OWNER_DISPLAY_NAME if target_id == OWNER_ID else ANON_LABEL
        await message.reply_text(
            f"شما در حال ارسال پیام ناشناس به {display} هستید! 🎭\n\n"
            "پیام متنی یا فایل خود را همین‌جا بفرستید؛ هویت شما پنهان می‌ماند.\n"
            "برای لغو، /start را بزنید."
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
            await message.reply_text("❌ ارسال فایل ممکن نشد؛ کمی بعد دوباره تلاش کنید.")
        return

    # استارت معمولی: هیچ حالتی فعال نمی‌شود
    if user.id == OWNER_ID:
        await message.reply_text(
            f"سلام، {OWNER_DISPLAY_NAME}!\nاز منو یا دکمه‌های زیر استفاده کنید.",
            reply_markup=owner_keyboard(),
        )
    else:
        await message.reply_text(
            "سلام! به ربات پیام ناشناس و موزیک خوش آمدید.\n\n"
            "• «ارسال پیام ناشناس» برای پیام به معرفی‌کنندهٔ ربات\n"
            "• «دریافت لینک ناشناس من» برای گرفتن لینک اختصاصی خودتان",
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


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id == OWNER_ID:
        text = (
            "راهنمای مالک:\n"
            "• /anon_inbox — صندوق پیام‌هایی که برای شما فرستاده شده\n"
            "• /active_chats — گفتگوهای فعال یک ساعت اخیر\n"
            "• /my_link — لینک ناشناس شما\n"
            "• /my_musics — مدیریت موزیک‌ها\n"
            "• پاسخ: روی پیام دریافتی Reply بزنید یا از دکمهٔ «✍️ پاسخ»."
        )
    else:
        text = (
            "راهنما:\n"
            "• /my_link — گرفتن لینک ناشناس شخصی شما (برای دوستانتان)\n"
            "• با لینک دیگران می‌توانید به آن‌ها پیام ناشناس بدهید\n"
            "• برای پاسخ به پیام دریافتی، دکمهٔ «✍️ پاسخ» را بزنید\n"
            "• برای خروج از حالت ناشناس، /start را بزنید."
        )
    await update.effective_message.reply_text(text)


# -------------------------
# صندوق و گفتگوهای فعال (فقط مالک)
# -------------------------
def owner_conversations():
    return [
        (k, v) for k, v in anon_conversations.items()
        if k.split(":")[0] == str(OWNER_ID)
    ]


async def show_owner_inbox(message):
    convs = owner_conversations()
    if not convs:
        await message.reply_text("📭 صندوق پیام‌ها خالی است.")
        return
    keyboard = []
    for k, item in convs:
        count = item.get("count", 0)
        keyboard.append([InlineKeyboardButton(
            f"👤 {item.get('name', 'کاربر')} ({username_text(item.get('username'))}) — {count} پیام",
            callback_data=f"view_anon_{k.split(':')[1]}",
        )])
    await message.reply_text("📥 صندوق پیام‌های ناشناس:", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_owner_active(message):
    now = time.time()
    active = [
        (k, v) for k, v in owner_conversations()
        if now - float(v.get("last_ts", 0)) <= ACTIVE_WINDOW_SECONDS
    ]
    active.sort(key=lambda p: float(p[1].get("last_ts", 0)), reverse=True)
    if not active:
        await message.reply_text("🟢 در یک ساعت اخیر گفتگوی فعالی ثبت نشده است.")
        return
    keyboard = []
    for k, item in active:
        count = item.get("count", 0)
        keyboard.append([InlineKeyboardButton(
            f"🟢 {item.get('name', 'کاربر')} ({username_text(item.get('username'))}) — {count} پیام",
            callback_data=f"view_anon_{k.split(':')[1]}",
        )])
    await message.reply_text(
        "🟢 گفتگوهای فعال یک ساعت اخیر:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def view_conversation(query, sender_id):
    item = anon_conversations.get(conv_key(OWNER_ID, sender_id))
    if not item:
        await query.edit_message_text("❌ سابقهٔ این گفتگو پیدا نشد.")
        return
    lines = []
    for entry in item.get("messages", [])[-10:]:
        if isinstance(entry, dict):
            who = item.get("name", "کاربر") if entry.get("from") == "user" else OWNER_DISPLAY_NAME
            lines.append(f"• {who}: {entry.get('text', '[رسانه]')}")
        else:
            lines.append(f"• {entry}")
    body = "\n".join(lines) or "هنوز پیامی ثبت نشده است."
    text = (
        f"نام: {item.get('name', 'کاربر')}\n"
        f"یوزرنیم: {username_text(item.get('username'))}\n"
        f"شناسه: {sender_id}\n\nپیام‌های اخیر:\n{body}"
    )
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ پاسخ به گفتگو", callback_data=f"prep_reply_{sender_id}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_to_inbox")],
    ]))


# -------------------------
# دکمه‌ها
# -------------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    data = query.data or ""

    if data == "go_anon_owner":
        if user.id == OWNER_ID:
            await query.message.reply_text("این گزینه برای تست از حساب دیگر استفاده می‌شود.")
            return
        anon_mode[user.id] = OWNER_ID
        await query.message.reply_text(
            f"شما در حال ارسال پیام ناشناس به {OWNER_DISPLAY_NAME} هستید. 🎭\n"
            "پیام خود را بفرستید.\nبرای لغو، /start را بزنید."
        )
        return

    if data == "btn_get_link":
        await send_my_link(query, context, user.id)
        return

    if data == "btn_my_messages":
        key = conv_key(user.id, user.id)
        mine = [
            (k, v) for k, v in anon_conversations.items()
            if k.split(":")[0] == str(user.id)
        ]
        if user.id == OWNER_ID or not mine:
            await query.message.reply_text("📩 پیامی برای نمایش پیدا نشد.")
        else:
            lines = []
            for k, item in mine:
                for m in item.get("messages", [])[-5:]:
                    if isinstance(m, dict):
                        who = item.get("name", "کاربر") if m.get("from") == "user" else ANON_LABEL
                        lines.append(f"• {who}: {m.get('text', '[رسانه]')}")
            await query.message.reply_text("📩 پیام‌های شما:\n" + "\n".join(lines))
        return

    if user.id != OWNER_ID:
        # پاسخ کاربران عادی به پیام دریافتی
        if data.startswith("prep_reply_"):
            reply_target[user.id] = int(data.removeprefix("prep_reply_"))
            await query.message.reply_text(
                "✍️ پاسخ خود را بفرستید (متنی یا فایل). گیرنده آن را ناشناس دریافت می‌کند.\n"
                "برای لغو، /start را بزنید."
            )
        return

    # --- مالک ---
    if data == "owner_inbox":
        await show_owner_inbox(query.message)
        return
    if data == "owner_active":
        await show_owner_active(query.message)
        return
    if data.startswith("view_anon_"):
        await view_conversation(query, int(data.removeprefix("view_anon_")))
        return
    if data.startswith("prep_reply_"):
        reply_target[OWNER_ID] = int(data.removeprefix("prep_reply_"))
        await query.message.reply_text(
            "✍️ پاسخ خود را بفرستید.\nبرای لغو، /start را بزنید."
        )
        return
    if data == "back_to_inbox":
        await show_owner_inbox(query.message)
        return
    if data.startswith("duration_"):
        await save_music_duration(query, context)
        return
    if data.startswith("delete_music_"):
        await delete_music(query, context)
        return


# -------------------------
# پیام‌ها
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


async def deliver_anon_message(context, sender, message, target_id):
    """ارسال پیام ناشناس از sender به target_id + ثبت در تاریخچه."""
    key = conv_key(target_id, sender.id)
    item = anon_conversations.setdefault(key, {
        "name": sender.full_name,
        "username": sender.username,
        "messages": [],
        "last_ts": time.time(),
        "count": 0,
    })
    item["name"] = sender.full_name
    item["username"] = sender.username
    item.setdefault("messages", []).append({
        "from": "user", "text": get_message_description(message), "ts": time.time(),
    })
    item["last_ts"] = time.time()
    item["count"] = item.get("count", 0) + 1
    save_data()

    display = OWNER_DISPLAY_NAME if target_id == OWNER_ID else ANON_LABEL
    header = (
        f"📩 پیام ناشناس جدید برای {display}\n\n"
        f"👤 نام فرستنده: {sender.full_name}\n"
        f"🔗 یوزرنیم: {username_text(sender.username)}\n"
        f"🆔 شناسه: {sender.id}"
    )
    await context.bot.send_message(chat_id=target_id, text=header)

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✍️ پاسخ به این گفتگو", callback_data=f"prep_reply_{sender.id}")
    ]])
    copied = await context.bot.copy_message(
        chat_id=target_id,
        from_chat_id=message.chat_id,
        message_id=message.message_id,
        reply_markup=keyboard,
    )
    msg_to_sender_map[copied.message_id] = (sender.id, target_id)


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    # --- مالک ---
    if user.id == OWNER_ID:
        target = reply_target.get(OWNER_ID)
        if message.reply_to_message:
            mapped = msg_to_sender_map.get(message.reply_to_message.message_id)
            if mapped and mapped[1] == OWNER_ID:
                target = mapped[0]

        if target:
            try:
                await context.bot.send_message(chat_id=target, text=f"💬 پیام از {OWNER_DISPLAY_NAME}:")
                await context.bot.copy_message(
                    chat_id=target, from_chat_id=message.chat_id, message_id=message.message_id,
                )
                key = conv_key(target, target)
                # ثبت پاسخ مالک در همان گفتگو
                for k, item in anon_conversations.items():
                    if k.split(":")[1] == str(target):
                        item.setdefault("messages", []).append({
                            "from": "owner", "text": get_message_description(message), "ts": time.time(),
                        })
                        item["last_ts"] = time.time()
                        break
                save_data()
                reply_target.pop(OWNER_ID, None)
                await message.reply_text("✅ پاسخ ارسال شد.")
            except Exception:
                logger.exception("ارسال پاسخ ناموفق بود.")
                await message.reply_text("❌ ارسال نشد؛ شاید کاربر ربات را مسدود کرده باشد.")
            return

        # فایل صوتی مالک -> ساخت لینک موزیک
        if message.audio or message.voice or (
            message.document and (message.document.mime_type or "").startswith("audio/")
        ):
            await receive_music(message, context)
        return

    # --- پاسخ کاربر عادی ---
    if user.id in reply_target:
        target = reply_target.pop(user.id)
        try:
            await context.bot.send_message(chat_id=target, text=f"💬 پیام از {ANON_LABEL}:")
            await context.bot.copy_message(
                chat_id=target, from_chat_id=message.chat_id, message_id=message.message_id,
            )
            for k, item in anon_conversations.items():
                if k.split(":")[0] == str(target) and k.split(":")[1] == str(user.id):
                    item.setdefault("messages", []).append({
                        "from": "owner", "text": get_message_description(message), "ts": time.time(),
                    })
                    item["last_ts"] = time.time()
                    break
            save_data()
            await message.reply_text("✅ پاسخ شما ارسال شد.")
        except Exception:
            await message.reply_text("❌ ارسال نشد.")
        return

    # --- پیام ناشناس کاربر ---
    target_id = anon_mode.get(user.id)
    if not target_id:
        return  # کاربر عادی که حالت ناشناس ندارد: نادیده گرفته می‌شود

    try:
        await deliver_anon_message(context, user, message, target_id)
        await message.reply_text("✅ پیام شما ناشناس ارسال شد. می‌توانید پیام بعدی هم بفرستید.")
    except Exception:
        logger.exception("ارسال پیام ناشناس ناموفق بود.")
        await message.reply_text("❌ ارسال نشد؛ کمی بعد دوباره تلاش کنید.")


# -------------------------
# موزیک (فقط مالک)
# -------------------------
async def receive_music(message, context):
    if message.audio:
        file_id, file_type = message.audio.file_id, "audio"
        name = message.audio.title or message.audio.file_name or "فایل صوتی"
    elif message.voice:
        file_id, file_type = message.voice.file_id, "voice"
        name = "پیام صوتی"
    else:
        file_id, file_type = message.document.file_id, "document"
        name = message.document.file_name or "فایل صوتی"

    temp_id = uuid.uuid4().hex[:8]
    pending_uploads[temp_id] = {
        "file_id": file_id, "file_type": file_type, "name": name,
        "caption": message.caption,
        "owner_chat_id": message.chat_id, "owner_message_id": message.message_id,
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
        await query.message.reply_text("⚠️ اطلاعات فایل پیدا نشد؛ دوباره فایل را بفرستید.")
        return

    music_id = f"music_{uuid.uuid4().hex[:10]}"
    expires_at = time.time() + seconds if seconds else None
    shared_music[music_id] = {**info, "created_at": time.time(), "expires_at": expires_at}
    save_data()

    bot = await context.bot.get_me()
    link = f"https://t.me/{bot.username}?start={music_id}"
    await query.message.reply_text(
        f"✅ لینک ساخته شد\n🎵 نام: {info['name']}\n"
        f"⏳ اعتبار: {format_remaining(seconds or None)}\n🔗 {link}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑 حذف و ابطال لینک", callback_data=f"delete_music_{music_id}")
        ]]),
    )
    if seconds:
        context.job_queue.run_once(expire_music_job, seconds, data={"music_id": music_id})


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
                chat_id=item["owner_chat_id"], message_id=item["owner_message_id"],
            )
        except Exception:
            pass


async def delete_message_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    try:
        await context.bot.delete_message(chat_id=data["chat_id"], message_id=data["message_id"])
    except Exception:
        pass


async def delete_music(query, context):
    if query.from_user.id != OWNER_ID:
        return
    music_id = query.data.removeprefix("delete_music_")
    item = shared_music.pop(music_id, None)
    if not item:
        await query.edit_message_text("این لینک قبلاً حذف شده است.")
        return
    save_data()
    try:
        await context.bot.delete_message(
            chat_id=item["owner_chat_id"], message_id=item["owner_message_id"],
        )
    except Exception:
        pass
    await query.edit_message_text("🗑 موزیک حذف و لینک آن باطل شد.")


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
        await update.effective_message.reply_text(
            f"🎵 {item.get('name', 'موزیک')}\n⏳ باقی‌مانده: {remaining}\n🔗 {link}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑 حذف و ابطال", callback_data=f"delete_music_{music_id}")
            ]]),
        )


# -------------------------
# راه‌اندازی
# -------------------------
def main():
    load_data()
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("متغیر محیطی BOT_TOKEN تنظیم نشده است.")

    application = (
        ApplicationBuilder()
        .token(token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("my_link", lambda u, c: send_my_link(u.effective_message, c, u.effective_user.id)))
    application.add_handler(CommandHandler("anon_link", lambda u, c: send_my_link(u.effective_message, c, u.effective_user.id)))
    application.add_handler(CommandHandler("anon_inbox", lambda u, c: show_owner_inbox(u.effective_message) if u.effective_user.id == OWNER_ID else None))
    application.add_handler(CommandHandler("active_chats", lambda u, c: show_owner_active(u.effective_message) if u.effective_user.id == OWNER_ID else None))
    application.add_handler(CommandHandler("my_musics", my_musics))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_handler))

    logger.info("Starting bot polling.")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

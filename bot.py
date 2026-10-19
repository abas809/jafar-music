import os
import time
import uuid
from aiohttp import web
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BotCommand,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# شناسه انحصاری مالک ربات (عباس)
OWNER_ID = 6073046430

# دیکشنری آهنگ‌های فعال
shared_music = {}

# نگهداری موقت فایل تا زمان انتخاب ساعت توسط مالک
pending_uploads = {}

# کیبورد ثابت منوی پایین صفحه برای مالک
OWNER_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📋 لیست موزیک‌های فعال")],
        [KeyboardButton("ℹ️ راهنما و وضعیت ربات")],
    ],
    resize_keyboard=True,
    is_persistent=True,
)


async def health_check(request):
    """وب‌سرور برای فعال ماندن روی Render توسط UptimeRobot"""
    return web.Response(text="Bot is running!")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


def format_remaining_time(remaining_seconds):
    """تبدیل ثانیه به فرمت خوانا"""
    if remaining_seconds is None:
        return "همیشگی (بدون انقضا)"
    if remaining_seconds <= 0:
        return "منقضی شده"
    hours = int(remaining_seconds // 3600)
    minutes = int((remaining_seconds % 3600) // 60)
    if hours > 24:
        days = hours // 24
        return f"{days} روز و {hours % 24} ساعت"
    return f"{hours} ساعت و {minutes} دقیقه"


async def delete_expired_message(context: ContextTypes.DEFAULT_TYPE):
    """حذف پیام پس از پایان مدت انقضا"""
    data = context.job.data
    chat_id = data.get("chat_id")
    message_id = data.get("message_id")
    music_id = data.get("music_id")

    if music_id and music_id in shared_music:
        del shared_music[music_id]

    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def post_init(application):
    """تنظیم خودکار منوی دستورات تلگرام (دکمه آبی Menu)"""
    commands = [
        BotCommand("start", "شروع مجدد ربات"),
        BotCommand("my_musics", "مشاهده لیست موزیک‌های فعال"),
    ]
    await application.bot.set_my_commands(commands)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args

    # اگر کاربر با لینک موزیک وارد شده باشد
    if args and len(args) > 0 and args[0].startswith("music_"):
        music_id = args[0]
        if music_id in shared_music:
            data = shared_music[music_id]
            caption = data.get("caption") or f"🎵 {data.get('name', 'موزیک ارسالی')}"

            if data["file_type"] == "audio":
                sent = await context.bot.send_audio(chat_id=user.id, audio=data["file_id"], caption=caption)
            elif data["file_type"] == "voice":
                sent = await context.bot.send_voice(chat_id=user.id, voice=data["file_id"], caption=caption)
            else:
                sent = await context.bot.send_document(chat_id=user.id, document=data["file_id"], caption=caption)

            duration = data.get("duration_sec")
            if duration:
                elapsed = time.time() - data["created_at"]
                remaining = max(1, duration - elapsed)
                context.job_queue.run_once(
                    delete_expired_message,
                    remaining,
                    data={"chat_id": user.id, "message_id": sent.message_id},
                )
        else:
            await update.message.reply_text("❌ متأسفانه این لینک منقضی یا باطل شده است.")
        return

    # استارت معمولی
    if user.id == OWNER_ID:
        await update.message.reply_text(
            "سلام عباس عزیز! خوش آمدی 👋\n\n"
            "منوی مدیریت زیر دستت آماده است. برای شروع، هر موزیکی دوست داری برام بفرست!",
            reply_markup=OWNER_KEYBOARD,
        )
    else:
        await update.message.reply_text("سلام! برای دریافت فایل باید از لینکی که فرستنده داده استفاده کنید.")


async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """راهنمای سریع ربات برای مالک"""
    if update.effective_user.id != OWNER_ID:
        return
    text = (
        "ℹ️ **راهنمای ربات جعفَر موزیک:**\n\n"
        "1️⃣ **ارسال آهنگ:** هر موزیک یا وویسی بفرستی، زمان اعتبار ازت پرسیده میشه.\n"
        "2️⃣ **لینک اختصاصی:** لینکی که ربات میده رو برای هر کی بفرستی، موزیک براش باز میشه.\n"
        "3️⃣ **حذف آنی:** زیر هر لینک و داخل منوی «لیست موزیک‌ها» دکمه حذف فوری داری.\n"
        "4️⃣ **امنیت:** ربات فقط با آیدی شما کار میکنه و برای بقیه قفله."
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=OWNER_KEYBOARD)


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != OWNER_ID:
        await update.message.reply_text("⛔️ شما اجازه آپلود فایل ندارید. این ربات شخصی است.")
        return

    msg = update.message
    file_id = None
    file_type = None
    file_name = "موزیک نامشخص"

    if msg.audio:
        file_id = msg.audio.file_id
        file_type = "audio"
        file_name = msg.audio.title or msg.audio.file_name or "فایل صوتی"
    elif msg.voice:
        file_id = msg.voice.file_id
        file_type = "voice"
        file_name = "پیام صوتی (Voice)"
    elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith("audio/"):
        file_id = msg.document.file_id
        file_type = "document"
        file_name = msg.document.file_name or "فایل صوتی"

    if not file_id:
        await msg.reply_text("فرمت فایل معتبر نیست.")
        return

    temp_id = uuid.uuid4().hex[:6]
    pending_uploads[temp_id] = {
        "file_id": file_id,
        "file_type": file_type,
        "name": file_name,
        "caption": msg.caption,
        "message_id": msg.message_id,
        "chat_id": msg.chat_id,
    }

    # دکمه‌های شیشه‌ای انتخاب زمان اعتبار فایل
    keyboard = [
        [
            InlineKeyboardButton("⚡️ ۱ ساعت", callback_data=f"exp_{temp_id}_3600"),
            InlineKeyboardButton("⏳ ۶ ساعت", callback_data=f"exp_{temp_id}_21600"),
        ],
        [
            InlineKeyboardButton("🌙 ۱۲ ساعت", callback_data=f"exp_{temp_id}_43200"),
            InlineKeyboardButton("☀️ ۲۴ ساعت", callback_data=f"exp_{temp_id}_86400"),
        ],
        [
            InlineKeyboardButton("🗓 ۳ روز", callback_data=f"exp_{temp_id}_259200"),
            InlineKeyboardButton("♾ بدون انقضا (دائمی)", callback_data=f"exp_{temp_id}_0"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await msg.reply_text(
        f"🎧 فایل دریافت شد: **{file_name}**\n\n"
        f"⏱ چقدر برای بقیه معتبر بماند؟",
        reply_markup=reply_markup,
        parse_mode="Markdown",
    )


async def duration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تنظیم انقضا بر اساس دکمه انتخابی"""
    query = update.callback_query
    await query.answer()

    if query.from_user.id != OWNER_ID:
        return

    data = query.data
    _, temp_id, sec_str = data.split("_")
    duration_sec = int(sec_str) if sec_str != "0" else None

    if temp_id not in pending_uploads:
        await query.edit_message_text("⚠️ این فایل قبلاً ثبت شده یا زمان انتخاب تمام شده است.")
        return

    info = pending_uploads.pop(temp_id)
    music_id = f"music_{uuid.uuid4().hex[:8]}"
    bot_info = await context.bot.get_me()
    share_link = f"https://t.me/{bot_info.username}?start={music_id}"

    shared_music[music_id] = {
        "file_id": info["file_id"],
        "file_type": info["file_type"],
        "name": info["name"],
        "caption": info["caption"],
        "created_at": time.time(),
        "duration_sec": duration_sec,
        "owner_chat_id": info["chat_id"],
        "owner_msg_id": info["message_id"],
        "reply_msg_id": query.message.message_id,
    }

    keyboard = [
        [InlineKeyboardButton("🗑 حذف فوری و ابطال لینک", callback_data=f"del_{music_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    duration_text = format_remaining_time(duration_sec)
    await query.edit_message_text(
        f"✅ **لینک با موفقیت ساخته شد!**\n\n"
        f"🎵 نام فایل: `{info['name']}`\n"
        f"⏱ مدت اعتبار: **{duration_text}**\n\n"
        f"🔗 **لینک اشتراک:**\n`{share_link}`",
        reply_markup=reply_markup,
        parse_mode="Markdown",
    )

    if duration_sec:
        context.job_queue.run_once(
            delete_expired_message,
            duration_sec,
            data={"chat_id": info["chat_id"], "message_id": info["message_id"], "music_id": music_id},
        )
        context.job_queue.run_once(
            delete_expired_message,
            duration_sec,
            data={"chat_id": info["chat_id"], "message_id": query.message.message_id},
        )


async def list_my_musics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش لیست فایل‌های فعال"""
    user = update.effective_user
    if user.id != OWNER_ID:
        return

    if not shared_music:
        await update.message.reply_text(
            "📭 در حال حاضر هیچ موزیک فعالی نداری!",
            reply_markup=OWNER_KEYBOARD
        )
        return

    bot_info = await context.bot.get_me()
    await update.message.reply_text("📋 **لیست موزیک‌های فعال شما:**", parse_mode="Markdown")

    for m_id, item in list(shared_music.items()):
        if item["duration_sec"]:
            elapsed = time.time() - item["created_at"]
            rem = item["duration_sec"] - elapsed
            rem_text = format_remaining_time(rem)
        else:
            rem_text = "♾ همیشگی (دائمی)"

        link = f"https://t.me/{bot_info.username}?start={m_id}"
        keyboard = [
            [InlineKeyboardButton("🗑 حذف این موزیک", callback_data=f"del_{m_id}")]
        ]
        text = (
            f"🎵 **نام:** `{item['name']}`\n"
            f"⏳ **زمان مانده:** {rem_text}\n"
            f"🔗 `{link}`"
        )
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )


async def delete_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف دستی با دکمه شیشه‌ای"""
    query = update.callback_query
    await query.answer()

    if query.from_user.id != OWNER_ID:
        await query.answer("شما دسترسی ندارید!", show_alert=True)
        return

    callback_data = query.data
    if callback_data.startswith("del_"):
        music_id = callback_data.replace("del_", "")

        if music_id in shared_music:
            data = shared_music.pop(music_id)
            try:
                await context.bot.delete_message(chat_id=data["owner_chat_id"], message_id=data["owner_msg_id"])
            except Exception:
                pass
            try:
                await context.bot.delete_message(chat_id=data["owner_chat_id"], message_id=data["reply_msg_id"])
            except Exception:
                pass

            await query.edit_message_text(f"🗑 فایل `{data.get('name', '')}` با موفقیت حذف و لینکش باطل شد.")
        else:
            await query.edit_message_text("این فایل قبلاً باطل شده است.")


def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN تنظیم نشده است!")

    application = (
        ApplicationBuilder()
        .token(token)
        .post_init(post_init)
        .build()
    )

    # سرور داخلی UptimeRobot
    application.job_queue.run_once(lambda ctx: start_web_server(), 0)

    # دستورات و پیام‌های متنی منو
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("my_musics", list_my_musics))
    application.add_handler(MessageHandler(filters.Regex("^📋 لیست موزیک‌های فعال$"), list_my_musics))
    application.add_handler(MessageHandler(filters.Regex("^ℹ️ راهنما و وضعیت ربات$"), show_help))

    # هندلرهای صوت و دکمه‌ها
    application.add_handler(
        MessageHandler(
            filters.AUDIO | filters.VOICE | filters.Document.Category("audio/"),
            handle_audio,
        )
    )
    application.add_handler(CallbackQueryHandler(duration_callback, pattern=r"^exp_"))
    application.add_handler(CallbackQueryHandler(delete_button_callback, pattern=r"^del_"))

    print("ربات فعال شد...")
    application.run_polling()


if __name__ == "__main__":
    main()

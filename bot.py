import os
import logging
import asyncio
import uuid
from aiohttp import web
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
EXPIRATION_SECONDS = 24 * 60 * 60

# حافظه موقت برای ذخیره اطلاعات فایل‌ها (در دیکشنری رم)
shared_music = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    # اگر کاربر با لینک اختصاصی وارد شد (مثلاً start=music_123)
    if args and args[0].startswith("music_"):
        music_id = args[0]
        if music_id in shared_music:
            data = shared_music[music_id]
            # ارسال فایل به دوستتان
            sent_msg = await context.bot.copy_message(
                chat_id=update.effective_chat.id,
                from_chat_id=data["owner_chat_id"],
                message_id=data["audio_msg_id"]
            )
            reply_msg = await update.message.reply_text(
                "🎵 این موزیک از طرف دوستتان برای شما ارسال شد و تا ۲۴ ساعت آینده به صورت خودکار حذف خواهد شد."
            )
            
            # زمان‌بندی برای حذف پیام از چت دوستتان
            job_data = {
                "chat_id": update.effective_chat.id,
                "audio_msg_id": sent_msg.message_id,
                "reply_msg_id": reply_msg.message_id
            }
            context.job_queue.run_once(
                delete_expired_audio,
                when=EXPIRATION_SECONDS,
                data=job_data,
                name=f"del_friend_{music_id}"
            )
            return
        else:
            await update.message.reply_text("❌ متأسفانه این لینک منقضی شده یا آهنگ پاک شده است.")
            return

    await update.message.reply_text(
        "سلام! 🎵\nهر موزیک یا فایلی بفرستی، هم ذخیره می‌شود و هم یک **لینک اشتراک‌گذاری اختصاصی** برای فرستادن به دوستانت دریافت می‌کنی که بعد از ۲۴ ساعت پاک می‌شود."
    )


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    chat_id = message.chat_id
    audio_msg_id = message.message_id

    # ساخت یک شناسهتاک برای موزیک
    music_id = f"music_{uuid.uuid4().hex[:8]}"
    
    # ذخیره مشخصات در حافظه موقت ربات
    shared_music[music_id] = {
        "owner_chat_id": chat_id,
        "audio_msg_id": audio_msg_id
    }

    bot_username = context.bot.username
    share_link = f"https://t.me/{bot_username}?start={music_id}"

    reply_msg = await message.reply_text(
        f"✅ موزیک با موفقیت ثبت شد!\n\n"
        f"🔗 **لینک اشتراک‌گذاری برای دوستتان:**\n`{share_link}`\n\n"
        f"این آهنگ و لینک آن تا ۲۴ ساعت آینده فعال هستند.",
        parse_mode="Markdown"
    )

    job_data = {
        "chat_id": chat_id,
        "audio_msg_id": audio_msg_id,
        "reply_msg_id": reply_msg.message_id,
        "music_id": music_id
    }

    # زمان‌بندی برای حذف کامل موزیک اصلی پس از ۲۴ ساعت
    context.job_queue.run_once(
        delete_expired_audio_and_memory,
        when=EXPIRATION_SECONDS,
        data=job_data,
        name=f"del_owner_{music_id}"
    )


async def delete_expired_audio(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    data = job.data
    try:
        await context.bot.delete_message(chat_id=data["chat_id"], message_id=data["audio_msg_id"])
        await context.bot.delete_message(chat_id=data["chat_id"], message_id=data["reply_msg_id"])
        logger.info("Friend message deleted successfully.")
    except Exception as e:
        logger.error(f"Error deleting friend message: {e}")


async def delete_expired_audio_and_memory(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    data = job.data
    music_id = data["music_id"]
    
    # حذف از حافظه موقت
    if music_id in shared_music:
        del shared_music[music_id]

    try:
        await context.bot.delete_message(chat_id=data["chat_id"], message_id=data["audio_msg_id"])
        await context.bot.delete_message(chat_id=data["chat_id"], message_id=data["reply_msg_id"])
        logger.info("Owner audio and message deleted successfully.")
    except Exception as e:
        logger.error(f"Error deleting owner audio: {e}")


async def health_check(request):
    return web.Response(text="Bot is running!")


async def run_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


async def main_async():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is not set!")

    await run_web_server()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    audio_filter = filters.AUDIO | filters.VOICE | (filters.Document.Category("audio/"))
    app.add_handler(MessageHandler(audio_filter, handle_audio))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    while True:
        await asyncio.sleep(3600)


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

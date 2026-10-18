import os
import logging
import asyncio
from aiohttp import web
from telegram import Update
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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام! 🎵\nهر موزیک یا فایلی بفرستی دریافت می‌شه و دقیقاً ۲۴ ساعت بعد به صورت خودکار حذف خواهد شد."
    )


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    chat_id = message.chat_id
    audio_msg_id = message.message_id

    reply_msg = await message.reply_text(
        "✅ موزیک دریافت شد. این فایل تا ۲۴ ساعت آینده به صورت خودکار حذف می‌شود."
    )

    job_data = {
        "chat_id": chat_id,
        "audio_msg_id": audio_msg_id,
        "reply_msg_id": reply_msg.message_id
    }

    context.job_queue.run_once(
        delete_expired_audio,
        when=EXPIRATION_SECONDS,
        data=job_data,
        name=f"del_{chat_id}_{audio_msg_id}"
    )


async def delete_expired_audio(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    data = job.data
    try:
        await context.bot.delete_message(chat_id=data["chat_id"], message_id=data["audio_msg_id"])
        await context.bot.delete_message(chat_id=data["chat_id"], message_id=data["reply_msg_id"])
        logger.info("Message deleted successfully.")
    except Exception as e:
        logger.error(f"Error deleting: {e}")


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

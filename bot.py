import os
import logging
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

# توکن به صورت امن از محیط خوانده می‌شود
BOT_TOKEN = os.getenv("BOT_TOKEN")

# مدت زمان ماندگاری: ۲۴ ساعت (۸۶۴۰۰ ثانیه)
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


def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is not set!")
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    audio_filter = filters.AUDIO | filters.VOICE | (filters.Document.Category("audio/"))
    app.add_handler(MessageHandler(audio_filter, handle_audio))
    app.run_polling()


if __name__ == "__main__":
    main()

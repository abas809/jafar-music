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

    # سرور سبک وب برای زنده نگه داشتن در رندر
    application.job_queue.run_once(lambda ctx: start_web_server(), 0)

    # ۱. هندلرهای کلیک روی دکمه‌های شیشه‌ای (باید اول ثبت شوند)
    application.add_handler(CallbackQueryHandler(duration_callback, pattern=r"^exp_"))
    application.add_handler(CallbackQueryHandler(delete_button_callback, pattern=r"^del_"))
    application.add_handler(CallbackQueryHandler(handle_anon_button_clicks))

    # ۲. هندلرهای دستورات
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("anon_inbox", anon_inbox_command))
    application.add_handler(CommandHandler("anon_link", send_anon_link))
    application.add_handler(CommandHandler("my_link", send_anon_link))
    application.add_handler(CommandHandler("ads", ads_command))
    application.add_handler(CommandHandler("my_musics", list_my_musics))
    application.add_handler(CommandHandler("help", show_help))

    # ۳. هندلر فایل‌های صوتی مالک
    application.add_handler(
        MessageHandler(
            (filters.AUDIO | filters.VOICE | filters.Document.Category("audio/")) & filters.User(user_id=OWNER_ID),
            handle_audio,
        )
    )

    # ۴. هندلر پیام‌های متنی عادی و ناشناس (باید آخرین مورد باشد)
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_all_messages,
        )
    )

    print("ربات با تمامی امکانات فعال شد...")
    application.run_polling()

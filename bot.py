# bot.py
# jafar-music — Anonymous Message & Music Bot
# Owner: Abbas (OWNER_ID=6073046430)

import os
import time
import uuid
import asyncio
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

# =========================
# تنظیمات مالک
# =========================
OWNER_ID = 6073046430
OWNER_DISPLAY_NAME = "Ả̸̱̻͕̜͔̗̹͗̐̏̄͜͝͝M̶͋̅͠"

# =========================
# حافظه‌ی موقت (In-Memory)
# =========================
shared_music = {}               # {music_id: {...}}
pending_uploads = {}            # {temp_id: {...}}

users_in_anon_mode = set()      # کاربرانی که وارد مود ارسال ناشناس به مالک شده‌اند
anon_conversations = {}         # {user_id: {"name":..., "username":..., "messages":[...]}}
admin_reply_target = {}         # {OWNER_ID: target_user_id}
msg_to_sender_map = {}          # {forwarded_message_id: sender_user_id}

# گفتگوهای فعال (در حال تبادل)
active_dialogs = {}             # {user_id: {"name":..., "username":..., "last_ts":..., "count": int}}

# =========================
# وب سرور برای Render (حل مشکل کرش)
# =========================
async def health_check(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


# =========================
# ابزارها
# =========================
def format_remaining_time(remaining_seconds):
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


def _username_str(user):
    return f"@{user.username}" if user.username else "ندارد"


def _track_active_dialog(user_id: int, name: str, username: str):
    now = time.time()
    if user_id not in active_dialogs:
        active_dialogs[user_id] = {"name": name, "username": username, "last_ts": now, "count": 1}
    else:
        active_dialogs[user_id]["last_ts"] = now
        active_dialogs[user_id]["count"] += 1


def _is_dialog_active(last_ts: float, window_sec: int = 3600) -> bool:
    return (time.time() - last_ts) <= window_sec


# =========================
# منوها و تنظیمات شروع
# =========================
async def post_init(application):
    # راه اندازی وب سرور Render
    asyncio.create_task(start_web_server())

    # منوی ادمین (عباس)
    owner_commands = [
        BotCommand("anon_inbox", "🎭 پیام ناشناس (صندوق پیام‌ها)"),
        BotCommand("anon_link", "🔗 دریافت لینک ناشناس من"),
        BotCommand("active_chats", "🟢 گفتگوهای فعال"),
        BotCommand("my_musics", "📋 موزیک‌های من"),
        BotCommand("help", "ℹ️ راهنما"),
        BotCommand("start", "🔄 شروع مجدد"),
    ]
    try:
        await application.bot.set_my_commands(
            owner_commands, scope=BotCommandScopeChat(chat_id=OWNER_ID)
        )
        await application.bot.set_chat_menu_button(chat_id=OWNER_ID, menu_button=MenuButtonCommands())
    except Exception as e:
        print(f"خطا در منوی ادمین: {e}")

    # منوی سایر کاربران
    default_commands = [
        BotCommand("start", "شروع / منو"),
        BotCommand("my_link", "🔗 دریافت لینک ناشناس من"),
        BotCommand("help", "راهنما"),
    ]
    try:
        await application.bot.set_my_commands(default_commands)
    except Exception as e:
        print(f"خطا در منوی عمومی: {e}")


def public_start_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎭 ارسال پیام ناشناس به عباس", callback_data="go_anon_owner"),
        ],
        [
            InlineKeyboardButton("🔗 دریافت لینک ناشناس من", callback_data="btn_get_link"),
            InlineKeyboardButton("📩 پیام‌های من", callback_data="btn_my_messages"),
        ],
    ])


# =========================
# دستورات اصلی
# =========================
async def send_anon_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    bot_info = await context.bot.get_me()

    if user.id == OWNER_ID:
        anon_url = f"https://t.me/{bot_info.username}?start=anon_chat"
        text = (
            f"🎭 **لینک اختصاصی پیام ناشناس شما ({OWNER_DISPLAY_NAME}):**\n\n"
            f"`{anon_url}`\n\n"
            "💡 این لینک را به دیگران بدهید تا به شما پیام ناشناس بفرستند."
        )
    else:
        anon_url = f"https://t.me/{bot_info.username}?start=anon_user_{user.id}"
        text = (
            "🎭 **لینک اختصاصی پیام ناشناس شما ساخته شد:**\n\n"
            f"`{anon_url}`\n\n"
            "🔗 این لینک را به دوستانتان بدهید تا بدون نمایش مشخصات به شما پیام دهند!"
        )
    await update.message.reply_text(text, parse_mode="Markdown")


async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if user.id == OWNER_ID:
        text = (
            "ℹ️ **راهنمای ادمین:**\n\n"
            "• `/anon_inbox` صندوق پیام‌های ناشناس را نشان می‌دهد.\n"
            "• `/active_chats` فهرست گفتگوهای فعال را نشان می‌دهد.\n"
            "• می‌توانید به پیام‌های دریافتی **Reply** دهید یا از دکمه «✍️ ارسال پاسخ» استفاده کنید.\n\n"
            "🎵 **موزیک تایم‌دار:** فایل صوتی را بفرستید و مدت را انتخاب کنید."
        )
    else:
        text = (
            "ℹ️ **راهنمای کاربران:**\n\n"
            "• برای ارسال پیام ناشناس به عباس، از دکمه «ارسال پیام ناشناس به عباس» استفاده کنید.\n"
            "• با «دریافت لینک ناشناس من» لینک اختصاصی خودتان ساخته می‌شود.\n"
            "• از «پیام‌های من» آخرین پیام‌های ارسالی‌تان نمایش داده می‌شود."
        )

    await update.message.reply_text(text, parse_mode="Markdown")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args

    if user.id == OWNER_ID and OWNER_ID in admin_reply_target:
        admin_reply_target.pop(OWNER_ID, None)

    users_in_anon_mode.discard(user.id)

    # ۱) استارت با لینک ناشناس مالک
    if args and args[0] == "anon_chat":
        if user.id == OWNER_ID:
            await update.message.reply_text("این لینک ناشناس خود شماست! آن را با دیگران به اشتراک بگذارید.")
            return

        users_in_anon_mode.add(user.id)

        welcome_anon = (
            f"شما در حال ارسال پیام ناشناس به {OWNER_DISPLAY_NAME} هستید! 🎭\n\n"
            "پیام شما (متن، عکس، ویس، موزیک یا ویدیو) به صورت ناشناس و بدون نمایش هویت برای ایشان ارسال خواهد شد.\n\n"
            "پیام خود را همینجا ارسال کنید.\n"
            "جهت لغو ارسال پیام روی /start بزنید."
        )
        await update.message.reply_text(welcome_anon, reply_markup=public_start_keyboard())

        alert_text = (
            "🔔 **ورود مخاطب به لینک پیام ناشناس!**\n\n"
            f"👤 **نام:** {user.full_name}\n"
            f"🔗 **یوزرنیم:** {_username_str(user)}\n"
            f"🆔 **آیدی:** `{user.id}`"
        )
        try:
            await context.bot.send_message(chat_id=OWNER_ID, text=alert_text, parse_mode="Markdown")
        except Exception:
            pass
        return

    # ۲) استارت با لینک موزیک
    if args and args[0].startswith("music_"):
        music_id = args[0]
        if music_id in shared_music:
            data = shared_music[music_id]
            caption = data.get("caption") or f"🎵 {data.get('name', 'موزیک')}"

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
            await update.message.reply_text("❌ این لینک منقضی یا نامعتبر است.")
        return

    # ۳) استارت مالک
    if user.id == OWNER_ID:
        try:
            await context.bot.set_chat_menu_button(chat_id=user.id, menu_button=MenuButtonCommands())
        except Exception:
            pass

        await update.message.reply_text(
            f"سلام عباس عزیز ({OWNER_DISPLAY_NAME}) خوش آمدید!\n\n"
            "از منوی پایین می‌توانید وارد صندوق ناشناس شوید، لینک ناشناس بگیرید، گفتگوهای فعال را ببینید و موزیک‌ها را مدیریت کنید.",
            reply_markup=public_start_keyboard()
        )
        return

    # ۴) استارت کاربران عادی (نمایش کامل امکانات ناشناس)
    await update.message.reply_text(
        "سلام! به ربات پیام ناشناس و موزیک خوش آمدید.\n\n"
        "از دکمه‌های زیر می‌توانید استفاده کنید:",
        reply_markup=public_start_keyboard(),
    )


# =========================
# بخش ادمین: صندوق ناشناس و گفتگوهای فعال
# =========================
async def anon_inbox_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    if not anon_conversations:
        await update.message.reply_text("📭 در حال حاضر هیچ پیامی در صندوق پیام‌های ناشناس وجود ندارد.")
        return

    keyboard = []
    for uid, data in anon_conversations.items():
        uname = f"@{data['username']}" if data.get("username") else f"ID: {uid}"
        btn_text = f"👤 {data.get('name', 'کاربر')} ({uname}) - {len(data['messages'])} پیام"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"view_anon_{uid}")])

    await update.message.reply_text(
        "🎭 **صندوق پیام‌های ناشناس دریافتی:**",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def active_chats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    active = []
    for uid, info in active_dialogs.items():
        if _is_dialog_active(info.get("last_ts", 0), window_sec=3600):
            active.append((uid, info))

    if not active:
        await update.message.reply_text("🟢 در حال حاضر گفتگوی فعالی ندارید (در ۱ ساعت اخیر).")
        return

    active.sort(key=lambda x: x[1].get("last_ts", 0), reverse=True)

    keyboard = []
    for uid, info in active:
        uname = f"@{info['username']}" if info.get("username") else f"ID: {uid}"
        cnt = info.get("count", 0)
        keyboard.append([InlineKeyboardButton(f"🟢 {info.get('name','کاربر')} ({uname}) — {cnt} پیام", callback_data=f"view_anon_{uid}")])

    await update.message.reply_text(
        "🟢 **گفتگوهای فعال و در حال تبادل (۱ ساعت اخیر):**\n\n"
        "روی هر نفر بزنید تا تمام پیام‌ها و جزئیات تبادل را ببینید و پاسخ دهید:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


# =========================
# پردازش دکمه‌ها
# =========================
async def handle_anon_button_clicks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    if data == "go_anon_owner":
        if user.id == OWNER_ID:
            await query.message.reply_text("این بخش برای دیگران است؛ لینک ناشناس خودت را با /anon_link بگیر.")
            return
        users_in_anon_mode.add(user.id)
        await query.message.reply_text(
            f"شما در حال ارسال پیام ناشناس به {OWNER_DISPLAY_NAME} هستید.\n"
            "پیام خود را ارسال کنید. برای لغو /start را بزنید."
        )
        return

    if data == "btn_get_link":
        bot_info = await context.bot.get_me()
        if user.id == OWNER_ID:
            link = f"https://t.me/{bot_info.username}?start=anon_chat"
        else:
            link = f"https://t.me/{bot_info.username}?start=anon_user_{user.id}"
        await query.message.reply_text(
            f"🔗 **لینک اختصاصی شما:**\n`{link}`",
            parse_mode="Markdown",
        )
        return

    if data == "btn_my_messages":
        if user.id in anon_conversations and anon_conversations[user.id]["messages"]:
            history = "\n".join([f"• {m}" for m in anon_conversations[user.id]["messages"][-5:]])
            await query.message.reply_text(f"📩 **آخرین پیام‌های ارسالی شما:**\n\n{history}", parse_mode="Markdown")
        else:
            await query.message.reply_text("شما هنوز پیامی ارسال نکرده‌اید.")
        return

    if user.id != OWNER_ID:
        return

    if data.startswith("view_anon_"):
        target_uid = int(data.replace("view_anon_", ""))
        user_data = anon_conversations.get(target_uid)

        if not user_data:
            await query.edit_message_text("❌ تاریخچه این کاربر پیدا نشد.")
            return

        uname = f"@{user_data['username']}" if user_data.get("username") else "ندارد"
        msg_list = "\n".join([f"▫️ {m}" for m in user_data["messages"][-15:]]) or "—"

        keyboard = [
            [InlineKeyboardButton("✍️ پاسخ به این کاربر", callback_data=f"prep_reply_{target_uid}")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="back_to_inbox")],
        ]

        text = (
            f"👤 **مشخصات فرستنده:**\n"
            f"نام: {user_data.get('name')}\n"
            f"یوزرنیم: {uname}\n"
            f"شناسه: `{target_uid}`\n"
            "─────────────────\n"
            f"📜 **پیام‌ها (۱۵ پیام آخر):**\n{msg_list}"
        )
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    if data.startswith("prep_reply_"):
        target_uid = int(data.replace("prep_reply_", ""))
        admin_reply_target[OWNER_ID] = target_uid
        target_name = anon_conversations.get(target_uid, {}).get("name", "کاربر")
        await query.message.reply_text(
            f"✍️ در حال نوشتن پاسخ به **{target_name}** هستید.\n"
            "پیام خود را بفرستید تا با اسم شما برای او ارسال شود.\n"
            "(لغو: /start)",
            parse_mode="Markdown",
        )
        return

    if data == "back_to_inbox":
        if not anon_conversations:
            await query.edit_message_text("📭 صندوق پیام‌ها خالی است.")
            return

        keyboard = []
        for uid, c_data in anon_conversations.items():
            uname = f"@{c_data['username']}" if c_data.get("username") else f"ID: {uid}"
            btn_text = f"👤 {c_data.get('name', 'کاربر')} ({uname}) - {len(c_data['messages'])} پیام"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"view_anon_{uid}")])

        await query.edit_message_text(
            "🎭 **صندوق پیام‌های ناشناس دریافتی:**",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )


# =========================
# تبادل پیام‌ها
# =========================
async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message
    if not msg:
        return

    # پاسخ ادمین
    if user.id == OWNER_ID:
        target_uid = None

        if msg.reply_to_message and msg.reply_to_message.message_id in msg_to_sender_map:
            target_uid = msg_to_sender_map[msg.reply_to_message.message_id]
        elif OWNER_ID in admin_reply_target:
            target_uid = admin_reply_target[OWNER_ID]

        if target_uid:
            try:
                await context.bot.send_message(
                    chat_id=target_uid,
                    text=f"💬 **پیام جدید از طرف {OWNER_DISPLAY_NAME}:**",
                    parse_mode="Markdown",
                )
                await context.bot.copy_message(
                    chat_id=target_uid,
                    from_chat_id=msg.chat_id,
                    message_id=msg.message_id,
                )
                await msg.reply_text("✅ پاسخ شما ارسال شد.")
                admin_reply_target.pop(OWNER_ID, None)

                info = anon_conversations.get(target_uid, {})
                _track_active_dialog(
                    target_uid,
                    info.get("name", "کاربر"),
                    info.get("username", None),
                )
            except Exception as e:
                await msg.reply_text(f"❌ ارسال نشد: {e}")
            return
        return

    # پیام کاربران به مالک
    if user.id in users_in_anon_mode:
        if user.id not in anon_conversations:
            anon_conversations[user.id] = {"name": user.full_name, "username": user.username, "messages": []}

        content_desc = msg.text or "[فایل یا چندرسانه‌ای]"
        anon_conversations[user.id]["messages"].append(content_desc)

        _track_active_dialog(user.id, user.full_name, user.username)

        header = (
            "📩 **پیام ناشناس جدید دریافت شد!**\n\n"
            f"👤 **فرستنده:** {user.full_name}\n"
            f"🔗 **یوزرنیم:** {_username_str(user)}\n"
            f"🆔 **آیدی:** `{user.id}`\n"
            "💡 _(برای پاسخ مستقیم، می‌توانید همین پیام را Reply کنید)_"
        )
        fwd_alert = await context.bot.send_message(chat_id=OWNER_ID, text=header, parse_mode="Markdown")

        reply_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✍️ ارسال پاسخ", callback_data=f"prep_reply_{user.id}")]
        ])
        copied_msg = await context.bot.copy_message(
            chat_id=OWNER_ID,
            from_chat_id=msg.chat_id,
            message_id=msg.message_id,
            reply_markup=reply_kb,
        )

        msg_to_sender_map[fwd_alert.message_id] = user.id
        msg_to_sender_map[copied_msg.message_id] = user.id

        await msg.reply_text("✅ پیام شما به صورت ناشناس ارسال شد!", reply_markup=public_start_keyboard())
        return


# =========================
# مدیریت موزیک (مالک)
# =========================
async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != OWNER_ID:
        return

    msg = update.message
    file_id = None
    file_type = None
    file_name = "موزیک"

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
    await msg.reply_text(
        f"🎧 فایل دریافت شد: **{file_name}**\n\n⏱ مدت اعتبار لینک چقدر باشد؟",
        reply_markup=InlineKeyboardMarkup(keyboard),
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

    keyboard = [[InlineKeyboardButton("🗑 حذف فوری و ابطال لینک", callback_data=f"del_{music_id}")]]
    duration_text = format_remaining_time(duration_sec)

    await query.edit_message_text(
        f"✅ **لینک موزیک ساخته شد!**\n\n"
        f"🎵 نام: `{info['name']}`\n"
        f"⏱ مدت اعتبار: **{duration_text}**\n\n"
        f"🔗 **لینک اشتراک:**\n`{share_link}`",
        reply_markup=InlineKeyboardMarkup(keyboard),
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
        keyboard = [[InlineKeyboardButton("🗑 حذف این موزیک", callback_data=f"del_{m_id}")]]
        text = (
            f"🎵 **نام:** `{item['name']}`\n"
            f"⏳ **تایم باقی‌مانده:** `{rem_text}`\n\n"
            f"🔗 **لینک:**\n`{link}`"
        )
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def delete_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != OWNER_ID:
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
            await query.edit_message_text(f"🗑 فایل `{data.get('name', '')}` حذف شد و لینک باطل گردید.")
        else:
            await query.edit_message_text("این فایل قبلاً باطل شده است.")


# =========================
# اجرای برنامه
# =========================
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

    # 1) Callback ها
    application.add_handler(CallbackQueryHandler(duration_callback, pattern=r"^exp_"))
    application.add_handler(CallbackQueryHandler(delete_button_callback, pattern=r"^del_"))
    application.add_handler(CallbackQueryHandler(handle_anon_button_clicks))

    # 2) Command ها
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("anon_inbox", anon_inbox_command))
    application.add_handler(CommandHandler("anon_link", send_anon_link))
    application.add_handler(CommandHandler("my_link", send_anon_link))
    application.add_handler(CommandHandler("active_chats", active_chats_command))
    application.add_handler(CommandHandler("my_musics", list_my_musics))
    application.add_handler(CommandHandler("help", show_help))

    # 3) فایل صوتی مالک
    application.add_handler(
        MessageHandler(
            (filters.AUDIO | filters.VOICE | filters.Document.Category("audio/")) & filters.User(user_id=OWNER_ID),
            handle_audio,
        )
    )

    # 4) متن ساده
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_all_messages))

    print("ربات آنلاین شد...")
    application.run_polling()


if __name__ == "__main__":
    main()

from __future__ import annotations

from typing import Literal

Language = Literal["uk", "en"]

TEXTS: dict[Language, dict[str, str]] = {
    "uk": {
        "welcome": (
            "✅ Мову встановлено! 🇺🇦\n\n"
            "Привіт, <b>{name}</b>! 👋\n\n"
            "💬 Я — ШІ-помічник Артема. Натисніть кнопку або просто напишіть запитання."
        ),
        "lang_changed": "✅ Мову змінено на українську 🇺🇦",
        "help": (
            "ℹ️ <b>Можливості бота:</b>\n\n"
            "• 💬 Напишіть будь-яке запитання — ШІ відповість одразу.\n"
            "• 📝 Залишити заявку: /lead або кнопка нижче.\n"
            "• 🌐 Змінити мову: /language\n"
            "• Скасування форми: /cancel або кнопка «Скасувати».\n"
            "• Довідка: /help"
        ),
        "about": (
            "Привіт! Я Артем — приватний Python-розробник. 👨‍💻\n\n"
            "Створюю Telegram-ботів під ключ: від зручних меню до інтеграції зі ШІ "
            "та Google Таблицями. Допомагаю бізнесу автоматизувати рутину і не "
            "втрачати клієнтів.\n\n"
            "Натисніть «📝 Залишити заявку» — і я зв'яжуся з вами!"
        ),
        "non_text": "Будь ласка, надішліть текст. Голосові та медіа я поки не опрацьовую.",
        "thinking": "🧠 <i>Обмірковую відповідь...</i>",
        "server_error": "Ой, сталася помилка 😔 Спробуйте трохи пізніше.",
        "no_lang_set": "Будь ласка, спочатку оберіть мову — введіть /start.",
        "ask_ai_hint": "💬 Просто напишіть ваше запитання нижче, і я відповім! ⬇️",
        "cancel_nothing": "Зараз нічого скасовувати 🙂",
        "cancel_form": "❌ Заповнення анкети скасовано.",
        "lead_start": "Чудово! Давайте оформимо заявку.\nЯк до вас звертатися? 👤",
        "lead_bad_name": "⚠️ Будь ласка, введіть справжнє ім'я (від 2 до 50 символів):",
        "lead_ask_phone": "Приємно познайомитися, {name}!\nВкажіть ваш контактний телефон 📞:",
        "lead_bad_phone": "⚠️ Некоректний номер. Введіть, наприклад: +380991234567",
        "lead_ask_request": "Коротко опишіть, який бот або послуга вас цікавить? 📝",
        "lead_bad_request": "⚠️ Будь ласка, опишіть запит детальніше (хоча б кілька слів):",
        "lead_saving": "⏳ Зберігаю заявку, секунду...",
        "lead_success": "✅ Заявку прийнято! Артем зв'яжеться з вами найближчим часом. 🎉",
        "lead_error": "❌ Не вдалося зберегти заявку. Спробуйте ще раз або напишіть пізніше.",
        "lead_non_text": "Будь ласка, введіть відповідь текстом або натисніть «Скасувати».",
        "antispam_warn": "⚠️ Не поспішайте. Зачекайте кілька секунд перед наступним повідомленням.",
        "ibtn_ask_ai": "💬 Поставити запитання ШІ",
        "ibtn_lead": "📝 Залишити заявку",
        "ibtn_about": "💼 Про автора",
        "ibtn_language": "🌐 Мова / Language",
        "ibtn_cancel": "❌ Скасувати",
        "stats": (
            "📊 <b>Статистика бота:</b>\n\n"
            "💬 Усього діалогів: <b>{total}</b>\n"
            "👥 Унікальних користувачів: <b>{users}</b>\n"
            "📅 Діалогів сьогодні: <b>{today}</b>"
        ),
        "stats_no_access": "⛔ У вас немає доступу до цієї команди.",
        "llm_too_long": "Текст занадто довгий. Скоротіть запитання до 1000 символів.",
        "llm_empty": "Вибачте, не вдалося сформулювати відповідь. Спробуйте перефразувати.",
        "llm_timeout": "Сервери трохи перевантажені. Повторіть через кілька хвилин.",
        "llm_error": "Вибачте, зараз не можу відповісти. Спробуйте пізніше.",
        "rag_no_answer": "У завантажених документах немає інформації з цього питання.",
        "rag_source_footer": "\n\n📄 Джерело: {sources}",
        "doc_indexing": "⏳ Індексую документ <b>{filename}</b>...",
        "doc_indexed_new": "✅ Документ <b>{filename}</b> успішно проіндексовано!\nСтворено фрагментів: <b>{chunks}</b>.",
        "doc_indexed_replaced": "🔄 Документ <b>{filename}</b> оновлено!\nСтару версію замінено. Нових фрагментів: <b>{chunks}</b>.",
        "doc_unsupported": "⚠️ Непідтримуваний формат файлу. Будь ласка, завантажуйте лише <b>.pdf</b> або <b>.docx</b>.",
        "doc_list_empty": "📁 База знань порожня. Завантажте .pdf або .docx файли.",
        "doc_list_header": "📚 <b>Індексовані документи:</b>\n\n{docs}",
        "doc_deleted": "🗑️ Документ <b>{filename}</b> успішно видалено з бази знань.",
        "doc_not_found": "❌ Документ <b>{filename}</b> не знайдено в базі знань.",
        "doc_empty": "⚠️ У документі <b>{filename}</b> не знайдено текстового шару. Можливо, файл порожній або містить лише зображення.",
        "doc_too_large": "⚠️ Файл занадто великий (максимум 20 МБ).",
        "ibtn_admin": "⚙️ Адмін-панель",
        "admin_panel_title": "⚙️ <b>Панель адміністратора</b>\n\nОберіть дію:",
        "admin_btn_stats": "📊 Статистика",
        "admin_btn_docs": "📂 Документи в базі",
        "admin_btn_upload": "📥 Додати документ",
        "admin_btn_clear_all": "🗑 Очистити всю базу RAG",
        "admin_btn_back": "⬅️ Назад",
        "admin_btn_back_main": "⬅️ Головне меню",
        "admin_upload_hint": (
            "📥 <b>Додавання документів:</b>\n\n"
            "Надішліть один або одразу кілька файлів (пачкою) у форматах <b>.pdf</b> або <b>.docx</b> сюди в чат. "
            "Бот автоматично та послідовно проіндексує їх і оновить базу знань."
        ),
        "doc_all_cleared": "🗑 Усі документи ({count}) видалено! Бот перейшов у базовий режим.",
        "admin_del_btn": "🗑 {filename}",
    },
    "en": {
        "welcome": (
            "✅ Language set! 🇬🇧\n\n"
            "Hi, <b>{name}</b>! 👋\n\n"
            "💬 I'm Artem's AI assistant. Press a button below or just type your question."
        ),
        "lang_changed": "✅ Language changed to English 🇬🇧",
        "help": (
            "ℹ️ <b>Bot features:</b>\n\n"
            "• 💬 Type any question — AI replies instantly.\n"
            "• 📝 Submit a request: /lead or the button below.\n"
            "• 🌐 Change language: /language\n"
            "• Cancel form: /cancel or the «Cancel» button.\n"
            "• Help: /help"
        ),
        "about": (
            "Hey! I'm Artem — a freelance Python developer. 👨‍💻\n\n"
            "I build Telegram bots end-to-end: from simple menus to AI assistants "
            "integrated with Google Sheets. I help businesses automate and never "
            "miss a lead.\n\n"
            "Press «📝 Leave a request» and I'll get back to you!"
        ),
        "non_text": "Please send text. I can't process voice or media messages yet.",
        "thinking": "🧠 <i>Thinking...</i>",
        "server_error": "Oops, something went wrong 😔 Please try again shortly.",
        "no_lang_set": "Please choose your language first — type /start.",
        "ask_ai_hint": "💬 Just type your question below and I'll answer! ⬇️",
        "cancel_nothing": "Nothing to cancel right now 🙂",
        "cancel_form": "❌ Form filling cancelled.",
        "lead_start": "Great! Let's fill out your request.\nWhat should I call you? 👤",
        "lead_bad_name": "⚠️ Please enter a real name (2–50 characters):",
        "lead_ask_phone": "Nice to meet you, {name}!\nPlease share your phone number 📞:",
        "lead_bad_phone": "⚠️ Invalid phone number. Example: +380991234567",
        "lead_ask_request": "Briefly describe what kind of bot or service you need 📝:",
        "lead_bad_request": "⚠️ Please describe your request in more detail:",
        "lead_saving": "⏳ Saving your request, one moment...",
        "lead_success": "✅ Request received! Artem will get in touch with you shortly. 🎉",
        "lead_error": "❌ Failed to save your request. Please try again or come back later.",
        "lead_non_text": "Please type your answer or press the Cancel button.",
        "antispam_warn": "⚠️ Please slow down. Wait a couple of seconds.",
        "ibtn_ask_ai": "💬 Ask AI Assistant",
        "ibtn_lead": "📝 Leave a request",
        "ibtn_about": "💼 About author",
        "ibtn_language": "🌐 Мова / Language",
        "ibtn_cancel": "❌ Cancel",
        "stats": (
            "📊 <b>Bot statistics:</b>\n\n"
            "💬 Total dialogs: <b>{total}</b>\n"
            "👥 Unique users: <b>{users}</b>\n"
            "📅 Dialogs today: <b>{today}</b>"
        ),
        "stats_no_access": "⛔ You don't have access to this command.",
        "llm_too_long": "Your message is too long. Please keep it under 1000 characters.",
        "llm_empty": "Sorry, I couldn't formulate an answer. Please try rephrasing.",
        "llm_timeout": "My servers are a bit busy. Please try again in a moment.",
        "llm_error": "Sorry, I can't respond right now. Please try again later.",
        "rag_no_answer": "There is no information on this topic in the uploaded documents.",
        "rag_source_footer": "\n\n📄 Source: {sources}",
        "doc_indexing": "⏳ Indexing document <b>{filename}</b>...",
        "doc_indexed_new": "✅ Document <b>{filename}</b> successfully indexed!\nChunks created: <b>{chunks}</b>.",
        "doc_indexed_replaced": "🔄 Document <b>{filename}</b> updated!\nPrevious version replaced. New chunks: <b>{chunks}</b>.",
        "doc_unsupported": "⚠️ Unsupported file format. Please upload only <b>.pdf</b> or <b>.docx</b> files.",
        "doc_list_empty": "📁 Knowledge base is empty. Upload .pdf or .docx files.",
        "doc_list_header": "📚 <b>Indexed documents:</b>\n\n{docs}",
        "doc_deleted": "🗑️ Document <b>{filename}</b> successfully deleted from knowledge base.",
        "doc_not_found": "❌ Document <b>{filename}</b> not found in knowledge base.",
        "doc_empty": "⚠️ No text layer found in document <b>{filename}</b>. The file may be empty or contain only images.",
        "doc_too_large": "⚠️ File is too large (maximum 20 MB).",
        "ibtn_admin": "⚙️ Admin Panel",
        "admin_panel_title": "⚙️ <b>Admin Panel</b>\n\nSelect an action:",
        "admin_btn_stats": "📊 Statistics",
        "admin_btn_docs": "📂 Documents in KB",
        "admin_btn_upload": "📥 Add document",
        "admin_btn_clear_all": "🗑 Clear entire RAG database",
        "admin_btn_back": "⬅️ Back",
        "admin_btn_back_main": "⬅️ Main menu",
        "admin_upload_hint": (
            "📥 <b>Upload Documents:</b>\n\n"
            "Send one or multiple files in batch (.pdf or .docx) here in chat. "
            "The bot will automatically and sequentially index them and update the knowledge base."
        ),
        "doc_all_cleared": "🗑 All documents ({count}) deleted! Bot switched to Lite mode.",
        "admin_del_btn": "🗑 {filename}",
    },
}

LANG_SELECT_MSG = "🌐 <b>Оберіть мову / Choose your language:</b>"


def get_lang(language_code: str | None) -> Language:
    return "uk" if language_code and language_code.startswith("uk") else "en"


def t(key: str, lang: Language = "uk", **kwargs) -> str:
    template = TEXTS.get(lang, TEXTS["uk"]).get(key) or TEXTS["uk"].get(key, key)
    return template.format(**kwargs) if kwargs else template

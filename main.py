import asyncio

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.types import ReplyKeyboardRemove

import config
from handlers import admin, lead_form
from i18n import LANG_SELECT_MSG, get_lang, t
from keyboards import get_language_keyboard, get_main_keyboard
from middlewares.antispam import AntiSpamMiddleware
from services.db import get_user_language, init_db, log_dialog, set_user_language
from services.llm import get_ai_answer

bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

dp.message.middleware(AntiSpamMiddleware(limit_seconds=2))
dp.include_router(lead_form.router)
dp.include_router(admin.router)


@dp.message(CommandStart())
async def cmd_start(message: types.Message) -> None:
    user_id = message.from_user.id
    await message.answer("👋", reply_markup=ReplyKeyboardRemove())

    lang = await get_user_language(user_id)
    if not lang:
        await message.answer(LANG_SELECT_MSG, reply_markup=get_language_keyboard())
        return

    name = message.from_user.first_name or ""
    await message.answer(
        t("welcome", lang, name=name),
        reply_markup=get_main_keyboard(lang),
    )


@dp.message(Command("language"))
async def cmd_language(message: types.Message) -> None:
    await message.answer("🌐", reply_markup=ReplyKeyboardRemove())
    await message.answer(LANG_SELECT_MSG, reply_markup=get_language_keyboard())


@dp.callback_query(F.data.startswith("lang:"))
async def handle_language_choice(callback: types.CallbackQuery) -> None:
    selected_lang = callback.data.split(":")[1]
    if selected_lang not in ("uk", "en"):
        selected_lang = "uk"

    user_id = callback.from_user.id
    await set_user_language(user_id, selected_lang)
    await callback.answer()

    name = callback.from_user.first_name or ""
    await callback.message.answer(
        t("welcome", selected_lang, name=name),
        reply_markup=get_main_keyboard(selected_lang),
    )


@dp.callback_query(F.data == "menu:ask_ai")
async def handle_menu_ask_ai(callback: types.CallbackQuery) -> None:
    user_id = callback.from_user.id
    lang = await get_user_language(user_id) or get_lang(callback.from_user.language_code)
    await callback.answer()
    await callback.message.answer(t("ask_ai_hint", lang))


@dp.callback_query(F.data == "menu:about")
async def handle_menu_about(callback: types.CallbackQuery) -> None:
    user_id = callback.from_user.id
    lang = await get_user_language(user_id) or get_lang(callback.from_user.language_code)
    await callback.answer()
    await callback.message.answer(t("about", lang), reply_markup=get_main_keyboard(lang))


@dp.callback_query(F.data == "menu:language")
async def handle_menu_language(callback: types.CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(LANG_SELECT_MSG, reply_markup=get_language_keyboard())


@dp.message(Command("help"))
async def cmd_help(message: types.Message) -> None:
    user_id = message.from_user.id
    lang = await get_user_language(user_id) or get_lang(message.from_user.language_code)
    await message.answer(
        t("help", lang),
        reply_markup=get_main_keyboard(lang),
    )


@dp.message(~F.text)
async def handle_non_text(message: types.Message) -> None:
    user_id = message.from_user.id
    lang = await get_user_language(user_id) or get_lang(message.from_user.language_code)
    await message.answer(t("non_text", lang))


@dp.message(StateFilter(None), F.text)
async def handle_user_message(message: types.Message) -> None:
    user_id = message.from_user.id
    lang = await get_user_language(user_id)
    if not lang:
        await message.answer(LANG_SELECT_MSG, reply_markup=get_language_keyboard())
        return

    wait_message = await message.answer(t("thinking", lang))
    try:
        answer = await get_ai_answer(user_id, message.text, lang)
        answer = f"{answer[:4000]}..." if len(answer) > 4000 else answer
        await wait_message.edit_text(answer, parse_mode=None)

        username = message.from_user.username or message.from_user.first_name
        await log_dialog(user_id, username, message.text, answer)
    except Exception as e:
        config.logger.error("Failed to process user message: %s", e)
        await wait_message.edit_text(t("server_error", lang))


async def main() -> None:
    await init_db()
    config.logger.info("Bot started successfully, listening for updates...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
import asyncio
import html

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.types import ReplyKeyboardRemove

import config
from handlers import admin, lead_form
from i18n import LANG_SELECT_MSG, get_lang, t
from keyboards import get_language_keyboard, get_main_keyboard
from middlewares.antispam import AntiSpamMiddleware
from services.db import (
    clear_user_history,
    get_user_language,
    init_db,
    log_dialog,
    set_user_language,
)
from services.llm import clear_history, get_ai_answer

bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(events_isolation=SimpleEventIsolation())
user_router = Router()
user_router.message.filter(F.chat.type == ChatType.PRIVATE)
user_router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)

dp.message.middleware(AntiSpamMiddleware(limit_seconds=2))
dp.include_router(lead_form.router)
dp.include_router(admin.router)
dp.include_router(user_router)


async def _clear_active_state(state: FSMContext | None) -> None:
    if state is not None and await state.get_state() is not None:
        await state.clear()


@dp.errors()
async def global_error_handler(event: types.ErrorEvent) -> bool:
    update_id = getattr(event.update, "update_id", None)
    config.logger.error(
        "Unhandled error while processing update %s: %s",
        update_id,
        event.exception,
        exc_info=event.exception,
    )
    return True


@user_router.message(StateFilter("*"), CommandStart())
async def cmd_start(message: types.Message, state: FSMContext | None = None) -> None:
    await _clear_active_state(state)

    user_id = message.from_user.id
    if config.ADMIN_ID and user_id != config.ADMIN_ID:
        config.logger.info("User %s connected (ADMIN_ID configured as %s)", user_id, config.ADMIN_ID)

    await message.answer("👋", reply_markup=ReplyKeyboardRemove())

    lang = await get_user_language(user_id)
    if not lang:
        await message.answer(LANG_SELECT_MSG, reply_markup=get_language_keyboard())
        return

    name = html.escape(message.from_user.first_name or "")
    is_admin = bool(config.ADMIN_ID) and user_id == config.ADMIN_ID
    await message.answer(
        t("welcome", lang, name=name),
        reply_markup=get_main_keyboard(lang, is_admin=is_admin),
    )


@user_router.message(StateFilter("*"), Command("language"))
async def cmd_language(message: types.Message, state: FSMContext | None = None) -> None:
    await _clear_active_state(state)
    await message.answer("🌐", reply_markup=ReplyKeyboardRemove())
    await message.answer(LANG_SELECT_MSG, reply_markup=get_language_keyboard())


@user_router.callback_query(F.data.startswith("lang:"))
async def handle_language_choice(callback: types.CallbackQuery) -> None:
    selected_lang = callback.data.split(":")[1]
    if selected_lang not in ("uk", "en"):
        selected_lang = "uk"

    user_id = callback.from_user.id
    await set_user_language(user_id, selected_lang)
    await callback.answer()

    name = html.escape(callback.from_user.first_name or "")
    is_admin = bool(config.ADMIN_ID) and user_id == config.ADMIN_ID
    await callback.message.answer(
        t("welcome", selected_lang, name=name),
        reply_markup=get_main_keyboard(selected_lang, is_admin=is_admin),
    )


@user_router.callback_query(F.data == "menu:ask_ai")
async def handle_menu_ask_ai(callback: types.CallbackQuery) -> None:
    user_id = callback.from_user.id
    lang = await get_user_language(user_id) or get_lang(callback.from_user.language_code)
    await callback.answer()
    await callback.message.answer(t("ask_ai_hint", lang))


@user_router.callback_query(F.data == "menu:about")
async def handle_menu_about(callback: types.CallbackQuery) -> None:
    user_id = callback.from_user.id
    lang = await get_user_language(user_id) or get_lang(callback.from_user.language_code)
    await callback.answer()
    is_admin = bool(config.ADMIN_ID) and user_id == config.ADMIN_ID
    await callback.message.answer(t("about", lang), reply_markup=get_main_keyboard(lang, is_admin=is_admin))


@user_router.callback_query(F.data == "menu:language")
async def handle_menu_language(callback: types.CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(LANG_SELECT_MSG, reply_markup=get_language_keyboard())


@user_router.message(StateFilter("*"), Command("help"))
async def cmd_help(message: types.Message, state: FSMContext | None = None) -> None:
    await _clear_active_state(state)

    user_id = message.from_user.id
    lang = await get_user_language(user_id) or get_lang(message.from_user.language_code)
    is_admin = bool(config.ADMIN_ID) and user_id == config.ADMIN_ID
    await message.answer(
        t("help", lang),
        reply_markup=get_main_keyboard(lang, is_admin=is_admin),
    )


@user_router.message(StateFilter("*"), Command("clear"))
async def cmd_clear(message: types.Message, state: FSMContext | None = None) -> None:
    await _clear_active_state(state)

    user_id = message.from_user.id
    await clear_user_history(user_id)
    clear_history(user_id)

    lang = await get_user_language(user_id) or get_lang(message.from_user.language_code)
    is_admin = bool(config.ADMIN_ID) and user_id == config.ADMIN_ID
    await message.answer(
        t("history_cleared", lang),
        reply_markup=get_main_keyboard(lang, is_admin=is_admin),
    )


@user_router.message(~F.text)
async def handle_non_text(message: types.Message) -> None:
    user_id = message.from_user.id
    lang = await get_user_language(user_id) or get_lang(message.from_user.language_code)
    await message.answer(t("non_text", lang))


@user_router.message(StateFilter(None), F.text)
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
    except Exception:  # noqa: BLE001 - catch any RAG/LLM/API error to clear thinking status
        config.logger.exception("Failed to process user message for user %s", user_id)
        try:
            await wait_message.edit_text(t("server_error", lang))
        except (TelegramAPIError, OSError, RuntimeError):
            try:
                await wait_message.delete()
            except (TelegramAPIError, OSError, RuntimeError):
                config.logger.debug("Failed to delete thinking message for user %s", user_id)
            try:
                await message.answer(t("server_error", lang))
            except (TelegramAPIError, OSError, RuntimeError):
                config.logger.exception("Failed to send error fallback for user %s", user_id)


async def main() -> None:
    await init_db()
    config.logger.info("Bot started successfully, listening for updates...")
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import re

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import config
from i18n import get_lang, t
from keyboards import get_cancel_keyboard, get_main_keyboard
from services.db import get_user_language
from services.sheets import add_lead_to_sheet

router = Router()

PHONE_REGEX = re.compile(r'^\+?[0-9\s\-\(\)]{7,20}$')


class LeadForm(StatesGroup):
    waiting_for_name = State()
    waiting_for_phone = State()
    waiting_for_request = State()


@router.message(Command("cancel"))
@router.callback_query(F.data == "form:cancel")
async def cancel_handler(event: types.Message | types.CallbackQuery, state: FSMContext):
    user = event.from_user
    if isinstance(event, types.CallbackQuery):
        await event.answer()
        message = event.message
    else:
        message = event

    data = await state.get_data()
    lang = data.get("lang") or await get_user_language(user.id) or get_lang(user.language_code)

    current_state = await state.get_state()
    if current_state is None:
        await message.answer(t("cancel_nothing", lang))
        return

    await state.clear()
    await message.answer(t("cancel_form", lang), reply_markup=get_main_keyboard(lang))


@router.message(Command("lead"))
@router.callback_query(F.data == "menu:lead")
async def start_lead_form(event: types.Message | types.CallbackQuery, state: FSMContext):
    user = event.from_user
    if isinstance(event, types.CallbackQuery):
        await event.answer()
        message = event.message
    else:
        message = event

    lang = await get_user_language(user.id) or get_lang(user.language_code)
    await state.update_data(lang=lang)
    await message.answer(t("lead_start", lang), reply_markup=get_cancel_keyboard(lang))
    await state.set_state(LeadForm.waiting_for_name)


@router.message(LeadForm.waiting_for_name, F.text)
async def process_name(message: types.Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang") or get_lang(message.from_user.language_code)

    name = message.text.strip()
    if len(name) < 2 or len(name) > 50 or name.isdigit():
        await message.answer(t("lead_bad_name", lang), reply_markup=get_cancel_keyboard(lang))
        return

    await state.update_data(name=name)
    await message.answer(t("lead_ask_phone", lang, name=name), reply_markup=get_cancel_keyboard(lang))
    await state.set_state(LeadForm.waiting_for_phone)


@router.message(LeadForm.waiting_for_phone, F.text)
async def process_phone(message: types.Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang") or get_lang(message.from_user.language_code)

    digits = re.sub(r'\D', '', message.text)
    if not PHONE_REGEX.match(message.text) or not (7 <= len(digits) <= 15):
        await message.answer(t("lead_bad_phone", lang), reply_markup=get_cancel_keyboard(lang))
        return

    await state.update_data(phone=message.text.strip())
    await message.answer(t("lead_ask_request", lang), reply_markup=get_cancel_keyboard(lang))
    await state.set_state(LeadForm.waiting_for_request)


@router.message(LeadForm.waiting_for_request, F.text)
async def process_request(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    lang = user_data.get("lang") or get_lang(message.from_user.language_code)
    request = message.text.strip()

    if len(request) < 3:
        await message.answer(t("lead_bad_request", lang), reply_markup=get_cancel_keyboard(lang))
        return

    await message.answer(t("lead_saving", lang))
    name = user_data.get("name")
    phone = user_data.get("phone")

    success = await asyncio.to_thread(add_lead_to_sheet, name, phone, request)
    if not success:
        await message.answer(t("lead_error", lang), reply_markup=get_cancel_keyboard(lang))
        return

    await message.answer(t("lead_success", lang), reply_markup=get_main_keyboard(lang))
    if config.ADMIN_ID:
        try:
            username = f"@{message.from_user.username}" if message.from_user.username else "—"
            admin_text = (
                f"🔔 <b>Нова заявка!</b>\n\n"
                f"👤 <b>Ім'я:</b> {name}\n"
                f"📞 <b>Телефон:</b> {phone}\n"
                f"📝 <b>Запит:</b> {request}\n"
                f"✈️ <b>TG:</b> {username} (ID: <code>{message.from_user.id}</code>)"
            )
            await message.bot.send_message(chat_id=config.ADMIN_ID, text=admin_text)
        except Exception as e:
            config.logger.warning(f"Failed to notify admin: {e}")
    await state.clear()


@router.message(LeadForm.waiting_for_name)
@router.message(LeadForm.waiting_for_phone)
@router.message(LeadForm.waiting_for_request)
async def handle_non_text_in_form(message: types.Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang") or get_lang(message.from_user.language_code)
    await message.answer(t("lead_non_text", lang), reply_markup=get_cancel_keyboard(lang))
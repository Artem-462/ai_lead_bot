import html
import re
from uuid import uuid4

from aiogram import F, Router, types
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.exc import SQLAlchemyError

import config
from i18n import get_lang, t
from keyboards import get_cancel_keyboard, get_main_keyboard
from services.db import create_lead, get_user_language, mark_lead_failed, mark_lead_sent
from services.sheets import add_lead_to_sheet

router = Router()
router.message.filter(F.chat.type == ChatType.PRIVATE)
router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)

PHONE_REGEX = re.compile(r'^\+?[0-9\s\-\(\)]{7,20}$')


class LeadForm(StatesGroup):
    waiting_for_name = State()
    waiting_for_phone = State()
    waiting_for_request = State()


@router.message(StateFilter("*"), Command("cancel"))
@router.callback_query(StateFilter("*"), F.data == "form:cancel")
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
    is_admin = bool(config.ADMIN_ID) and user.id == config.ADMIN_ID
    await message.answer(t("cancel_form", lang), reply_markup=get_main_keyboard(lang, is_admin=is_admin))


@router.message(StateFilter("*"), Command("lead"))
@router.callback_query(StateFilter("*"), F.data == "menu:lead")
async def start_lead_form(event: types.Message | types.CallbackQuery, state: FSMContext):
    user = event.from_user
    if isinstance(event, types.CallbackQuery):
        await event.answer()
        message = event.message
    else:
        message = event

    lang = await get_user_language(user.id) or get_lang(user.language_code)
    await state.update_data(lang=lang, lead_uuid=str(uuid4()))
    await message.answer(t("lead_start", lang), reply_markup=get_cancel_keyboard(lang))
    await state.set_state(LeadForm.waiting_for_name)


@router.message(LeadForm.waiting_for_name, F.text, ~F.text.startswith("/"))
async def process_name(message: types.Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang") or get_lang(message.from_user.language_code)

    name = message.text.strip()
    if len(name) < 2 or len(name) > 50 or name.isdigit():
        await message.answer(t("lead_bad_name", lang), reply_markup=get_cancel_keyboard(lang))
        return

    await state.update_data(name=name)
    await message.answer(
        t("lead_ask_phone", lang, name=html.escape(name)),
        reply_markup=get_cancel_keyboard(lang),
    )
    await state.set_state(LeadForm.waiting_for_phone)


@router.message(LeadForm.waiting_for_phone, F.text, ~F.text.startswith("/"))
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


@router.message(LeadForm.waiting_for_request, F.text, ~F.text.startswith("/"))
async def process_request(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    lang = user_data.get("lang") or get_lang(message.from_user.language_code)
    request = message.text.strip()

    if len(request) < 3:
        await message.answer(t("lead_bad_request", lang), reply_markup=get_cancel_keyboard(lang))
        return

    name = user_data.get("name")
    phone = user_data.get("phone")
    lead_uuid = user_data.get("lead_uuid") or str(uuid4())
    await state.update_data(lead_uuid=lead_uuid)

    try:
        lead = await create_lead(
            user_id=message.from_user.id,
            username=message.from_user.username,
            name=name,
            phone=phone,
            request_text=request,
            lead_uuid=lead_uuid,
        )
    except (SQLAlchemyError, OSError) as error:
        config.logger.error(
            "Failed to persist lead (lead_uuid=%s, error=%s)", lead_uuid, type(error).__name__
        )
        await message.answer(t("lead_error", lang), reply_markup=get_cancel_keyboard(lang))
        return

    try:
        await message.answer(t("lead_saving", lang))
        success = lead.status == "sent"
        if not success:
            try:
                success = await add_lead_to_sheet(
                    lead.name, lead.phone, lead.request_text, lead_uuid=lead.lead_uuid
                )
                if not success:
                    raise RuntimeError("Google Sheets did not accept the lead")
            except Exception as error:  # noqa: BLE001 - keep accepted leads on any synchronization error
                success = False
                await mark_lead_failed(lead.id, f"{type(error).__name__}: {error}")
                config.logger.warning(
                    "Lead kept locally after sync failure (lead_uuid=%s, error=%s)",
                    lead.lead_uuid,
                    type(error).__name__,
                )
            else:
                await mark_lead_sent(lead.id)

        if config.ADMIN_ID:
            try:
                username = f"@{lead.username}" if lead.username else "—"
                admin_text = (
                    f"🔔 <b>Нова заявка!</b>\n\n"
                    f"👤 <b>Ім'я:</b> {html.escape(lead.name)}\n"
                    f"📞 <b>Телефон:</b> {html.escape(lead.phone)}\n"
                    f"📝 <b>Запит:</b> {html.escape(lead.request_text)}\n"
                    f"✈️ <b>TG:</b> {html.escape(username)} (ID: <code>{lead.user_id}</code>)\n"
                    f"🆔 <code>{html.escape(lead.lead_uuid)}</code>"
                )
                if not success:
                    admin_text += (
                        "\n\n⚠️ Синхронізація з Google Sheets тимчасово не вдалася. "
                        "Заявку збережено локально."
                    )
                await message.bot.send_message(chat_id=config.ADMIN_ID, text=admin_text)
            except (TelegramAPIError, OSError, RuntimeError) as error:
                config.logger.warning(
                    "Failed to notify admin (lead_uuid=%s, error=%s)",
                    lead.lead_uuid,
                    type(error).__name__,
                )

        is_admin = bool(config.ADMIN_ID) and message.from_user.id == config.ADMIN_ID
        await message.answer(
            t("lead_success", lang), reply_markup=get_main_keyboard(lang, is_admin=is_admin)
        )
    finally:
        await state.clear()


@router.message(LeadForm.waiting_for_name, ~F.text)
@router.message(LeadForm.waiting_for_phone, ~F.text)
@router.message(LeadForm.waiting_for_request, ~F.text)
async def handle_non_text_in_form(message: types.Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang") or get_lang(message.from_user.language_code)
    await message.answer(t("lead_non_text", lang), reply_markup=get_cancel_keyboard(lang))

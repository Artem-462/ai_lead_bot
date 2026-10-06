"""
tests/test_handlers.py
Tests for handlers/admin.py, handlers/lead_form.py, keyboards.py, and main.py callbacks.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------
from aiogram import types as aiogram_types


class MockMessage(aiogram_types.Message, MagicMock):
    pass


class MockCallbackQuery(aiogram_types.CallbackQuery, MagicMock):
    pass


def make_message(text="hello", user_id=123, lang_code="uk", username="testuser"):
    msg = MockMessage()
    msg.text = text
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.from_user.username = username
    msg.from_user.first_name = "Test"
    msg.from_user.language_code = lang_code
    msg.answer = AsyncMock()
    msg.bot = MagicMock()
    msg.bot.send_message = AsyncMock()
    return msg


def make_callback(data="menu:lead", user_id=123, lang_code="uk", username="testuser"):
    cb = MockCallbackQuery()
    cb.data = data
    cb.from_user = MagicMock()
    cb.from_user.id = user_id
    cb.from_user.username = username
    cb.from_user.first_name = "Test"
    cb.from_user.language_code = lang_code
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.answer = AsyncMock()
    cb.message.edit_text = AsyncMock()
    return cb


def make_state(current=None, data=None):
    state = AsyncMock()
    state.get_state = AsyncMock(return_value=current)
    state.get_data = AsyncMock(return_value=data or {"lang": "uk"})
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()
    state.clear = AsyncMock()
    return state


# ---------------------------------------------------------------------------
# Import handlers and keyboards
# ---------------------------------------------------------------------------
import handlers.admin as admin_handler
import handlers.lead_form as lead_handler
import keyboards
from i18n import t

# ===========================================================================
# keyboards.py — InlineKeyboardMarkup structure
# ===========================================================================

class TestKeyboards:
    def test_language_keyboard(self):
        kb = keyboards.get_language_keyboard()
        assert kb is not None

    def test_main_keyboard_uk(self):
        kb = keyboards.get_main_keyboard("uk")
        assert kb is not None

    def test_main_keyboard_en(self):
        kb = keyboards.get_main_keyboard("en")
        assert kb is not None

    def test_cancel_keyboard(self):
        kb = keyboards.get_cancel_keyboard("uk")
        assert kb is not None


# ===========================================================================
# handlers/admin.py — cmd_stats
# ===========================================================================

class TestCmdStats:
    """Tests for the /stats admin command."""

    @pytest.mark.asyncio
    async def test_non_admin_receives_no_access_uk(self):
        msg = make_message(user_id=999_999, lang_code="uk")
        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="uk"):
            mock_cfg.ADMIN_ID = 111
            await admin_handler.cmd_stats(msg)
        msg.answer.assert_awaited_once()
        call_text = msg.answer.call_args[0][0]
        assert call_text == t("stats_no_access", "uk")

    @pytest.mark.asyncio
    async def test_non_admin_receives_no_access_en(self):
        msg = make_message(user_id=999_999, lang_code="en")
        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="en"):
            mock_cfg.ADMIN_ID = 111
            await admin_handler.cmd_stats(msg)
        msg.answer.assert_awaited_once()
        call_text = msg.answer.call_args[0][0]
        assert call_text == t("stats_no_access", "en")

    @pytest.mark.asyncio
    async def test_admin_receives_stats_uk(self):
        admin_id = 42
        msg = make_message(user_id=admin_id, lang_code="uk")
        fake_stats = {"total": 5, "users": 3, "today": 2}
        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="uk"), \
             patch("handlers.admin.get_dialog_stats", new_callable=AsyncMock, return_value=fake_stats):
            mock_cfg.ADMIN_ID = admin_id
            await admin_handler.cmd_stats(msg)
        msg.answer.assert_awaited_once()
        call_text = msg.answer.call_args[0][0]
        assert "5" in call_text
        assert "3" in call_text
        assert "2" in call_text
        assert call_text == t("stats", "uk", **fake_stats)

    @pytest.mark.asyncio
    async def test_admin_receives_stats_en(self):
        admin_id = 42
        msg = make_message(user_id=admin_id, lang_code="en")
        fake_stats = {"total": 5, "users": 3, "today": 2}
        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="en"), \
             patch("handlers.admin.get_dialog_stats", new_callable=AsyncMock, return_value=fake_stats):
            mock_cfg.ADMIN_ID = admin_id
            await admin_handler.cmd_stats(msg)
        msg.answer.assert_awaited_once()
        call_text = msg.answer.call_args[0][0]
        assert call_text == t("stats", "en", **fake_stats)

    @pytest.mark.asyncio
    async def test_no_admin_id_configured_returns_no_access(self):
        msg = make_message(user_id=42, lang_code="uk")
        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="uk"):
            mock_cfg.ADMIN_ID = None
            await admin_handler.cmd_stats(msg)
        msg.answer.assert_awaited_once()
        call_text = msg.answer.call_args[0][0]
        assert call_text == t("stats_no_access", "uk")


# ===========================================================================
# handlers/lead_form.py — start_lead_form
# ===========================================================================

class TestStartLeadForm:
    @pytest.mark.asyncio
    async def test_start_via_command(self):
        msg = make_message(text="/lead")
        state = make_state()
        with patch("handlers.lead_form.get_user_language", new_callable=AsyncMock, return_value="uk"):
            await lead_handler.start_lead_form(msg, state)
        state.set_state.assert_awaited_once_with(lead_handler.LeadForm.waiting_for_name)
        msg.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_via_callback(self):
        cb = make_callback(data="menu:lead")
        state = make_state()
        with patch("handlers.lead_form.get_user_language", new_callable=AsyncMock, return_value="uk"):
            await lead_handler.start_lead_form(cb, state)
        cb.answer.assert_awaited_once()
        state.set_state.assert_awaited_once_with(lead_handler.LeadForm.waiting_for_name)
        cb.message.answer.assert_awaited_once()


# ===========================================================================
# handlers/lead_form.py — process_name
# ===========================================================================

class TestProcessName:
    @pytest.mark.asyncio
    async def test_valid_name_calls_update_data(self):
        msg = make_message(text="Anna")
        state = make_state(data={"lang": "uk"})
        await lead_handler.process_name(msg, state)
        state.update_data.assert_awaited()

    @pytest.mark.asyncio
    async def test_valid_name_advances_state(self):
        msg = make_message(text="Anna")
        state = make_state(data={"lang": "uk"})
        await lead_handler.process_name(msg, state)
        state.set_state.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_numeric_input_rejected(self):
        msg = make_message(text="1234")
        state = make_state(data={"lang": "uk"})
        await lead_handler.process_name(msg, state)
        msg.answer.assert_awaited_once()
        assert t("lead_bad_name", "uk") in msg.answer.call_args[0][0]
        state.update_data.assert_not_awaited()
        state.set_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_single_char_name_rejected(self):
        msg = make_message(text="a")
        state = make_state(data={"lang": "uk"})
        await lead_handler.process_name(msg, state)
        msg.answer.assert_awaited_once()
        assert t("lead_bad_name", "uk") in msg.answer.call_args[0][0]
        state.set_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_too_long_name_rejected(self):
        msg = make_message(text="x" * 51)
        state = make_state(data={"lang": "uk"})
        await lead_handler.process_name(msg, state)
        msg.answer.assert_awaited_once()
        assert t("lead_bad_name", "uk") in msg.answer.call_args[0][0]
        state.set_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_valid_name_en_lang(self):
        msg = make_message(text="John", lang_code="en")
        state = make_state(data={"lang": "en"})
        await lead_handler.process_name(msg, state)
        state.update_data.assert_awaited()
        state.set_state.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exactly_2_chars_accepted(self):
        msg = make_message(text="Jo")
        state = make_state(data={"lang": "uk"})
        await lead_handler.process_name(msg, state)
        state.update_data.assert_awaited()

    @pytest.mark.asyncio
    async def test_exactly_50_chars_accepted(self):
        msg = make_message(text="A" * 50)
        state = make_state(data={"lang": "uk"})
        await lead_handler.process_name(msg, state)
        state.update_data.assert_awaited()


# ===========================================================================
# handlers/lead_form.py — process_phone
# ===========================================================================

class TestProcessPhone:
    @pytest.mark.asyncio
    async def test_valid_phone_calls_update_data(self):
        msg = make_message(text="+380991234567")
        state = make_state(data={"lang": "uk"})
        await lead_handler.process_phone(msg, state)
        state.update_data.assert_awaited()

    @pytest.mark.asyncio
    async def test_valid_phone_advances_state(self):
        msg = make_message(text="+380991234567")
        state = make_state(data={"lang": "uk"})
        await lead_handler.process_phone(msg, state)
        state.set_state.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invalid_phone_abc_rejected(self):
        msg = make_message(text="abc")
        state = make_state(data={"lang": "uk"})
        await lead_handler.process_phone(msg, state)
        msg.answer.assert_awaited_once()
        assert t("lead_bad_phone", "uk") in msg.answer.call_args[0][0]
        state.update_data.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_phone_too_short_rejected(self):
        msg = make_message(text="123")
        state = make_state(data={"lang": "uk"})
        await lead_handler.process_phone(msg, state)
        msg.answer.assert_awaited_once()
        state.set_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_valid_international_phone(self):
        msg = make_message(text="+44 7700 900123")
        state = make_state(data={"lang": "en"})
        await lead_handler.process_phone(msg, state)
        state.update_data.assert_awaited()

    @pytest.mark.asyncio
    async def test_valid_phone_en_lang(self):
        msg = make_message(text="+380991234567", lang_code="en")
        state = make_state(data={"lang": "en"})
        await lead_handler.process_phone(msg, state)
        state.update_data.assert_awaited()


# ===========================================================================
# handlers/lead_form.py — process_request
# ===========================================================================

class TestProcessRequest:
    @pytest.mark.asyncio
    async def test_short_request_rejected(self):
        msg = make_message(text="hi")
        state = make_state(data={"lang": "uk", "name": "Anna", "phone": "+380991234567"})
        await lead_handler.process_request(msg, state)
        msg.answer.assert_awaited()
        first_call_text = msg.answer.call_args_list[0][0][0]
        assert first_call_text == t("lead_bad_request", "uk")

    @pytest.mark.asyncio
    async def test_single_char_request_rejected(self):
        msg = make_message(text="x")
        state = make_state(data={"lang": "uk", "name": "Anna", "phone": "+380991234567"})
        await lead_handler.process_request(msg, state)
        first_call_text = msg.answer.call_args_list[0][0][0]
        assert first_call_text == t("lead_bad_request", "uk")

    @pytest.mark.asyncio
    async def test_exactly_2_chars_rejected(self):
        msg = make_message(text="ab")
        state = make_state(data={"lang": "uk", "name": "Anna", "phone": "+380991234567"})
        await lead_handler.process_request(msg, state)
        first_call_text = msg.answer.call_args_list[0][0][0]
        assert first_call_text == t("lead_bad_request", "uk")

    @pytest.mark.asyncio
    async def test_valid_request_sends_saving_message(self):
        msg = make_message(text="I need a Telegram bot for my shop")
        state = make_state(data={"lang": "uk", "name": "Anna", "phone": "+380991234567"})
        with patch("handlers.lead_form.add_lead_to_sheet", return_value=True), \
             patch("handlers.lead_form.config") as mock_cfg:
            mock_cfg.ADMIN_ID = None
            await lead_handler.process_request(msg, state)
        first_call = msg.answer.call_args_list[0][0][0]
        assert first_call == t("lead_saving", "uk")

    @pytest.mark.asyncio
    async def test_valid_request_on_success_clears_state(self):
        msg = make_message(text="I need a Telegram bot")
        state = make_state(data={"lang": "uk", "name": "Anna", "phone": "+380991234567"})
        with patch("handlers.lead_form.add_lead_to_sheet", return_value=True), \
             patch("handlers.lead_form.config") as mock_cfg:
            mock_cfg.ADMIN_ID = None
            await lead_handler.process_request(msg, state)
        state.clear.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_valid_request_on_failure_does_not_clear_state(self):
        msg = make_message(text="I need a bot")
        state = make_state(data={"lang": "uk", "name": "Anna", "phone": "+380991234567"})
        with patch("handlers.lead_form.add_lead_to_sheet", return_value=False), \
             patch("handlers.lead_form.config") as mock_cfg:
            mock_cfg.ADMIN_ID = None
            await lead_handler.process_request(msg, state)
        state.clear.assert_not_awaited()


# ===========================================================================
# handlers/lead_form.py — cancel_handler (Message & CallbackQuery)
# ===========================================================================

class TestCancelHandler:
    @pytest.mark.asyncio
    async def test_no_active_state_answers_cancel_nothing(self):
        msg = make_message(text="/cancel", lang_code="uk")
        state = make_state(current=None)
        await lead_handler.cancel_handler(msg, state)
        msg.answer.assert_awaited_once()
        call_text = msg.answer.call_args[0][0]
        assert call_text == t("cancel_nothing", "uk")

    @pytest.mark.asyncio
    async def test_no_active_state_does_not_clear(self):
        msg = make_message(text="/cancel", lang_code="uk")
        state = make_state(current=None)
        await lead_handler.cancel_handler(msg, state)
        state.clear.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_active_state_clears_state(self):
        msg = make_message(text="/cancel", lang_code="uk")
        state = make_state(current="LeadForm:waiting_for_name")
        await lead_handler.cancel_handler(msg, state)
        state.clear.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_active_state_answers_cancel_form(self):
        msg = make_message(text="/cancel", lang_code="uk")
        state = make_state(current="LeadForm:waiting_for_phone")
        await lead_handler.cancel_handler(msg, state)
        msg.answer.assert_awaited_once()
        call_text = msg.answer.call_args[0][0]
        assert call_text == t("cancel_form", "uk")

    @pytest.mark.asyncio
    async def test_cancel_nothing_en_lang(self):
        msg = make_message(text="/cancel", lang_code="en")
        state = make_state(current=None, data={"lang": "en"})
        await lead_handler.cancel_handler(msg, state)
        call_text = msg.answer.call_args[0][0]
        assert call_text == t("cancel_nothing", "en")

    @pytest.mark.asyncio
    async def test_cancel_form_en_lang(self):
        msg = make_message(text="/cancel", lang_code="en")
        state = make_state(current="LeadForm:waiting_for_request", data={"lang": "en"})
        await lead_handler.cancel_handler(msg, state)
        call_text = msg.answer.call_args[0][0]
        assert call_text == t("cancel_form", "en")

    @pytest.mark.asyncio
    async def test_cancel_via_inline_callback(self):
        cb = make_callback(data="form:cancel")
        state = make_state(current="LeadForm:waiting_for_name")
        await lead_handler.cancel_handler(cb, state)
        cb.answer.assert_awaited_once()
        state.clear.assert_awaited_once()
        cb.message.answer.assert_awaited_once()
        call_text = cb.message.answer.call_args[0][0]
        assert call_text == t("cancel_form", "uk")

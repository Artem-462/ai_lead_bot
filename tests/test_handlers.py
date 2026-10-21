import asyncio
import io
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import types as aiogram_types

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import handlers.admin as admin_handler
import handlers.lead_form as lead_handler
import keyboards
from i18n import t


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

    def test_main_keyboard_is_admin_false(self):
        kb_uk = keyboards.get_main_keyboard("uk", is_admin=False)
        datas_uk = [btn.callback_data for row in kb_uk.inline_keyboard for btn in row]
        assert "admin:panel" not in datas_uk
        texts_uk = [btn.text for row in kb_uk.inline_keyboard for btn in row]
        assert not any("Адмін-панель" in txt for txt in texts_uk)

        kb_en = keyboards.get_main_keyboard("en", is_admin=False)
        datas_en = [btn.callback_data for row in kb_en.inline_keyboard for btn in row]
        assert "admin:panel" not in datas_en
        texts_en = [btn.text for row in kb_en.inline_keyboard for btn in row]
        assert not any("Admin Panel" in txt for txt in texts_en)

    def test_main_keyboard_is_admin_true(self):
        kb_uk = keyboards.get_main_keyboard("uk", is_admin=True)
        datas_uk = [btn.callback_data for row in kb_uk.inline_keyboard for btn in row]
        assert "admin:panel" in datas_uk
        texts_uk = [btn.text for row in kb_uk.inline_keyboard for btn in row]
        assert any("Адмін-панель" in txt for txt in texts_uk)

        kb_en = keyboards.get_main_keyboard("en", is_admin=True)
        datas_en = [btn.callback_data for row in kb_en.inline_keyboard for btn in row]
        assert "admin:panel" in datas_en
        texts_en = [btn.text for row in kb_en.inline_keyboard for btn in row]
        assert any("Admin Panel" in txt for txt in texts_en)

    def test_admin_panel_keyboard(self):
        kb = keyboards.get_admin_panel_keyboard("uk")
        datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert datas == ["admin:stats", "admin:docs", "admin:upload_hint", "admin:back_main"]

    def test_admin_docs_keyboard(self):
        docs = [{"id": 1, "filename": "test1.pdf"}, {"filename": "test2.docx"}]
        kb = keyboards.get_admin_docs_keyboard(docs, "uk")
        datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert "admin:del:1" in datas
        assert "admin:del:test2.docx" in datas
        assert "admin:clear_all_docs" in datas
        assert datas[-2] == "admin:clear_all_docs"
        assert datas[-1] == "admin:panel"

    def test_admin_docs_keyboard_empty(self):
        kb = keyboards.get_admin_docs_keyboard([], "uk")
        datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert "admin:clear_all_docs" not in datas
        assert datas == ["admin:panel"]

    def test_admin_back_keyboard(self):
        kb = keyboards.get_admin_back_keyboard("uk")
        datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert datas == ["admin:panel"]


class TestCmdStats:
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

    @pytest.mark.asyncio
    async def test_admin_notification_html_escaped(self):
        msg = make_message(text="<script>alert(1)</script>", user_id=777, username="hack<er>&co")
        state = make_state(data={"lang": "uk", "name": "<b>Bob</b> & Co", "phone": "+380991234567<tag>"})
        with patch("handlers.lead_form.add_lead_to_sheet", return_value=True), \
             patch("handlers.lead_form.config") as mock_cfg:
            mock_cfg.ADMIN_ID = 999999
            await lead_handler.process_request(msg, state)

        msg.bot.send_message.assert_awaited_once()
        admin_text = msg.bot.send_message.call_args.kwargs["text"]
        assert "&lt;b&gt;Bob&lt;/b&gt; &amp; Co" in admin_text
        assert "+380991234567&lt;tag&gt;" in admin_text
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in admin_text
        assert "@hack&lt;er&gt;&amp;co" in admin_text
        assert "<script>" not in admin_text

    @pytest.mark.asyncio
    async def test_lead_form_html_escaping_name_and_request(self):
        msg = make_message(text="<script>alert(1)</script> & < 500$", user_id=123)
        state = make_state(data={"lang": "uk", "name": "<b>John</b>", "phone": "+380991234567"})
        with patch("handlers.lead_form.add_lead_to_sheet", return_value=True), \
             patch("handlers.lead_form.config") as mock_cfg:
            mock_cfg.ADMIN_ID = 999999
            await lead_handler.process_request(msg, state)

        msg.bot.send_message.assert_awaited_once()
        admin_text = msg.bot.send_message.call_args.kwargs["text"]
        assert "&lt;b&gt;John&lt;/b&gt;" in admin_text
        assert "&lt;script&gt;" in admin_text
        assert "&lt; 500$" in admin_text


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


class TestAdminPanel:
    @pytest.mark.asyncio
    async def test_show_admin_panel_command_non_admin(self):
        msg = make_message(user_id=123)
        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="uk"):
            mock_cfg.ADMIN_ID = 999
            await admin_handler.show_admin_panel(msg)
        msg.answer.assert_awaited_once()
        assert t("stats_no_access", "uk") in msg.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_show_admin_panel_command_admin(self):
        msg = make_message(user_id=999)
        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="uk"):
            mock_cfg.ADMIN_ID = 999
            await admin_handler.show_admin_panel(msg)
        msg.answer.assert_awaited_once()
        assert t("admin_panel_title", "uk") in msg.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_show_admin_panel_callback_admin(self):
        cb = make_callback(data="admin:panel", user_id=999)
        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="uk"):
            mock_cfg.ADMIN_ID = 999
            await admin_handler.show_admin_panel(cb)
        cb.answer.assert_awaited_once()
        cb.message.edit_text.assert_awaited_once()
        assert t("admin_panel_title", "uk") in cb.message.edit_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_show_admin_panel_callback_non_admin(self):
        cb = make_callback(data="admin:panel", user_id=123)
        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="uk"):
            mock_cfg.ADMIN_ID = 999
            await admin_handler.show_admin_panel(cb)
        cb.answer.assert_awaited_once_with(t("stats_no_access", "uk"), show_alert=True)
        cb.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_show_admin_panel_callback_no_admin_configured(self):
        cb = make_callback(data="admin:panel", user_id=123)
        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="uk"):
            mock_cfg.ADMIN_ID = None
            await admin_handler.show_admin_panel(cb)
        cb.answer.assert_awaited_once_with(t("stats_no_access", "uk"), show_alert=True)

    @pytest.mark.asyncio
    async def test_admin_stats_callback(self):
        cb = make_callback(data="admin:stats", user_id=999)
        stats = {"total": 10, "users": 5, "today": 2}
        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="uk"), \
             patch("handlers.admin.get_dialog_stats", new_callable=AsyncMock, return_value=stats):
            mock_cfg.ADMIN_ID = 999
            await admin_handler.cb_admin_stats(cb)
        cb.answer.assert_awaited_once()
        cb.message.edit_text.assert_awaited_once()
        assert "10" in cb.message.edit_text.call_args[0][0]
        markup = cb.message.edit_text.call_args.kwargs.get("reply_markup")
        assert markup is not None
        callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        assert "admin:panel" in callbacks

    @pytest.mark.asyncio
    async def test_admin_stats_callback_non_admin(self):
        cb = make_callback(data="admin:stats", user_id=123)
        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="uk"):
            mock_cfg.ADMIN_ID = 999
            await admin_handler.cb_admin_stats(cb)
        cb.answer.assert_awaited_once_with(t("stats_no_access", "uk"), show_alert=True)
        cb.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_admin_docs_callback_empty(self):
        cb = make_callback(data="admin:docs", user_id=999)
        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="uk"), \
             patch("handlers.admin.list_documents", new_callable=AsyncMock, return_value=[]):
            mock_cfg.ADMIN_ID = 999
            await admin_handler.cb_admin_docs(cb)
        cb.answer.assert_awaited_once()
        cb.message.edit_text.assert_awaited_once()
        assert t("doc_list_empty", "uk") in cb.message.edit_text.call_args[0][0]
        markup = cb.message.edit_text.call_args.kwargs.get("reply_markup")
        assert markup is not None
        callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        assert "admin:panel" in callbacks

    @pytest.mark.asyncio
    async def test_admin_docs_callback_non_empty(self):
        cb = make_callback(data="admin:docs", user_id=999)
        sample_docs = [{"id": 1, "filename": "sample.pdf", "file_type": "pdf", "chunk_count": 3, "uploaded_at": "2026-10-22 12:00:00"}]
        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="uk"), \
             patch("handlers.admin.list_documents", new_callable=AsyncMock, return_value=sample_docs):
            mock_cfg.ADMIN_ID = 999
            await admin_handler.cb_admin_docs(cb)
        cb.answer.assert_awaited_once()
        cb.message.edit_text.assert_awaited_once()
        assert "sample.pdf" in cb.message.edit_text.call_args[0][0]
        markup = cb.message.edit_text.call_args.kwargs.get("reply_markup")
        assert markup is not None
        callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        assert "admin:del:1" in callbacks
        assert "admin:panel" in callbacks

    @pytest.mark.asyncio
    async def test_admin_docs_callback_non_admin(self):
        cb = make_callback(data="admin:docs", user_id=123)
        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="uk"):
            mock_cfg.ADMIN_ID = 999
            await admin_handler.cb_admin_docs(cb)
        cb.answer.assert_awaited_once_with(t("stats_no_access", "uk"), show_alert=True)
        cb.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_admin_upload_hint_callback(self):
        cb = make_callback(data="admin:upload_hint", user_id=999)
        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="uk"):
            mock_cfg.ADMIN_ID = 999
            await admin_handler.cb_admin_upload_hint(cb)
        cb.answer.assert_awaited_once()
        cb.message.edit_text.assert_awaited_once()
        assert t("admin_upload_hint", "uk") in cb.message.edit_text.call_args[0][0]
        markup = cb.message.edit_text.call_args.kwargs.get("reply_markup")
        assert markup is not None
        callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        assert "admin:panel" in callbacks

    @pytest.mark.asyncio
    async def test_admin_upload_hint_callback_non_admin(self):
        cb = make_callback(data="admin:upload_hint", user_id=123)
        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="uk"):
            mock_cfg.ADMIN_ID = 999
            await admin_handler.cb_admin_upload_hint(cb)
        cb.answer.assert_awaited_once_with(t("stats_no_access", "uk"), show_alert=True)
        cb.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_admin_del_doc_by_id_callback(self):
        cb = make_callback(data="admin:del:1", user_id=999)
        sample_docs = [{"id": 1, "filename": "sample.pdf", "file_type": "pdf", "chunk_count": 3, "uploaded_at": "2026-10-22"}]
        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="uk"), \
             patch("handlers.admin.list_documents", new_callable=AsyncMock, side_effect=[sample_docs, []]), \
             patch("handlers.admin.delete_document_by_id", new_callable=AsyncMock, return_value=True) as mock_del:
            mock_cfg.ADMIN_ID = 999
            await admin_handler.cb_admin_delete_doc(cb)
        mock_del.assert_awaited_once_with(1)
        cb.answer.assert_awaited_once_with(t("doc_deleted", "uk", filename="sample.pdf"))
        cb.message.edit_text.assert_awaited_once()
        assert t("doc_list_empty", "uk") in cb.message.edit_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_admin_del_doc_by_name_callback(self):
        cb = make_callback(data="admin:del:sample.pdf", user_id=999)
        sample_docs = [{"id": 1, "filename": "sample.pdf", "file_type": "pdf", "chunk_count": 3, "uploaded_at": "2026-10-22"}]
        remaining_docs = [{"id": 2, "filename": "other.docx", "file_type": "docx", "chunk_count": 2, "uploaded_at": "2026-10-22"}]
        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="uk"), \
             patch("handlers.admin.list_documents", new_callable=AsyncMock, side_effect=[sample_docs, remaining_docs]), \
             patch("handlers.admin.delete_document_by_name", new_callable=AsyncMock, return_value=True) as mock_del:
            mock_cfg.ADMIN_ID = 999
            await admin_handler.cb_admin_delete_doc(cb)
        mock_del.assert_awaited_once_with("sample.pdf")
        cb.answer.assert_awaited_once_with(t("doc_deleted", "uk", filename="sample.pdf"))
        cb.message.edit_text.assert_awaited_once()
        assert "other.docx" in cb.message.edit_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_admin_del_doc_callback_non_admin(self):
        cb = make_callback(data="admin:del:1", user_id=123)
        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="uk"):
            mock_cfg.ADMIN_ID = 999
            await admin_handler.cb_admin_delete_doc(cb)
        cb.answer.assert_awaited_once_with(t("stats_no_access", "uk"), show_alert=True)
        cb.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_admin_back_main_callback(self):
        cb = make_callback(data="admin:back_main", user_id=999)
        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="uk"):
            mock_cfg.ADMIN_ID = 999
            await admin_handler.cb_admin_back_main(cb)
        cb.answer.assert_awaited_once()
        cb.message.edit_text.assert_awaited_once()
        assert t("welcome", "uk", name="Test") in cb.message.edit_text.call_args[0][0]
        markup = cb.message.edit_text.call_args.kwargs.get("reply_markup")
        assert markup is not None
        callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        assert "admin:panel" in callbacks

    @pytest.mark.asyncio
    async def test_admin_back_main_callback_non_admin(self):
        cb = make_callback(data="admin:back_main", user_id=123)
        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="uk"):
            mock_cfg.ADMIN_ID = 999
            await admin_handler.cb_admin_back_main(cb)
        cb.answer.assert_awaited_once_with(t("stats_no_access", "uk"), show_alert=True)
        cb.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_upload_non_admin_rejected(self):
        msg = make_message(user_id=123)
        msg.document = MagicMock()
        msg.document.file_name = "test.pdf"
        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="uk"):
            mock_cfg.ADMIN_ID = 999
            await admin_handler.handle_document_upload(msg)
        msg.answer.assert_awaited_once_with(t("non_text", "uk"))

    @pytest.mark.asyncio
    async def test_admin_clear_all_docs_callback_admin(self):
        cb = make_callback(data="admin:clear_all_docs", user_id=999)
        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="uk"), \
             patch("handlers.admin.clear_all_documents", new_callable=AsyncMock, return_value=3) as mock_clear:
            mock_cfg.ADMIN_ID = 999
            await admin_handler.cb_admin_clear_all_docs(cb)
        mock_clear.assert_awaited_once()
        cb.answer.assert_awaited_once_with(t("doc_all_cleared", "uk", count=3), show_alert=True)
        cb.message.edit_text.assert_awaited_once()
        assert t("doc_list_empty", "uk") in cb.message.edit_text.call_args[0][0]
        markup = cb.message.edit_text.call_args.kwargs.get("reply_markup")
        assert markup is not None
        callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        assert "admin:panel" in callbacks

    @pytest.mark.asyncio
    async def test_admin_clear_all_docs_callback_non_admin(self):
        cb = make_callback(data="admin:clear_all_docs", user_id=123)
        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="uk"), \
             patch("handlers.admin.clear_all_documents", new_callable=AsyncMock) as mock_clear:
            mock_cfg.ADMIN_ID = 999
            await admin_handler.cb_admin_clear_all_docs(cb)
        mock_clear.assert_not_awaited()
        cb.answer.assert_awaited_once_with(t("stats_no_access", "uk"), show_alert=True)
        cb.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handle_document_upload_uses_index_lock(self):
        import io
        msg = make_message(user_id=999)
        msg.document = MagicMock()
        msg.document.file_name = "test.pdf"
        msg.document.file_size = 1024
        msg.bot.download = AsyncMock(return_value=io.BytesIO(b"dummy pdf bytes"))
        wait_msg = MagicMock()
        wait_msg.edit_text = AsyncMock()
        msg.answer = AsyncMock(return_value=wait_msg)

        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="uk"), \
             patch("handlers.admin.index_document", new_callable=AsyncMock, return_value={"is_replaced": False, "chunk_count": 5}) as mock_idx:
            mock_cfg.ADMIN_ID = 999
            await admin_handler.handle_document_upload(msg)

        mock_idx.assert_awaited_once_with("test.pdf", b"dummy pdf bytes")
        wait_msg.edit_text.assert_awaited_once()
        assert "5" in wait_msg.edit_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_admin_clear_all_docs_callback(self):
        cb_admin = make_callback(data="admin:clear_all_docs", user_id=999)
        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="uk"), \
             patch("handlers.admin.clear_all_documents", new_callable=AsyncMock, return_value=3) as mock_clear:
            mock_cfg.ADMIN_ID = 999
            await admin_handler.cb_admin_clear_all_docs(cb_admin)
        mock_clear.assert_awaited_once()
        cb_admin.answer.assert_awaited_once_with(t("doc_all_cleared", "uk", count=3), show_alert=True)
        cb_admin.message.edit_text.assert_awaited_once()
        assert t("doc_list_empty", "uk") in cb_admin.message.edit_text.call_args[0][0]

        cb_non_admin = make_callback(data="admin:clear_all_docs", user_id=123)
        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="uk"), \
             patch("handlers.admin.clear_all_documents", new_callable=AsyncMock) as mock_clear_unauth:
            mock_cfg.ADMIN_ID = 999
            await admin_handler.cb_admin_clear_all_docs(cb_non_admin)
        mock_clear_unauth.assert_not_awaited()
        cb_non_admin.answer.assert_awaited_once_with(t("stats_no_access", "uk"), show_alert=True)
        cb_non_admin.message.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_batch_document_upload_concurrent(self):
        doc_names = ["doc1.pdf", "doc2.pdf", "doc3.docx"]
        messages = []
        wait_messages = []
        for name in doc_names:
            msg = make_message(user_id=999)
            msg.document = MagicMock()
            msg.document.file_name = name
            msg.document.file_size = 1024
            wait_msg = MagicMock()
            wait_msg.edit_text = AsyncMock()
            msg.answer = AsyncMock(return_value=wait_msg)
            msg.bot.download = AsyncMock(return_value=io.BytesIO(b"content"))
            messages.append(msg)
            wait_messages.append(wait_msg)

        async def fake_index(filename, file_bytes):
            await asyncio.sleep(0.01)
            return {"is_replaced": False, "chunk_count": 3}

        with patch("handlers.admin.config") as mock_cfg, \
             patch("handlers.admin.get_user_language", new_callable=AsyncMock, return_value="uk"), \
             patch("handlers.admin.index_document", side_effect=fake_index) as mock_index:
            mock_cfg.ADMIN_ID = 999
            await asyncio.gather(*[admin_handler.handle_document_upload(m) for m in messages])

        assert mock_index.await_count == 3
        for name, wait_msg in zip(doc_names, wait_messages):
            wait_msg.edit_text.assert_awaited_once()
            assert t("doc_indexed_new", "uk", filename=name, chunks=3) in wait_msg.edit_text.call_args[0][0]

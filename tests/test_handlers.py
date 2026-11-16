import asyncio
import importlib.util
import io
import logging
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from aiogram import types as aiogram_types
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import handlers.admin as admin_handler
import handlers.lead_form as lead_handler
import keyboards
from i18n import t
from services.models import Lead


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


@pytest.fixture
def lead_db(real_db, monkeypatch):
    for name in ("create_lead", "mark_lead_sent", "mark_lead_failed"):
        monkeypatch.setattr(lead_handler, name, getattr(real_db, name))
    return real_db


@pytest.fixture
def sheets_module():
    path = os.path.join(PROJECT_ROOT, "services", "sheets.py")
    spec = importlib.util.spec_from_file_location("services.sheets.testing", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def hanging_sheets(sheets_module, monkeypatch):
    cancelled = asyncio.Event()

    async def hang(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def short_timeout(awaitable, timeout):
        assert timeout == 10.0
        return await asyncio.wait_for(awaitable, timeout=0.01)

    async_api = SimpleNamespace(
        to_thread=AsyncMock(side_effect=hang),
        wait_for=AsyncMock(side_effect=short_timeout),
    )
    monkeypatch.setattr(sheets_module, "asyncio", async_api)
    return sheets_module, async_api, cancelled


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
        assert UUID(state.update_data.call_args.kwargs["lead_uuid"])

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
    async def test_valid_request_on_failure_confirms_and_clears_state(self, lead_db):
        msg = make_message(text="I need a bot")
        state = make_state(data={"lang": "uk", "name": "Anna", "phone": "+380991234567"})
        with patch("handlers.lead_form.add_lead_to_sheet", return_value=False), \
             patch("handlers.lead_form.config") as mock_cfg:
            mock_cfg.ADMIN_ID = None
            await lead_handler.process_request(msg, state)
        state.clear.assert_awaited_once()
        assert msg.answer.call_args.args[0] == t("lead_success", "uk")
        saved = (await lead_db.get_pending_leads())[0]
        assert saved.status == "failed"
        assert saved.attempts == 1

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


    async def test_persists_pending_lead_before_sending_and_marks_sent(self, lead_db, monkeypatch):
        msg = make_message(text="A Telegram bot for my shop")
        state = make_state(data={"lang": "uk", "name": "Anna", "phone": "+380991234567"})

        async def send(name, phone, request, *, lead_uuid):
            pending = await lead_db.get_pending_leads()
            assert len(pending) == 1
            assert pending[0].status == "pending"
            assert pending[0].attempts == 0
            assert pending[0].lead_uuid == lead_uuid
            assert (name, phone, request) == (pending[0].name, pending[0].phone, pending[0].request_text)
            return True

        send_mock = AsyncMock(side_effect=send)
        monkeypatch.setattr(lead_handler, "add_lead_to_sheet", send_mock)
        monkeypatch.setattr(lead_handler.config, "ADMIN_ID", 999)
        await lead_handler.process_request(msg, state)

        async with lead_db.async_session() as session:
            saved = (await session.scalars(select(Lead))).one()
        assert saved.status == "sent"
        assert saved.attempts == 1
        assert saved.sent_at is not None
        assert saved.last_error is None
        assert await lead_db.get_pending_leads() == []
        send_mock.assert_awaited_once()
        msg.bot.send_message.assert_awaited_once()
        assert "⚠️" not in msg.bot.send_message.call_args.kwargs["text"]
        assert msg.answer.call_args.args[0] == t("lead_success", "uk")
        state.clear.assert_awaited_once()

    async def test_sheets_timeout_keeps_lead_and_notifies_admin(self, lead_db, hanging_sheets, monkeypatch):
        sheets, async_api, cancelled = hanging_sheets
        monkeypatch.setattr(lead_handler, "add_lead_to_sheet", sheets.add_lead_to_sheet)
        monkeypatch.setattr(lead_handler.config, "ADMIN_ID", 999)
        msg = make_message(text="A Telegram bot for my shop")
        state = make_state(data={"lang": "uk", "name": "Anna", "phone": "+380991234567"})

        await lead_handler.process_request(msg, state)

        saved = (await lead_db.get_pending_leads())[0]
        assert saved.status == "failed"
        assert saved.attempts == 1
        assert "TimeoutError" in saved.last_error
        assert saved.sent_at is None
        assert saved.name == "Anna"
        assert saved.phone == "+380991234567"
        assert saved.request_text == msg.text
        assert UUID(saved.lead_uuid)
        async_api.wait_for.assert_awaited_once()
        assert cancelled.is_set()
        msg.bot.send_message.assert_awaited_once()
        admin_text = msg.bot.send_message.call_args.kwargs["text"]
        assert "⚠️" in admin_text
        assert "Google Sheets" in admin_text
        assert saved.lead_uuid in admin_text
        assert msg.answer.call_args.args[0] == t("lead_success", "uk")
        state.clear.assert_awaited_once()

    @pytest.mark.parametrize("error", [OSError("Offline"), ValueError("Invalid credentials"), RuntimeError("Unavailable")])
    async def test_any_sheet_error_keeps_lead_and_confirms_acceptance(self, lead_db, monkeypatch, error):
        monkeypatch.setattr(lead_handler, "add_lead_to_sheet", AsyncMock(side_effect=error))
        monkeypatch.setattr(lead_handler.config, "ADMIN_ID", 999)
        msg = make_message(text="A Telegram bot", lang_code="en")
        state = make_state(data={"lang": "en", "name": "John", "phone": "+380991234567"})

        await lead_handler.process_request(msg, state)

        saved = (await lead_db.get_pending_leads())[0]
        assert saved.status == "failed"
        assert saved.last_error == f"{type(error).__name__}: {error}"
        assert msg.answer.call_args.args[0] == t("lead_success", "en")
        assert "⚠️" in msg.bot.send_message.call_args.kwargs["text"]
        state.clear.assert_awaited_once()

    @pytest.mark.parametrize("success", [True, False])
    async def test_admin_notification_failure_does_not_block_confirmation(self, lead_db, monkeypatch, success):
        monkeypatch.setattr(lead_handler, "add_lead_to_sheet", AsyncMock(return_value=success))
        monkeypatch.setattr(lead_handler.config, "ADMIN_ID", 999)
        msg = make_message(text="A Telegram bot")
        msg.bot.send_message.side_effect = RuntimeError("Telegram unavailable")
        state = make_state(data={"lang": "uk", "name": "Anna", "phone": "+380991234567"})

        await lead_handler.process_request(msg, state)

        async with lead_db.async_session() as session:
            saved = (await session.scalars(select(Lead))).one()
        assert saved.status == ("sent" if success else "failed")
        assert msg.answer.call_args.args[0] == t("lead_success", "uk")
        state.clear.assert_awaited_once()

    async def test_fsm_is_cleared_if_confirmation_cannot_be_sent(self, lead_db, monkeypatch):
        monkeypatch.setattr(lead_handler, "add_lead_to_sheet", AsyncMock(return_value=True))
        monkeypatch.setattr(lead_handler.config, "ADMIN_ID", 999)
        msg = make_message(text="A Telegram bot")
        msg.answer.side_effect = [None, RuntimeError("Telegram unavailable")]
        state = make_state(data={"lang": "uk", "name": "Anna", "phone": "+380991234567"})

        with pytest.raises(RuntimeError, match="Telegram unavailable"):
            await lead_handler.process_request(msg, state)

        msg.bot.send_message.assert_awaited_once()
        state.clear.assert_awaited_once()
        async with lead_db.async_session() as session:
            assert (await session.scalars(select(Lead))).one().status == "sent"

    async def test_already_sent_uuid_is_not_sent_twice(self, lead_db, monkeypatch):
        lead = await lead_db.create_lead(123, "Anna", "+380991234567", "A Telegram bot", lead_uuid="same-key")
        await lead_db.mark_lead_sent(lead.id)
        sheet_mock = AsyncMock(return_value=True)
        monkeypatch.setattr(lead_handler, "add_lead_to_sheet", sheet_mock)
        monkeypatch.setattr(lead_handler.config, "ADMIN_ID", None)
        msg = make_message(text="A Telegram bot")
        state = make_state(data={"lang": "uk", "name": "Anna", "phone": "+380991234567", "lead_uuid": "same-key"})

        await lead_handler.process_request(msg, state)

        sheet_mock.assert_not_awaited()
        async with lead_db.async_session() as session:
            assert await session.scalar(select(func.count()).select_from(Lead)) == 1
            assert (await session.get(Lead, lead.id)).attempts == 1
        state.clear.assert_awaited_once()

    async def test_local_storage_failure_does_not_claim_acceptance(self, monkeypatch):
        monkeypatch.setattr(lead_handler, "create_lead", AsyncMock(side_effect=SQLAlchemyError("Database unavailable")))
        sheet_mock = AsyncMock(return_value=True)
        monkeypatch.setattr(lead_handler, "add_lead_to_sheet", sheet_mock)
        msg = make_message(text="A Telegram bot")
        state = make_state(data={"lang": "uk", "name": "Anna", "phone": "+380991234567"})

        await lead_handler.process_request(msg, state)

        sheet_mock.assert_not_awaited()
        msg.bot.send_message.assert_not_awaited()
        assert msg.answer.call_args.args[0] == t("lead_error", "uk")
        state.clear.assert_not_awaited()


class TestSheets:
    def test_client_has_network_timeout(self, sheets_module, monkeypatch):
        client = MagicMock()
        service_account = MagicMock(return_value=client)
        monkeypatch.setattr(sheets_module.gspread, "service_account", service_account)
        assert sheets_module.get_gc() is client
        assert sheets_module.get_gc() is client
        client.set_timeout.assert_called_once_with(10.0)
        service_account.assert_called_once()

    async def test_success_keeps_row_format_and_does_not_log_personal_data(self, sheets_module, monkeypatch, caplog):
        client = MagicMock()
        monkeypatch.setattr(sheets_module, "get_gc", lambda: client)
        caplog.set_level(logging.INFO, logger=sheets_module.__name__)

        assert await sheets_module.add_lead_to_sheet(
            "Private Name", "+380991234567", "Private request", lead_uuid="lead-identifier"
        ) is True

        client.open.return_value.sheet1.append_row.assert_called_once_with(
            ["Private Name", "+380991234567", "Private request"], value_input_option="RAW"
        )
        assert "lead-identifier" in caplog.text
        assert "Private Name" not in caplog.text
        assert "+380991234567" not in caplog.text
        assert "Private request" not in caplog.text

    async def test_errors_propagate_without_logging_personal_data(self, sheets_module, monkeypatch, caplog):
        monkeypatch.setattr(sheets_module, "get_gc", MagicMock(side_effect=OSError("Private Name +380991234567")))
        caplog.set_level(logging.INFO, logger=sheets_module.__name__)

        with pytest.raises(OSError):
            await sheets_module.add_lead_to_sheet("Private Name", "+380991234567", "Request", lead_uuid="lead-identifier")

        assert "lead-identifier" in caplog.text
        assert "OSError" in caplog.text
        assert "Private Name" not in caplog.text
        assert "+380991234567" not in caplog.text

    async def test_hanging_sheet_call_times_out(self, hanging_sheets):
        sheets, async_api, cancelled = hanging_sheets
        with pytest.raises(TimeoutError):
            await sheets.add_lead_to_sheet("Test", "1234567", "Request", lead_uuid="lead-identifier")
        async_api.wait_for.assert_awaited_once()
        async_api.to_thread.assert_awaited_once_with(sheets._append_lead, "Test", "1234567", "Request")
        assert cancelled.is_set()


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

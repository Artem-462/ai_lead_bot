import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.enums import ChatType
from aiogram.fsm.storage.memory import SimpleEventIsolation
from sqlalchemy.exc import SQLAlchemyError

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import main
from handlers import admin, lead_form
from i18n import LANG_SELECT_MSG, t
from middlewares.antispam import AntiSpamMiddleware
from tests.test_handlers import make_callback, make_message, make_state


class TestMainHandlers:
    def test_routers_registration_order(self):
        assert len(main.dp.sub_routers) >= 3
        assert main.dp.sub_routers[0] == lead_form.router
        assert main.dp.sub_routers[1] == admin.router
        assert main.dp.sub_routers[2] == main.user_router

    @pytest.mark.asyncio
    async def test_cmd_start_logs_non_admin_connection(self):
        msg = make_message(text="/start", user_id=123)
        with patch("main.config.ADMIN_ID", 999), \
             patch("main.config.logger.info") as mock_log, \
             patch("main.get_user_language", new_callable=AsyncMock, return_value="uk"):
            await main.cmd_start(msg)
            mock_log.assert_called_with("User %s connected (ADMIN_ID configured as %s)", 123, 999)

    @pytest.mark.asyncio
    async def test_cmd_start_does_not_log_admin_connection(self):
        msg = make_message(text="/start", user_id=999)
        with patch("main.config.ADMIN_ID", 999), \
             patch("main.config.logger.info") as mock_log, \
             patch("main.get_user_language", new_callable=AsyncMock, return_value="uk"):
            await main.cmd_start(msg)
            assert not any("ADMIN_ID configured as" in str(call) for call in mock_log.call_args_list)

    @pytest.mark.asyncio
    async def test_cmd_start_new_user_prompts_language(self):
        msg = make_message(text="/start", user_id=123)
        with patch("main.get_user_language", new_callable=AsyncMock, return_value=None):
            await main.cmd_start(msg)
        assert msg.answer.await_count == 2
        calls = msg.answer.call_args_list
        assert "reply_markup" in calls[0].kwargs
        assert calls[1].args[0] == LANG_SELECT_MSG
        assert "reply_markup" in calls[1].kwargs

    @pytest.mark.asyncio
    async def test_cmd_start_returning_user_shows_welcome(self):
        msg = make_message(text="/start", user_id=123)
        with patch("main.get_user_language", new_callable=AsyncMock, return_value="uk"):
            await main.cmd_start(msg)
        assert msg.answer.await_count == 2
        calls = msg.answer.call_args_list
        assert "reply_markup" in calls[0].kwargs
        assert t("welcome", "uk", name="Test") in calls[1].args[0]
        assert "reply_markup" in calls[1].kwargs

    @pytest.mark.asyncio
    async def test_cmd_language_shows_language_selection(self):
        msg = make_message(text="/language")
        await main.cmd_language(msg)
        assert msg.answer.await_count == 2
        calls = msg.answer.call_args_list
        assert "reply_markup" in calls[0].kwargs
        assert calls[1].args[0] == LANG_SELECT_MSG

    @pytest.mark.asyncio
    async def test_handle_language_choice_uk(self):
        cb = make_callback(data="lang:uk", user_id=123)
        with patch("main.set_user_language", new_callable=AsyncMock) as mock_set:
            await main.handle_language_choice(cb)
            mock_set.assert_awaited_once_with(123, "uk")
        cb.answer.assert_awaited_once()
        cb.message.answer.assert_awaited_once()
        assert t("welcome", "uk", name="Test") in cb.message.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_handle_language_choice_en(self):
        cb = make_callback(data="lang:en", user_id=456)
        with patch("main.set_user_language", new_callable=AsyncMock) as mock_set:
            await main.handle_language_choice(cb)
            mock_set.assert_awaited_once_with(456, "en")
        cb.answer.assert_awaited_once()
        cb.message.answer.assert_awaited_once()
        assert t("welcome", "en", name="Test") in cb.message.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_handle_menu_ask_ai(self):
        cb = make_callback(data="menu:ask_ai", user_id=123)
        with patch("main.get_user_language", new_callable=AsyncMock, return_value="uk"):
            await main.handle_menu_ask_ai(cb)
        cb.answer.assert_awaited_once()
        cb.message.answer.assert_awaited_once()
        assert cb.message.answer.call_args[0][0] == t("ask_ai_hint", "uk")

    @pytest.mark.asyncio
    async def test_handle_menu_about(self):
        cb = make_callback(data="menu:about", user_id=123)
        with patch("main.get_user_language", new_callable=AsyncMock, return_value="uk"):
            await main.handle_menu_about(cb)
        cb.answer.assert_awaited_once()
        cb.message.answer.assert_awaited_once()
        assert t("about", "uk") in cb.message.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_handle_menu_language(self):
        cb = make_callback(data="menu:language", user_id=123)
        await main.handle_menu_language(cb)
        cb.answer.assert_awaited_once()
        cb.message.answer.assert_awaited_once()
        assert cb.message.answer.call_args[0][0] == LANG_SELECT_MSG

    @pytest.mark.asyncio
    async def test_handle_non_text(self):
        msg = make_message(text="")
        with patch("main.get_user_language", new_callable=AsyncMock, return_value="uk"):
            await main.handle_non_text(msg)
        msg.answer.assert_awaited_once()
        assert msg.answer.call_args[0][0] == t("non_text", "uk")

    @pytest.mark.asyncio
    async def test_handle_user_message_prompts_lang_if_unset(self):
        msg = make_message(text="Hello", user_id=123)
        with patch("main.get_user_language", new_callable=AsyncMock, return_value=None):
            await main.handle_user_message(msg)
        msg.answer.assert_awaited_once()
        assert msg.answer.call_args[0][0] == LANG_SELECT_MSG

    @pytest.mark.asyncio
    async def test_handle_user_message_answers_with_ai(self):
        msg = make_message(text="Hello AI", user_id=123)
        wait_msg = MagicMock()
        wait_msg.edit_text = AsyncMock()
        msg.answer = AsyncMock(return_value=wait_msg)

        with patch("main.get_user_language", new_callable=AsyncMock, return_value="uk"), \
             patch("main.count_user_messages_today", new_callable=AsyncMock, return_value=5), \
             patch("main.get_ai_answer", new_callable=AsyncMock, return_value="ШІ відповідь") as mock_ai, \
             patch("main.log_dialog", new_callable=AsyncMock) as mock_log:
            await main.handle_user_message(msg)
            mock_ai.assert_awaited_once_with(123, "Hello AI", "uk")
            wait_msg.edit_text.assert_awaited_once_with("ШІ відповідь", parse_mode=None)
            mock_log.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_daily_quota_reached_blocks_ai_and_offers_lead_form(self):
        msg = make_message(text="Need more answers", user_id=123)
        with patch("main.get_user_language", new_callable=AsyncMock, return_value="uk"), \
             patch("main.count_user_messages_today", new_callable=AsyncMock, return_value=30), \
             patch("main.config.DAILY_USER_MESSAGE_LIMIT", 30), \
             patch("main.config.ADMIN_ID", 999), \
             patch("main.get_ai_answer", new_callable=AsyncMock) as mock_ai:
            await main.handle_user_message(msg)

        mock_ai.assert_not_awaited()
        msg.answer.assert_awaited_once()
        text_arg = msg.answer.call_args[0][0]
        assert text_arg == t("daily_limit_reached", "uk")
        markup = msg.answer.call_args.kwargs.get("reply_markup")
        assert markup is not None
        btn = markup.inline_keyboard[0][0]
        assert btn.callback_data == "menu:lead"
        assert btn.text == t("ibtn_lead", "uk")

    @pytest.mark.asyncio
    async def test_daily_quota_admin_bypasses_limit(self):
        msg = make_message(text="Admin question", user_id=999)
        wait_msg = MagicMock()
        wait_msg.edit_text = AsyncMock()
        msg.answer = AsyncMock(return_value=wait_msg)

        with patch("main.get_user_language", new_callable=AsyncMock, return_value="en"), \
             patch("main.config.ADMIN_ID", 999), \
             patch("main.config.DAILY_USER_MESSAGE_LIMIT", 10), \
             patch("main.count_user_messages_today", new_callable=AsyncMock, return_value=50) as mock_count, \
             patch("main.get_ai_answer", new_callable=AsyncMock, return_value="Admin reply") as mock_ai:
            await main.handle_user_message(msg)

        mock_count.assert_not_awaited()
        mock_ai.assert_awaited_once_with(999, "Admin question", "en")
        wait_msg.edit_text.assert_awaited_once_with("Admin reply", parse_mode=None)

    @pytest.mark.asyncio
    async def test_daily_quota_disabled_when_zero(self):
        msg = make_message(text="Question with zero limit", user_id=123)
        wait_msg = MagicMock()
        wait_msg.edit_text = AsyncMock()
        msg.answer = AsyncMock(return_value=wait_msg)

        with patch("main.get_user_language", new_callable=AsyncMock, return_value="uk"), \
             patch("main.config.ADMIN_ID", 999), \
             patch("main.config.DAILY_USER_MESSAGE_LIMIT", 0), \
             patch("main.count_user_messages_today", new_callable=AsyncMock, return_value=100) as mock_count, \
             patch("main.get_ai_answer", new_callable=AsyncMock, return_value="Reply") as mock_ai:
            await main.handle_user_message(msg)

        mock_count.assert_not_awaited()
        mock_ai.assert_awaited_once_with(123, "Question with zero limit", "uk")

    async def test_log_dialog_database_error_is_reported(self):
        msg = make_message(text="Hello", user_id=123)
        wait_msg = MagicMock()
        wait_msg.edit_text = AsyncMock()
        msg.answer = AsyncMock(return_value=wait_msg)
        with patch("main.get_user_language", new_callable=AsyncMock, return_value="uk"), \
             patch("main.get_ai_answer", new_callable=AsyncMock, return_value="Answer"), \
             patch("main.log_dialog", new_callable=AsyncMock, side_effect=SQLAlchemyError("Database unavailable")):
            await main.handle_user_message(msg)
        assert wait_msg.edit_text.call_args.args[0] == t("server_error", "uk")

    @pytest.mark.asyncio
    async def test_antispam_admin_bypass_multiple_messages(self):
        middleware = AntiSpamMiddleware(limit_seconds=2)
        handler = AsyncMock(return_value="ok")
        msg = make_message(user_id=999, lang_code="uk")

        with patch("middlewares.antispam.config.ADMIN_ID", 999):
            for _ in range(3):
                res = await middleware(handler, msg, {})
                assert res == "ok"

        assert handler.await_count == 3
        msg.answer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_antispam_regular_user_throttled(self):
        middleware = AntiSpamMiddleware(limit_seconds=2)
        handler = AsyncMock(return_value="ok")
        msg = make_message(user_id=123, lang_code="uk")

        with patch("middlewares.antispam.config.ADMIN_ID", 999):
            first_res = await middleware(handler, msg, {})
            assert first_res == "ok"

            second_res = await middleware(handler, msg, {})
            assert second_res is None

        assert handler.await_count == 1
        msg.answer.assert_awaited_once_with(t("antispam_warn", "uk"))

    @pytest.mark.asyncio
    async def test_cmd_start_escapes_html_first_name(self):
        msg = make_message(text="/start", user_id=123)
        msg.from_user.first_name = "<b>Alice</b> & <script>"
        with patch("main.get_user_language", new_callable=AsyncMock, return_value="uk"):
            await main.cmd_start(msg)
        welcome_text = msg.answer.call_args_list[1].args[0]
        assert "&lt;b&gt;Alice&lt;/b&gt; &amp; &lt;script&gt;" in welcome_text
        assert "<script>" not in welcome_text

    @pytest.mark.asyncio
    async def test_handle_language_choice_escapes_html_first_name(self):
        cb = make_callback(data="lang:en", user_id=456)
        cb.from_user.first_name = "<Bob> & Co"
        with patch("main.set_user_language", new_callable=AsyncMock):
            await main.handle_language_choice(cb)
        welcome_text = cb.message.answer.call_args[0][0]
        assert "&lt;Bob&gt; &amp; Co" in welcome_text
        assert "<Bob>" not in welcome_text

    @pytest.mark.asyncio
    async def test_cmd_start_clears_active_fsm_state(self):
        msg = make_message(text="/start", user_id=123)
        state = make_state(current="LeadForm:waiting_for_name")
        with patch("main.get_user_language", new_callable=AsyncMock, return_value="uk"):
            await main.cmd_start(msg, state)
        state.clear.assert_awaited_once()
        assert msg.answer.await_count == 2

    @pytest.mark.asyncio
    async def test_cmd_help_clears_active_fsm_state(self):
        msg = make_message(text="/help", user_id=123)
        state = make_state(current="LeadForm:waiting_for_phone")
        with patch("main.get_user_language", new_callable=AsyncMock, return_value="uk"):
            await main.cmd_help(msg, state)
        state.clear.assert_awaited_once()
        assert msg.answer.call_args[0][0] == t("help", "uk")

    @pytest.mark.asyncio
    async def test_cmd_language_clears_active_fsm_state(self):
        msg = make_message(text="/language", user_id=123)
        state = make_state(current="LeadForm:waiting_for_request")
        await main.cmd_language(msg, state)
        state.clear.assert_awaited_once()
        assert msg.answer.call_args_list[1].args[0] == LANG_SELECT_MSG

    @pytest.mark.asyncio
    async def test_handle_user_message_catches_unexpected_ai_exception(self):
        msg = make_message(text="Question", user_id=123)
        wait_msg = MagicMock()
        wait_msg.edit_text = AsyncMock()
        wait_msg.delete = AsyncMock()
        msg.answer = AsyncMock(return_value=wait_msg)

        with patch("main.get_user_language", new_callable=AsyncMock, return_value="uk"), \
             patch("main.get_ai_answer", new_callable=AsyncMock, side_effect=Exception("Embedding API down")):
            await main.handle_user_message(msg)

        wait_msg.edit_text.assert_awaited_once_with(t("server_error", "uk"))

    @pytest.mark.asyncio
    async def test_handle_user_message_deletes_wait_msg_if_error_edit_fails(self):
        msg = make_message(text="Question", user_id=123)
        wait_msg = MagicMock()
        wait_msg.edit_text = AsyncMock(side_effect=RuntimeError("cannot edit"))
        wait_msg.delete = AsyncMock()
        msg.answer = AsyncMock(side_effect=[wait_msg, None])

        with patch("main.get_user_language", new_callable=AsyncMock, return_value="uk"), \
             patch("main.get_ai_answer", new_callable=AsyncMock, side_effect=Exception("LLM failure")):
            await main.handle_user_message(msg)

        wait_msg.delete.assert_awaited_once()
        assert msg.answer.await_count == 2
        assert msg.answer.call_args_list[1].args[0] == t("server_error", "uk")

    @pytest.mark.asyncio
    async def test_global_error_handler_registered_and_logs_traceback(self):
        registered = [func for func, _, _ in main.dp.errors.handlers]
        assert main.global_error_handler in registered

        err = RuntimeError("unexpected boom")
        event = main.types.ErrorEvent(update=MagicMock(update_id=42), exception=err)
        with patch("main.config.logger.error") as mock_error:
            handled = await main.global_error_handler(event)

        assert handled is True
        mock_error.assert_called_once()
        assert mock_error.call_args.kwargs["exc_info"] is err

    def test_dispatcher_uses_simple_event_isolation(self):
        assert isinstance(main.dp.fsm.events_isolation, SimpleEventIsolation)
        assert isinstance(main.dp.events_isolation, SimpleEventIsolation)

    def test_dispatcher_uses_sqlalchemy_storage(self):
        from services.fsm_storage import SQLAlchemyStorage
        assert isinstance(main.dp.storage, SQLAlchemyStorage)

    @pytest.mark.asyncio
    async def test_main_preserves_pending_updates_on_startup(self):
        with patch("main.init_db", new_callable=AsyncMock) as mock_init_db, \
             patch("main.OutboxWorker") as mock_worker_cls, \
             patch.object(main.bot, "delete_webhook", new_callable=AsyncMock) as mock_delete_webhook, \
             patch.object(main.dp, "start_polling", new_callable=AsyncMock) as mock_start_polling:
            mock_worker = mock_worker_cls.return_value
            mock_worker.stop = AsyncMock()
            await main.main()

        mock_init_db.assert_awaited_once()
        mock_worker.start.assert_called_once()
        mock_worker.stop.assert_awaited_once()
        mock_delete_webhook.assert_awaited_once_with(drop_pending_updates=False)
        mock_start_polling.assert_awaited_once_with(main.bot)

    @pytest.mark.parametrize("lang", ["uk", "en"])
    @pytest.mark.asyncio
    async def test_cmd_clear_clears_history_and_fsm_state(self, lang):
        msg = make_message(text="/clear", user_id=123, lang_code=lang)
        state = make_state(current="LeadForm:waiting_for_name")
        with (
            patch("main.clear_user_history", new_callable=AsyncMock, return_value=4) as mock_clear_db,
            patch("main.clear_history") as mock_clear_mem,
            patch("main.get_user_language", new_callable=AsyncMock, return_value=lang),
        ):
            await main.cmd_clear(msg, state)

        state.clear.assert_awaited_once()
        mock_clear_db.assert_awaited_once_with(123)
        mock_clear_mem.assert_called_once_with(123)
        msg.answer.assert_awaited_once()
        assert msg.answer.call_args.args[0] == t("history_cleared", lang)
        assert "reply_markup" in msg.answer.call_args.kwargs

    @pytest.mark.parametrize("lang", ["uk", "en"])
    def test_help_text_mentions_clear_command(self, lang):
        assert "/clear" in t("help", lang)

    @pytest.mark.parametrize("router", [main.user_router, lead_form.router, admin.router])
    def test_routers_filter_to_private_chats_only(self, router):
        assert router.message.filters
        assert router.callback_query.filters

        private_msg = make_message(text="hello")
        private_msg.chat = MagicMock(type=ChatType.PRIVATE)
        assert all(flt.resolve(private_msg) for flt in router.message.filters)

        for non_private in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
            group_msg = make_message(text="hello")
            group_msg.chat = MagicMock(type=non_private)
            assert not all(flt.resolve(group_msg) for flt in router.message.filters)

            group_cb = make_callback(data="menu:lead")
            group_cb.message.chat = MagicMock(type=non_private)
            assert not all(flt.resolve(group_cb) for flt in router.callback_query.filters)

        private_cb = make_callback(data="menu:lead")
        private_cb.message.chat = MagicMock(type=ChatType.PRIVATE)
        assert all(flt.resolve(private_cb) for flt in router.callback_query.filters)

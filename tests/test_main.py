import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import main
from handlers import admin, lead_form
from i18n import LANG_SELECT_MSG, t
from middlewares.antispam import AntiSpamMiddleware
from tests.test_handlers import make_callback, make_message


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
             patch("main.get_ai_answer", new_callable=AsyncMock, return_value="ШІ відповідь") as mock_ai, \
             patch("main.log_dialog", new_callable=AsyncMock) as mock_log:
            await main.handle_user_message(msg)
            mock_ai.assert_awaited_once_with(123, "Hello AI", "uk")
            wait_msg.edit_text.assert_awaited_once_with("ШІ відповідь", parse_mode=None)
            mock_log.assert_awaited_once()

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

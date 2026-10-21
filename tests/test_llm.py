import importlib.util
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from google.genai.errors import APIError

# Load the REAL services/llm.py directly from disk,
# bypassing the stub that conftest put in sys.modules.
_llm_path = os.path.join(PROJECT_ROOT, "services", "llm.py")
_spec = importlib.util.spec_from_file_location("services.llm.real", _llm_path)
llm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(llm)

from i18n import t


class TestErrorDetection:
    def test_api_error_503(self):
        err = APIError(message="Service Unavailable", code=503)
        assert llm._is_unavailable_error(err) is True

    def test_api_error_429(self):
        err = APIError(message="Too Many Requests", code=429)
        assert llm._is_unavailable_error(err) is True

    def test_api_error_400(self):
        err = APIError(message="Bad Request", code=400)
        assert llm._is_unavailable_error(err) is False

    def test_string_marker_unavailable(self):
        err = Exception("503 UNAVAILABLE: High Demand")
        assert llm._is_unavailable_error(err) is True

    def test_string_marker_resource_exhausted(self):
        err = Exception("RESOURCE_EXHAUSTED: quota exceeded")
        assert llm._is_unavailable_error(err) is True

    def test_unrelated_exception(self):
        err = ValueError("Invalid token configuration")
        assert llm._is_unavailable_error(err) is False

    def test_api_error_404_model_not_found(self):
        err = APIError(message="Model not found", code=404)
        assert llm._is_model_not_found_error(err) is True

    def test_string_marker_not_found(self):
        err = Exception("models/gemini-3.8-flash is NOT_FOUND")
        assert llm._is_model_not_found_error(err) is True

    def test_string_marker_404(self):
        err = Exception("HTTP 404: Not Found")
        assert llm._is_model_not_found_error(err) is True

    def test_unrelated_error_not_model_not_found(self):
        err = APIError(message="Service Unavailable", code=503)
        assert llm._is_model_not_found_error(err) is False


class TestMarkdownCleanup:
    def test_clean_markdown_removes_headers(self):
        raw = "### Важливий заголовок\n## Підзаголовок\nТекст"
        cleaned = llm._clean_markdown(raw)
        assert "###" not in cleaned
        assert "##" not in cleaned
        assert "Важливий заголовок" in cleaned
        assert "Підзаголовок" in cleaned

    def test_clean_markdown_removes_bold_asterisks(self):
        raw = "Це **дуже** важлива **інформація**."
        cleaned = llm._clean_markdown(raw)
        assert "**" not in cleaned
        assert cleaned == "Це дуже важлива інформація."

    def test_clean_markdown_preserves_clean_text(self):
        clean = "📌 Пункт 1\n🔹 Пункт 2"
        assert llm._clean_markdown(clean) == clean

    def test_system_prompt_contains_formatting_rule(self):
        prompt = llm._build_system_instruction("база")
        assert "СУВОРЕ ПРАВИЛО ФОРМАТУВАННЯ" in prompt
        assert "Не використовуй Markdown-теги заголовків ('###', '##')" in prompt
        assert "виділення жирним через подвійні зірочки ('**')" in prompt

    def test_system_prompt_contains_conciseness_and_a2_b1_rules(self):
        prompt = llm._build_system_instruction("база")
        assert "лаконічн" in prompt
        assert "A2-B1" in prompt


class TestGetAiAnswer:
    @pytest.fixture(autouse=True)
    def clean_sessions(self):
        llm._sessions.clear()
        yield
        llm._sessions.clear()

    @pytest.mark.asyncio
    async def test_message_too_long(self):
        long_text = "x" * 1001
        result = await llm.get_ai_answer(123, long_text, "uk")
        assert result == t("llm_too_long", "uk")

    @pytest.mark.asyncio
    async def test_success_on_first_try(self):
        with patch.object(llm, "_call_model", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = "Hello, I can help you build a bot."
            ans = await llm.get_ai_answer(123, "Tell me about bots", "en")
            assert ans == "Hello, I can help you build a bot."
            mock_call.assert_awaited_once()
            # Verify primary model was used
            assert mock_call.call_args[0][0] == "gemini-3.8-flash"

    @pytest.mark.asyncio
    async def test_retry_success_on_second_attempt(self):
        err_503 = APIError(message="503 UNAVAILABLE High Demand", code=503)
        with patch.object(llm, "_call_model", new_callable=AsyncMock) as mock_call, \
             patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_call.side_effect = [err_503, "Response after retry"]
            ans = await llm.get_ai_answer(123, "Need bot", "uk")
            assert ans == "Response after retry"
            assert mock_call.await_count == 2
            # 1st model: attempt 1 failed, 1 retry after 1.5s
            mock_sleep.assert_awaited_once_with(1.5)
            assert mock_call.call_args_list[0][0][0] == "gemini-3.8-flash"
            assert mock_call.call_args_list[1][0][0] == "gemini-3.8-flash"

    @pytest.mark.asyncio
    async def test_fallback_to_second_model(self):
        err_503 = APIError(message="503 UNAVAILABLE", code=503)
        with patch.object(llm, "_call_model", new_callable=AsyncMock) as mock_call, \
             patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            # Model 1 fails 2 times, Model 2 succeeds on first attempt
            mock_call.side_effect = [err_503, err_503, "Success from 2nd model"]
            ans = await llm.get_ai_answer(123, "Need bot", "uk")
            assert ans == "Success from 2nd model"
            assert mock_call.await_count == 3
            mock_sleep.assert_awaited_once_with(1.5)
            # Verify model sequence: 2 calls to 3.8-flash, 1 call to 2.5-flash
            assert mock_call.call_args_list[0][0][0] == "gemini-3.8-flash"
            assert mock_call.call_args_list[1][0][0] == "gemini-3.8-flash"
            assert mock_call.call_args_list[2][0][0] == "gemini-2.5-flash"

    @pytest.mark.asyncio
    async def test_fallback_to_third_model(self):
        err_429 = APIError(message="429 Too Many Requests", code=429)
        with patch.object(llm, "_call_model", new_callable=AsyncMock) as mock_call, \
             patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            # Model 1 fails 2 times, Model 2 fails 2 times, Model 3 succeeds
            mock_call.side_effect = [err_429, err_429, err_429, err_429, "Success from 3rd model"]
            ans = await llm.get_ai_answer(123, "Need bot", "uk")
            assert ans == "Success from 3rd model"
            assert mock_call.await_count == 5
            assert mock_sleep.await_count == 2
            # 5th call was made to the 3rd model in the cascade
            assert mock_call.call_args_list[4][0][0] == "gemini-flash-latest"

    @pytest.mark.asyncio
    async def test_all_models_fail_returns_timeout_message(self):
        err_503 = APIError(message="503 UNAVAILABLE", code=503)
        with patch.object(llm, "_call_model", new_callable=AsyncMock) as mock_call, \
             patch("asyncio.sleep", new_callable=AsyncMock):
            # All 3 models fail 2 times each (6 calls total)
            mock_call.side_effect = [err_503] * 6
            ans = await llm.get_ai_answer(123, "Need bot", "uk")
            assert ans == t("llm_timeout", "uk")
            assert mock_call.await_count == 6

    @pytest.mark.asyncio
    async def test_markdown_cleaned_in_response(self):
        with patch.object(llm, "_call_model", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = "### Привіт!\nЦе **гарний** бот."
            ans = await llm.get_ai_answer(123, "Привіт", "uk")
            assert "###" not in ans
            assert "**" not in ans
            assert "Привіт!\nЦе гарний бот." in ans

    @pytest.mark.asyncio
    async def test_non_retryable_error_fails_immediately(self):
        with patch.object(llm, "_call_model", new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = ValueError("Fatal API format error")
            ans = await llm.get_ai_answer(123, "Hello", "uk")
            assert ans == t("llm_error", "uk")
            mock_call.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_session_history_stored_and_cleared(self):
        with patch.object(llm, "_call_model", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = "Response 1"
            await llm.get_ai_answer(999, "Msg 1", "uk")
            assert len(llm._get_history(999)) == 2

            llm.clear_history(999)
            assert len(llm._get_history(999)) == 0

    @pytest.mark.asyncio
    async def test_model_not_found_switches_immediately_without_sleep(self):
        err_404 = APIError(message="404 NOT_FOUND", code=404)
        with patch.object(llm, "_call_model", new_callable=AsyncMock) as mock_call, \
             patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_call.side_effect = [err_404, "Success from 2nd model"]
            ans = await llm.get_ai_answer(123, "Need bot", "uk")
            assert ans == "Success from 2nd model"
            assert mock_call.await_count == 2
            mock_sleep.assert_not_awaited()
            assert mock_call.call_args_list[0][0][0] == "gemini-3.8-flash"
            assert mock_call.call_args_list[1][0][0] == "gemini-2.5-flash"

    @pytest.mark.asyncio
    async def test_model_not_found_404_fast_fallback(self):
        err_404 = APIError(message="404 NOT_FOUND: models/gemini-3.8-flash", code=404)
        with patch.object(llm, "_call_model", new_callable=AsyncMock) as mock_call, \
             patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_call.side_effect = [err_404, "Success from 2.5-flash"]
            ans = await llm.get_ai_answer(123, "Need bot", "uk")
            assert ans == "Success from 2.5-flash"
            assert mock_call.await_count == 2
            mock_sleep.assert_not_awaited()
            assert mock_call.call_args_list[0][0][0] == "gemini-3.8-flash"
            assert mock_call.call_args_list[1][0][0] == "gemini-2.5-flash"

    @pytest.mark.asyncio
    async def test_session_capacity_capped_at_500(self):
        llm._sessions.clear()
        for i in range(500):
            llm._get_history(i)
        assert len(llm._sessions) == 500
        assert 0 in llm._sessions

        llm._get_history(500)
        assert len(llm._sessions) == 500
        assert 0 not in llm._sessions
        assert 500 in llm._sessions

        llm._get_history(1)
        assert len(llm._sessions) == 500
        assert 1 in llm._sessions

    @pytest.mark.asyncio
    async def test_has_documents_fast_check_used(self):
        mock_has_docs = AsyncMock(return_value=False)
        with patch("services.db.has_documents", mock_has_docs), \
             patch.object(llm, "has_documents", mock_has_docs, create=True), \
             patch.object(llm, "_call_model", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = "Normal KB reply"
            ans = await llm.get_ai_answer(123, "Hello", "uk")
            assert ans == "Normal KB reply"
            mock_has_docs.assert_awaited_once()

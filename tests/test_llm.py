import importlib.util
import logging
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.exc import SQLAlchemyError

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from google.genai.errors import APIError

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


class TestHybridKnowledgeBase:
    @pytest.fixture(autouse=True)
    def company_context(self):
        llm._sessions.clear()
        with (
            patch.object(llm, "load_knowledge_base", return_value="Бот збирає заявки цілодобово. Базовий тариф: $100.") as load,
            patch.object(llm, "has_documents", new_callable=AsyncMock, return_value=True),
        ):
            yield load
        llm._sessions.clear()

    @pytest.mark.asyncio
    async def test_general_consultation_with_documents_and_no_relevant_chunks(self, company_context):
        question = "Чим бот корисний для бізнесу?"
        answer = "Бот збирає заявки цілодобово й допомагає не втрачати звернення клієнтів."
        with (
            patch("services.rag.search_knowledge_base", new_callable=AsyncMock, return_value=[]) as search,
            patch.object(llm, "_call_model", new_callable=AsyncMock, return_value=answer) as model,
        ):
            result = await llm.get_ai_answer(123, question, "uk")
        assert result == answer
        assert "📄" not in result
        company_context.assert_called_once_with()
        search.assert_awaited_once_with(question)
        model.assert_awaited_once()
        prompt = model.call_args.args[2]
        assert "Бот збирає заявки цілодобово" in prompt
        assert "knowledge_base.txt" in prompt
        assert "не є причиною" in prompt

    @pytest.mark.asyncio
    async def test_general_answer_does_not_cite_unused_document(self, company_context):
        chunks = [{"content": "Підтримка коштує $50.", "filename": "price.pdf", "page_number": 2}]
        answer = "Бот приймає заявки цілодобово."
        with (
            patch("services.rag.search_knowledge_base", new_callable=AsyncMock, return_value=chunks),
            patch.object(llm, "_call_model", new_callable=AsyncMock, return_value=f"BASE_ANSWER\n{answer}") as model,
        ):
            result = await llm.get_ai_answer(123, "Чим бот корисний для бізнесу?", "uk")
        assert result == answer
        assert "BASE_ANSWER" not in result
        assert "📄" not in result
        company_context.assert_called_once_with()
        assert "Підтримка коштує $50." in model.call_args.args[2]
        assert "Бот збирає заявки цілодобово" in model.call_args.args[2]

    @pytest.mark.parametrize("lang,question,answer,footer", [
        ("uk", "Яка ціна бота?", "Ціна бота — $300.", "📄 Джерело: price.pdf (стор. 2)"),
        ("en", "What is the bot price?", "The bot price is $300.", "📄 Source: price.pdf (p. 2)"),
    ])
    @pytest.mark.asyncio
    async def test_document_facts_take_priority_and_have_exact_source(self, company_context, lang, question, answer, footer):
        chunks = [{"content": "Ціна бота: $300.", "filename": "price.pdf", "page_number": 2}]
        with (
            patch("services.rag.search_knowledge_base", new_callable=AsyncMock, return_value=chunks),
            patch.object(llm, "_call_model", new_callable=AsyncMock, return_value=f"DOCUMENT_ANSWER: 1\n{answer}") as model,
        ):
            result = await llm.get_ai_answer(123, question, lang)
        assert result == f"{answer}\n\n{footer}"
        company_context.assert_called_once_with()
        prompt = model.call_args.args[2]
        assert "Базовий тариф: $100." in prompt
        assert "Ціна бота: $300." in prompt
        assert "Фрагменти документів мають пріоритет" in prompt
        assert "DOCUMENT_ANSWER" not in result

    @pytest.mark.asyncio
    async def test_only_used_retrieved_fragment_is_cited(self):
        chunks = [
            {"content": "Графік роботи", "filename": "schedule.pdf", "page_number": 1, "score": 0.95},
            {"content": "Ціна: $300", "filename": "price.pdf", "page_number": 4, "score": 0.90},
        ]
        with (
            patch("services.rag.search_knowledge_base", new_callable=AsyncMock, return_value=chunks),
            patch.object(llm, "_call_model", new_callable=AsyncMock, return_value="DOCUMENT_ANSWER: 2\nЦіна — $300."),
        ):
            result = await llm.get_ai_answer(123, "Яка ціна?", "uk")
        assert result == "Ціна — $300.\n\n📄 Джерело: price.pdf (стор. 4)"
        assert "schedule.pdf" not in result

    @pytest.mark.asyncio
    async def test_sources_from_all_used_documents_are_preserved(self):
        chunks = [
            {"content": "Ціна: $300", "filename": "price.pdf", "page_number": 2, "score": 0.96},
            {"content": "Підтримка: $50/міс.", "filename": "support.docx", "page_number": 3, "score": 0.89},
        ]
        with (
            patch("services.rag.search_knowledge_base", new_callable=AsyncMock, return_value=chunks),
            patch.object(llm, "_call_model", new_callable=AsyncMock,
                         return_value="DOCUMENT_ANSWER: 1, 2\nБот — $300, підтримка — $50/міс."),
        ):
            result = await llm.get_ai_answer(123, "Яка вартість бота та підтримки?", "uk")
        assert "📄 Джерело: price.pdf (стор. 2), support.docx (стор. 3)" in result

    @pytest.mark.asyncio
    async def test_price_from_company_context_is_available_without_document_match(self):
        with (
            patch("services.rag.search_knowledge_base", new_callable=AsyncMock, return_value=[]),
            patch.object(llm, "_call_model", new_callable=AsyncMock, return_value="Базовий тариф — $100.") as model,
        ):
            result = await llm.get_ai_answer(123, "Який базовий тариф?", "uk")
        assert result == "Базовий тариф — $100."
        assert "Базовий тариф: $100." in model.call_args.args[2]
        assert "📄" not in result

    @pytest.mark.parametrize("documents_available", [True, False])
    @pytest.mark.parametrize("lang", ["uk", "en"])
    @pytest.mark.asyncio
    async def test_unknown_specific_facts_return_honest_refusal(self, documents_available, lang):
        with (
            patch.object(llm, "has_documents", new_callable=AsyncMock, return_value=documents_available),
            patch("services.rag.search_knowledge_base", new_callable=AsyncMock, return_value=[]) as search,
            patch.object(llm, "_call_model", new_callable=AsyncMock, return_value="NO_RAG_ANSWER") as model,
        ):
            result = await llm.get_ai_answer(123, "What is the enterprise SLA penalty?", lang)
        assert result == t("rag_no_answer", lang)
        assert "NO_RAG_ANSWER" in model.call_args.args[2]
        assert "ані в документах, ані в knowledge_base.txt" in model.call_args.args[2]
        assert not llm._get_history(123)
        assert search.await_count == int(documents_available)

    @pytest.mark.asyncio
    async def test_unknown_fact_is_not_invented_from_unrelated_documents(self):
        chunks = [{"content": "Ціна бота: $300.", "filename": "price.pdf", "page_number": 2}]
        with (
            patch("services.rag.search_knowledge_base", new_callable=AsyncMock, return_value=chunks),
            patch.object(llm, "_call_model", new_callable=AsyncMock, return_value="NO_RAG_ANSWER"),
        ):
            result = await llm.get_ai_answer(123, "Який штраф передбачений корпоративним SLA?", "uk")
        assert result == t("rag_no_answer", "uk")
        assert "📄" not in result

    @pytest.mark.asyncio
    async def test_search_failure_does_not_disable_general_consultation(self, caplog):
        with (
            patch("services.rag.search_knowledge_base", new_callable=AsyncMock, side_effect=RuntimeError("Embedding failed")),
            patch.object(llm, "_call_model", new_callable=AsyncMock, return_value="Бот збирає заявки цілодобово.") as model,
        ):
            result = await llm.get_ai_answer(123, "Чим корисний бот?", "uk")
        assert result == "Бот збирає заявки цілодобово."
        assert "Бот збирає заявки цілодобово" in model.call_args.args[2]
        assert "Document search failed" in caplog.text

    @pytest.mark.parametrize("error", [SQLAlchemyError("Database unavailable"), OSError("Database unavailable")])
    @pytest.mark.asyncio
    async def test_database_failure_keeps_company_context(self, error):
        with (
            patch.object(llm, "has_documents", new_callable=AsyncMock, side_effect=error),
            patch("services.rag.search_knowledge_base", new_callable=AsyncMock) as search,
            patch.object(llm, "_call_model", new_callable=AsyncMock, return_value="Бот збирає заявки цілодобово."),
        ):
            result = await llm.get_ai_answer(123, "Чим корисний бот?", "uk")
        assert result == "Бот збирає заявки цілодобово."
        search.assert_not_awaited()

    @pytest.mark.parametrize("identifiers", ["0", "2", "price.pdf", "knowledge_base.txt", ""])
    @pytest.mark.asyncio
    async def test_invalid_document_citations_are_stripped_with_warning(self, identifiers, caplog):
        chunks = [{"content": "Ціна: $300", "filename": "price.pdf", "page_number": 2}]
        caplog.set_level(logging.WARNING)
        with (
            patch("services.rag.search_knowledge_base", new_callable=AsyncMock, return_value=chunks),
            patch.object(
                llm,
                "_call_model",
                new_callable=AsyncMock,
                return_value=f"DOCUMENT_ANSWER: {identifiers}\nЦіна — $300.",
            ),
        ):
            result = await llm.get_ai_answer(123, "Яка ціна?", "uk")
        assert result == "Ціна — $300."
        assert "DOCUMENT_ANSWER" not in result
        assert "📄" not in result
        assert "Invalid document source identifiers" in caplog.text
        assert llm._get_history(123)

    @pytest.mark.parametrize("identifiers", ["1, 99", "1, knowledge_base.txt", "0, 1"])
    @pytest.mark.asyncio
    async def test_mixed_valid_and_invalid_citations_keep_only_valid_sources(self, identifiers, caplog):
        chunks = [{"content": "Ціна: $300", "filename": "price.pdf", "page_number": 2}]
        caplog.set_level(logging.WARNING)
        with (
            patch("services.rag.search_knowledge_base", new_callable=AsyncMock, return_value=chunks),
            patch.object(
                llm,
                "_call_model",
                new_callable=AsyncMock,
                return_value=f"DOCUMENT_ANSWER: {identifiers}\nЦіна — $300.",
            ),
        ):
            result = await llm.get_ai_answer(123, "Яка ціна?", "uk")
        assert result == "Ціна — $300.\n\n📄 Джерело: price.pdf (стор. 2)"
        assert "DOCUMENT_ANSWER" not in result
        assert "Invalid document source identifiers" in caplog.text

    @pytest.mark.asyncio
    async def test_base_answer_with_suffix_returns_clean_body(self):
        chunks = [{"content": "Ціна: $300", "filename": "price.pdf", "page_number": 2}]
        with (
            patch("services.rag.search_knowledge_base", new_callable=AsyncMock, return_value=chunks),
            patch.object(
                llm,
                "_call_model",
                new_callable=AsyncMock,
                return_value="BASE_ANSWER: knowledge_base.txt\nБазовий тариф — $100.",
            ),
        ):
            result = await llm.get_ai_answer(123, "Який базовий тариф?", "uk")
        assert result == "Базовий тариф — $100."
        assert "BASE_ANSWER" not in result
        assert "📄" not in result

    @pytest.mark.asyncio
    async def test_source_marker_without_answer_returns_empty_response_message(self):
        chunks = [{"content": "Ціна: $300", "filename": "price.pdf", "page_number": 2}]
        with (
            patch("services.rag.search_knowledge_base", new_callable=AsyncMock, return_value=chunks),
            patch.object(llm, "_call_model", new_callable=AsyncMock, return_value="BASE_ANSWER\n"),
        ):
            result = await llm.get_ai_answer(123, "Яка ціна?", "uk")
        assert result == t("llm_empty", "uk")

    def test_sources_deduplicate_and_sort_page_numbers(self):
        chunks = [
            {"filename": "price.pdf", "page_number": 4},
            {"filename": "price.pdf", "page_number": 2},
            {"filename": "price.pdf", "page_number": 4},
        ]
        assert llm.format_sources(chunks, "uk") == "price.pdf (стор. 2, 4)"


class TestContextualSearchAndTokenLimits:
    @pytest.fixture(autouse=True)
    def clean_sessions(self):
        llm._sessions.clear()
        yield
        llm._sessions.clear()

    @pytest.mark.asyncio
    async def test_followup_question_builds_contextual_search_query_and_preserves_llm_input(self):
        chunks = [{"content": "Стандарт ($300): бот + CRM + підтримка", "filename": "price.pdf", "page_number": 2}]
        with (
            patch.object(llm, "load_knowledge_base", return_value="Базовий опис"),
            patch.object(llm, "has_documents", new_callable=AsyncMock, return_value=True),
            patch("services.rag.search_knowledge_base", new_callable=AsyncMock, return_value=chunks) as search,
            patch.object(llm, "_call_model", new_callable=AsyncMock) as model,
        ):
            model.side_effect = [
                "DOCUMENT_ANSWER: 1\nЄ 3 тарифи: Базовий ($100), Стандарт ($300), Преміум ($600).",
                "DOCUMENT_ANSWER: 1\nУ тариф Стандарт входять бот, CRM та підтримка.",
            ]
            await llm.get_ai_answer(42, "Які є тарифи на ботів?", "uk")
            followup = "А що входить у другий варіант?"
            await llm.get_ai_answer(42, followup, "uk")

        assert search.await_count == 2
        assert search.await_args_list[0].args == ("Які є тарифи на ботів?",)
        enriched_query = search.await_args_list[1].args[0]
        assert "Які є тарифи на ботів?" in enriched_query
        assert "Стандарт ($300)" in enriched_query
        assert followup in enriched_query
        assert "📄" not in enriched_query
        assert len(enriched_query) <= 500

        llm_contents = model.await_args_list[1].args[1]
        assert len(llm_contents) == 3
        assert llm._extract_content_text(llm_contents[-1]) == followup

    def test_contextual_search_query_caps_length_at_500_chars(self):
        history = [
            llm.genai_types.Content(role="user", parts=[llm.genai_types.Part(text="Питання " * 80)]),
            llm.genai_types.Content(role="model", parts=[llm.genai_types.Part(text="Відповідь " * 80)]),
        ]
        followup = "А другий варіант?"
        query = llm._build_search_query(followup, history)
        assert len(query) <= 500
        assert query.endswith(followup)

    @pytest.mark.asyncio
    async def test_history_is_trimmed_to_max_history_chars_keeping_recent_turns(self, monkeypatch):
        monkeypatch.setattr(llm.config, "MAX_HISTORY_CHARS", 120)
        llm._update_history(77, "Old question " + "a" * 40, "Old answer " + "b" * 40)
        llm._update_history(77, "Mid question " + "c" * 30, "Mid answer " + "d" * 30)
        llm._update_history(77, "Recent question", "Recent answer")

        with (
            patch.object(llm, "has_documents", new_callable=AsyncMock, return_value=False),
            patch.object(llm, "_call_model", new_callable=AsyncMock, return_value="Final answer") as model,
        ):
            await llm.get_ai_answer(77, "New question", "uk")

        sent_contents = model.await_args.args[1]
        sent_texts = [llm._extract_content_text(item) for item in sent_contents]
        assert sent_texts[-1] == "New question"
        prior_history = sent_contents[:-1]
        assert prior_history[0].role == "user"
        assert sum(len(llm._extract_content_text(item)) for item in prior_history) <= 120
        assert "Recent question" in sent_texts
        assert "Recent answer" in sent_texts
        assert not any(text.startswith("Old question") for text in sent_texts)

    @pytest.mark.asyncio
    async def test_call_model_passes_max_output_tokens_to_generate_content_config(self, monkeypatch):
        monkeypatch.setattr(llm.config, "MAX_OUTPUT_TOKENS", 777)
        fake_response = type("Resp", (), {"text": "Answer"})()
        mock_generate = AsyncMock(return_value=fake_response)
        with (
            patch.object(llm.client.aio.models, "generate_content", mock_generate),
            patch.object(llm.genai_types, "GenerateContentConfig", wraps=llm.genai_types.GenerateContentConfig) as mock_cfg,
        ):
            result = await llm._call_model("gemini-3.8-flash", [], "system prompt")

        assert result == "Answer"
        mock_cfg.assert_called_once()
        assert mock_cfg.call_args.kwargs["max_output_tokens"] == 777
        assert mock_cfg.call_args.kwargs["system_instruction"] == "system prompt"

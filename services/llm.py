import asyncio
import logging
import os
import re
from typing import Any

import aiosqlite
from google import genai
from google.genai import errors
from google.genai import types as genai_types

import config
from i18n import t
from services.db import has_documents

logger = logging.getLogger(__name__)
client = genai.Client(api_key=config.GEMINI_API_KEY)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB_PATH = os.path.join(BASE_DIR, "knowledge_base.txt")

_kb_cache: str = ""
_kb_mtime: float = 0.0


def load_knowledge_base() -> str:
    global _kb_cache, _kb_mtime
    try:
        current_mtime = os.path.getmtime(KB_PATH)
        if current_mtime != _kb_mtime:
            with open(KB_PATH, "r", encoding="utf-8") as f:
                _kb_cache = f.read()
            _kb_mtime = current_mtime
            logger.info("Knowledge base reloaded from disk")
        return _kb_cache
    except FileNotFoundError:
        logger.warning("Knowledge base file not found: %s", KB_PATH)
        return "База знань поки порожня."


MAX_HISTORY = 8
MAX_SESSIONS = 500
_sessions: dict[int, list[genai_types.Content]] = {}


def _get_history(user_id: int) -> list[genai_types.Content]:
    if user_id not in _sessions:
        if len(_sessions) >= MAX_SESSIONS:
            _sessions.pop(next(iter(_sessions)))
        _sessions[user_id] = []
    return _sessions[user_id]


def _update_history(
    user_id: int,
    user_text: str,
    model_reply: str,
) -> None:
    history = _get_history(user_id)
    history.append(
        genai_types.Content(role="user", parts=[genai_types.Part(text=user_text)])
    )
    history.append(
        genai_types.Content(role="model", parts=[genai_types.Part(text=model_reply)])
    )
    if len(history) > MAX_HISTORY * 2:
        _sessions[user_id] = history[-(MAX_HISTORY * 2):]


def clear_history(user_id: int) -> None:
    _sessions.pop(user_id, None)


def _build_system_instruction(knowledge_data: str) -> str:
    return f"""Ти — експертний ШІ-консультант і персональний асистент приватного розробника Артема.

ТВОЄ ЗАВДАННЯ:
Грамотно, дружелюбно і по суті консультувати потенційних клієнтів, пояснювати цінність автоматизації \
для їхнього бізнесу і допомагати сформулювати задачу для Артема.

ПРАВИЛА ТА ЕТИКА:
1. Спілкуйся жваво, професійно і впевнено, без зайвої «води».
2. Відповіді мають бути лаконічними, без зайвої «води» та зайвого тексту (якщо тільки клієнт сам явно не просить розгорнутої відповіді).
3. НІКОЛИ не проси телефон, пошту або інші контакти у чаті.
4. Для замовлення або точного розрахунку проєкту м'яко скеровуй клієнта:
   «Щоб обговорити ваш проєкт і розрахувати точну вартість, натисніть кнопку \
«📝 Залишити заявку» нижче, і Артем зв'яжеться з вами.»

СУВОРЕ ПРАВИЛО ФОРМАТУВАННЯ:
Не використовуй Markdown-теги заголовків ('###', '##') та виділення жирним через подвійні зірочки ('**'). Оформлюй пункти списків через зрозумілі емодзі (📌, 🔹, 1., 2.) та чисті абзаци з відступами. Текст має виглядати як акуратне повідомлення живої людини в Telegram.

МОВА ТА СТИЛЬ ВІДПОВІДІ:
• Відповідай тією мовою, якою пише до тебе користувач. Якщо пишуть українською — відповідай українською.
• Рівень англійської (A2-B1): при відповідях англійською мовою використовуй лексику та граматику рівня A2-B1. Уникай складних термінів, пасивних конструкцій та громіздких речень, щоб текст легко сприймався міжнародними клієнтами (якщо тільки користувач сам не просить іншого).

РОЗПОДІЛ ОБЛАСТЕЙ ЗНАНЬ:
• ЗАГАЛЬНІ ПИТАННЯ ПО IT І БІЗНЕСУ (Використовуй весь потенціал свого інтелекту):
  Якщо клієнт питає про користь ботів, сценарії для його ніші (ресторан, салон краси, нерухомість),
  різницю між сайтом і ботом, інтеграції або технології — докладно і зрозуміло пояснюй,
  наводь приклади і показуй вигоду.

• ЦІНИ, ТЕРМІНИ ТА УМОВИ АРТЕМА (СТРОГО по базі знань):
  По цінах, термінах, передоплаті та умовах підтримки спирайся ЛИШЕ на факти з блоку нижче.
  Не вигадуй знижки, гарантії або тарифи, яких там немає.
  Якщо клієнт просить те, чого немає в прайсі — запропонуй натиснути «Залишити заявку»
  для індивідуальної оцінки.

--- БАЗА ЗНАНЬ ---
{knowledge_data}"""


def _clean_markdown(text: str) -> str:
    cleaned = re.sub(r"(?m)^#{1,6}\s*", "", text)
    return cleaned.replace("**", "")


def _is_model_not_found_error(e: Exception) -> bool:
    if isinstance(e, errors.APIError) and getattr(e, "code", None) == 404:
        return True
    err_str = str(e).upper()
    return "NOT_FOUND" in err_str or "404" in err_str


def _is_unavailable_error(e: Exception) -> bool:
    if isinstance(e, errors.APIError) and getattr(e, "code", None) in (503, 429):
        return True
    err_str = str(e).upper()
    return any(marker in err_str for marker in ("503", "429", "UNAVAILABLE", "HIGH DEMAND", "OVERLOADED", "RESOURCE_EXHAUSTED"))


async def _call_model(
    model_name: str,
    contents: list[genai_types.Content],
    system_instruction: str,
    timeout: float = 20.0,
) -> str | None:
    response = await asyncio.wait_for(
        client.aio.models.generate_content(
            model=model_name,
            contents=contents,
            config=genai_types.GenerateContentConfig(
                system_instruction=system_instruction,
                automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),
            ),
        ),
        timeout=timeout,
    )
    return response.text if response and response.text else None


def format_sources(chunks: list[dict[str, Any]], lang: str) -> str:
    docs: dict[str, list[int]] = {}
    for c in chunks:
        fn = c["filename"]
        pg = c["page_number"]
        if fn not in docs:
            docs[fn] = []
        if pg not in docs[fn]:
            docs[fn].append(pg)

    page_prefix = "стор. " if lang == "uk" else "p. "
    parts = []
    for fn, pages in docs.items():
        sorted_pages = sorted(pages)
        pages_str = ", ".join(str(p) for p in sorted_pages)
        parts.append(f"{fn} ({page_prefix}{pages_str})")

    return ", ".join(parts)


def _build_rag_instruction(chunks: list[dict[str, Any]], lang: str) -> str:
    context_blocks = []
    for idx, c in enumerate(chunks, 1):
        context_blocks.append(
            f"--- Document: {c['filename']} (Page: {c['page_number']}) ---\n{c['content']}"
        )
    context_str = "\n\n".join(context_blocks)

    return f"""Ти — експертний ШІ-консультант. Твоє завдання — надати точну та лаконічну відповідь на запитання користувача СТРОГО на основі наданих нижче фрагментів документів.

СТРОГІ ПРАВИЛА ТА ОБМЕЖЕННЯ:
1. Відповідай ВИКЛЮЧНО фактами з наведених фрагментів. Не вигадуй жодної інформації від себе і не використовуй зовнішні здогадки.
2. Якщо у наданих фрагментах немає вичерпної відповіді на запитання користувача, виведи ТІЛЬКИ одне слово: NO_RAG_ANSWER
3. Не згадуй номери сторінок або назви файлів у тексті відповіді (вони будуть додані автоматично у футері).
4. Відповідай тією мовою, якою написано запитання (українською або англійською).
5. СУВОРЕ ПРАВИЛО ФОРМАТУВАННЯ: Не використовуй Markdown-теги заголовків ('###', '##') та виділення жирним через подвійні зірочки ('**').

--- НАДАНІ ФРАГМЕНТИ ДОКУМЕНТІВ ---
{context_str}"""


async def get_ai_answer(user_id: int, user_text: str, lang: str = "uk") -> str:
    if len(user_text) > 1000:
        return t("llm_too_long", lang)

    try:
        is_rag_mode = await has_documents()
    except (aiosqlite.Error, OSError):
        is_rag_mode = False

    chunks: list[dict[str, Any]] = []

    if is_rag_mode:
        from services.rag import search_knowledge_base

        chunks = await search_knowledge_base(user_text)
        if not chunks:
            return t("rag_no_answer", lang)
        system_instruction = _build_rag_instruction(chunks, lang)
    else:
        knowledge_data = load_knowledge_base()
        system_instruction = _build_system_instruction(knowledge_data)

    history = list(_get_history(user_id))
    history.append(
        genai_types.Content(role="user", parts=[genai_types.Part(text=user_text)])
    )

    models_chain = getattr(
        config,
        "GEMINI_MODELS",
        ["gemini-3.8-flash", "gemini-2.5-flash", "gemini-flash-latest"],
    )

    reply_text: str | None = None
    all_overloaded = True

    for model_name in models_chain:
        for attempt in range(2):
            if attempt == 1:
                logger.warning("Gemini %s unavailable, retrying in 1.5s...", model_name)
                await asyncio.sleep(1.5)

            try:
                reply_text = await _call_model(model_name, history, system_instruction)
                all_overloaded = False
                break
            except (errors.APIError, errors.ClientError, TimeoutError, ValueError, OSError) as e:
                if _is_model_not_found_error(e):
                    logger.warning("Model %s not found (404), switching immediately: %s", model_name, e)
                    break
                if not (_is_unavailable_error(e) or isinstance(e, TimeoutError)):
                    logger.error("LLM API error (%s): %s", model_name, e)
                    return t("llm_error", lang)
                logger.warning("Model %s overloaded (%s), attempt %d/2", model_name, e, attempt + 1)

        if reply_text:
            break

        logger.warning("Model %s failed after retry, switching to next fallback", model_name)

    if not reply_text:
        if all_overloaded:
            logger.error("All models in fallback chain exhausted or unavailable")
            return t("llm_timeout", lang)
        logger.warning("Gemini returned empty response (safety filter triggered)")
        return t("llm_empty", lang)

    if is_rag_mode and "NO_RAG_ANSWER" in reply_text.strip():
        return t("rag_no_answer", lang)

    reply_text = _clean_markdown(reply_text)

    if is_rag_mode:
        sources_str = format_sources(chunks, lang)
        reply_text = f"{reply_text}{t('rag_source_footer', lang, sources=sources_str)}"

    _update_history(user_id, user_text, reply_text)
    return reply_text
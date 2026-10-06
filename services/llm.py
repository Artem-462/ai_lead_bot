import asyncio
import logging
import os
import re

from google import genai
from google.genai import errors
from google.genai import types as genai_types

import config
from i18n import t

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
_sessions: dict[int, list[genai_types.Content]] = {}


def _get_history(user_id: int) -> list[genai_types.Content]:
    return _sessions.setdefault(user_id, [])


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

СТРОГОЕ ПРАВИЛО ФОРМАТИРОВАНИЯ:
Не используй Markdown-теги заголовков ('###', '##') и выделения жирным через двойные звездочки ('**'). Оформляй пункты списков через понятные эмодзи (📌, 🔹, 1., 2.) и чистые абзацы с отступами. Текст должен выглядеть как аккуратное сообщение живого человека в Telegram.

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


async def get_ai_answer(user_id: int, user_text: str, lang: str = "uk") -> str:
    if len(user_text) > 1000:
        return t("llm_too_long", lang)

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
            except Exception as e:
                if not (_is_unavailable_error(e) or isinstance(e, asyncio.TimeoutError)):
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

    reply_text = _clean_markdown(reply_text)
    _update_history(user_id, user_text, reply_text)
    return reply_text
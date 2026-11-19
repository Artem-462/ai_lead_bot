import os
import sys
from datetime import UTC, datetime
from typing import Any

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault("BOT_TOKEN", "123456789:ABCdefGHIjklMNOpqrsTUVwxyz1234567")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("ADMIN_ID", "999999")
os.environ.setdefault("DAILY_USER_MESSAGE_LIMIT", "30")

from aiogram import Bot, types

import services.db


@pytest.fixture(autouse=True)
async def setup_test_db(tmp_path, monkeypatch):
    test_db_path = tmp_path / "integration_test.db"
    monkeypatch.setattr(services.db, "DB_FILE", str(test_db_path))
    await services.db.init_db()
    yield
    await services.db.engine.dispose()


@pytest.fixture
def mock_bot() -> tuple[Bot, list[Any]]:
    bot = Bot(token="123456789:ABCdefGHIjklMNOpqrsTUVwxyz1234567")
    sent_requests: list[Any] = []
    counter = 100

    async def fake_make_request(b: Bot, method: Any, timeout: float | None = None) -> Any:
        nonlocal counter
        sent_requests.append(method)
        method_name = type(method).__name__
        if method_name in ("SendMessage", "EditMessageText"):
            counter += 1
            chat_id = getattr(method, "chat_id", None) or 12345
            return types.Message(
                message_id=counter,
                date=datetime.now(UTC),
                chat=types.Chat(id=chat_id, type="private"),
                text=getattr(method, "text", "") or "",
            ).as_(b)
        if method_name == "AnswerCallbackQuery":
            return True
        if method_name == "DeleteMessage":
            return True
        return True

    bot.session.make_request = fake_make_request
    return bot, sent_requests

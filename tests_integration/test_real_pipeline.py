from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
from aiogram import Dispatcher, types
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import SimpleEventIsolation
from sqlalchemy import select

import config
import main
import services.db
from handlers import lead_form
from handlers.lead_form import LeadForm
from i18n import t
from services.fsm_storage import SQLAlchemyStorage
from services.models import Lead
from services.outbox import retry_failed_leads
from services.vector_store import SQLiteNumPyVectorStore


def make_update(
    text: str,
    user_id: int = 12345,
    chat_id: int | None = None,
    first_name: str = "TestUser",
    username: str = "testuser",
    update_id: int = 1,
    message_id: int = 1,
) -> types.Update:
    if chat_id is None:
        chat_id = user_id
    user = types.User(
        id=user_id,
        is_bot=False,
        first_name=first_name,
        username=username,
        language_code="uk",
    )
    chat = types.Chat(
        id=chat_id,
        type="private",
        first_name=first_name,
        username=username,
    )
    message = types.Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=chat,
        from_user=user,
        text=text,
    )
    return types.Update(update_id=update_id, message=message)


def make_dispatcher(*routers) -> Dispatcher:
    dp = Dispatcher(storage=SQLAlchemyStorage(), events_isolation=SimpleEventIsolation())
    for r in routers:
        r._parent_router = None
        dp.include_router(r)
    return dp


@pytest.mark.asyncio
async def test_fsm_survives_dispatcher_restart(mock_bot):
    bot, _ = mock_bot
    user_id = 1001

    await services.db.set_user_language(user_id, "uk")

    dp1 = make_dispatcher(lead_form.router)

    upd_start = make_update("/lead", user_id=user_id, update_id=1, message_id=1)
    await dp1.feed_update(bot, upd_start)

    upd_name = make_update("Олексій", user_id=user_id, update_id=2, message_id=2)
    await dp1.feed_update(bot, upd_name)

    storage = SQLAlchemyStorage()
    key = StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id)
    saved_state = await storage.get_state(key)
    assert saved_state == LeadForm.waiting_for_phone.state
    saved_data = await storage.get_data(key)
    assert saved_data.get("name") == "Олексій"

    dp2 = make_dispatcher(lead_form.router)

    with patch("handlers.lead_form.add_lead_to_sheet", new_callable=AsyncMock, return_value=True):
        upd_phone = make_update("+380501234567", user_id=user_id, update_id=3, message_id=3)
        await dp2.feed_update(bot, upd_phone)

        upd_req = make_update("Потрібен бот для автомийок", user_id=user_id, update_id=4, message_id=4)
        await dp2.feed_update(bot, upd_req)

    async with services.db.async_session() as session:
        leads = (await session.scalars(select(Lead).where(Lead.user_id == user_id))).all()
        assert len(leads) == 1
        lead = leads[0]
        assert lead.name == "Олексій"
        assert lead.phone == "+380501234567"
        assert lead.request_text == "Потрібен бот для автомийок"
        assert lead.status == "sent"

    final_state = await storage.get_state(key)
    assert final_state is None


@pytest.mark.asyncio
async def test_lead_outbox_recovery(mock_bot):
    bot, _ = mock_bot
    user_id = 1002

    await services.db.set_user_language(user_id, "uk")
    dp = make_dispatcher(lead_form.router)

    with patch(
        "handlers.lead_form.add_lead_to_sheet",
        new_callable=AsyncMock,
        side_effect=RuntimeError("Sheets API unavailable"),
    ):
        upd_start = make_update("/lead", user_id=user_id, update_id=1, message_id=1)
        await dp.feed_update(bot, upd_start)

        upd_name = make_update("Марія", user_id=user_id, update_id=2, message_id=2)
        await dp.feed_update(bot, upd_name)

        upd_phone = make_update("+380671112233", user_id=user_id, update_id=3, message_id=3)
        await dp.feed_update(bot, upd_phone)

        upd_req = make_update("Консультація по інтеграції CRM", user_id=user_id, update_id=4, message_id=4)
        await dp.feed_update(bot, upd_req)

    async with services.db.async_session() as session:
        lead = (await session.scalars(select(Lead).where(Lead.user_id == user_id))).one()
        assert lead.status == "failed"
        assert lead.attempts == 1
        assert "Sheets API unavailable" in (lead.last_error or "")
        assert lead.sent_at is None

    with patch("services.sheets.add_lead_to_sheet", new_callable=AsyncMock, return_value=True) as mock_sheet:
        sent_count, _ = await retry_failed_leads()
        assert sent_count == 1
        mock_sheet.assert_awaited_once_with(
            lead.name, lead.phone, lead.request_text, lead_uuid=lead.lead_uuid
        )

    async with services.db.async_session() as session:
        refreshed_lead = (await session.scalars(select(Lead).where(Lead.user_id == user_id))).one()
        assert refreshed_lead.status == "sent"
        assert refreshed_lead.attempts == 2
        assert refreshed_lead.last_error is None
        assert refreshed_lead.sent_at is not None


@pytest.mark.asyncio
async def test_daily_user_message_limit_and_admin_bypass(mock_bot, monkeypatch):
    bot, sent_requests = mock_bot
    regular_user_id = 2001
    admin_user_id = 999999

    monkeypatch.setattr(config, "DAILY_USER_MESSAGE_LIMIT", 2)
    monkeypatch.setattr(config, "ADMIN_ID", admin_user_id)

    await services.db.set_user_language(regular_user_id, "uk")
    await services.db.set_user_language(admin_user_id, "uk")

    dp = make_dispatcher(main.user_router)

    with patch("main.get_ai_answer", new_callable=AsyncMock, return_value="Відповідь AI") as mock_ai:
        u1 = make_update("Перше питання", user_id=regular_user_id, update_id=1, message_id=1)
        await dp.feed_update(bot, u1)
        assert mock_ai.call_count == 1

        u2 = make_update("Друге питання", user_id=regular_user_id, update_id=2, message_id=2)
        await dp.feed_update(bot, u2)
        assert mock_ai.call_count == 2

        mock_ai.reset_mock()
        u3 = make_update("Третє питання", user_id=regular_user_id, update_id=3, message_id=3)
        await dp.feed_update(bot, u3)
        mock_ai.assert_not_called()

        quota_msg = t("daily_limit_reached", "uk")
        assert any(quota_msg in getattr(req, "text", "") for req in sent_requests[-2:])

        await services.db.log_dialog(admin_user_id, "admin", "q1", "a1")
        await services.db.log_dialog(admin_user_id, "admin", "q2", "a2")

        mock_ai.reset_mock()
        u_admin = make_update("Питання від адміна", user_id=admin_user_id, update_id=4, message_id=4)
        await dp.feed_update(bot, u_admin)
        mock_ai.assert_called_once()


@pytest.mark.asyncio
async def test_sqlite_numpy_vector_store_end_to_end():
    embedding = np.array([0.5] * config.EMBEDDING_DIM, dtype=np.float32)
    chunk = {
        "content": "Тарифи: стандартний пакет $300, преміум $600",
        "filename": "pricing.pdf",
        "page_number": 1,
        "chunk_index": 0,
        "embedding": embedding,
    }

    res = await services.db.upsert_document(
        "pricing.pdf",
        "pdf",
        [chunk],
        embedding_model=config.EMBEDDING_MODEL,
        embedding_dim=config.EMBEDDING_DIM,
    )
    assert res["chunk_count"] == 1

    store = SQLiteNumPyVectorStore(
        embedding_model=config.EMBEDDING_MODEL,
        embedding_dim=config.EMBEDDING_DIM,
    )
    query_vector = np.array([0.5] * config.EMBEDDING_DIM, dtype=np.float32)
    results = await store.search(query_vector, top_k=2, min_score=0.5)

    assert len(results) == 1
    assert results[0]["filename"] == "pricing.pdf"
    assert results[0]["content"] == "Тарифи: стандартний пакет $300, преміум $600"
    assert results[0]["score"] == pytest.approx(1.0, rel=1e-3)
    assert store.matrix is not None
    assert store.matrix.shape == (1, config.EMBEDDING_DIM)

    deleted = await services.db.delete_document_by_name("pricing.pdf")
    assert deleted is True

    assert store.matrix is None
    results_after = await store.search(query_vector, top_k=2, min_score=0.5)
    assert results_after == []

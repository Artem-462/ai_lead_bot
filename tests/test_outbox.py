import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from services.models import Lead
from services.outbox import OutboxWorker, retry_failed_leads


class TestOutbox:
    @pytest.mark.asyncio
    async def test_retry_failed_leads_respects_max_attempts(self, real_db, monkeypatch):
        lead1 = await real_db.create_lead(1, "User1", "111", "Req1")
        lead2 = await real_db.create_lead(2, "User2", "222", "Req2")
        lead3 = await real_db.create_lead(3, "User3", "333", "Req3")

        await real_db.mark_lead_failed(lead1.id, "Err1")  # attempts=1
        await real_db.mark_lead_failed(lead2.id, "Err2")
        await real_db.mark_lead_failed(lead2.id, "Err2")
        await real_db.mark_lead_failed(lead2.id, "Err2")
        await real_db.mark_lead_failed(lead2.id, "Err2")  # attempts=4
        for _ in range(5):
            await real_db.mark_lead_failed(lead3.id, "Err3")

        import services.outbox as outbox_mod

        monkeypatch.setattr(outbox_mod.db, "get_failed_leads", real_db.get_failed_leads)
        monkeypatch.setattr(outbox_mod.db, "mark_lead_sent", real_db.mark_lead_sent)
        monkeypatch.setattr(outbox_mod.db, "mark_lead_failed", real_db.mark_lead_failed)

        sheet_mock = AsyncMock(return_value=True)
        monkeypatch.setattr(outbox_mod.sheets, "add_lead_to_sheet", sheet_mock)

        sent, failed = await retry_failed_leads(max_attempts=5)
        assert sent == 2
        assert failed == 0
        assert sheet_mock.await_count == 2

        async with real_db.async_session() as session:
            l1 = await session.get(Lead, lead1.id)
            l2 = await session.get(Lead, lead2.id)
            l3 = await session.get(Lead, lead3.id)
        assert l1.status == "sent"
        assert l2.status == "sent"
        assert l3.status == "failed"

    @pytest.mark.asyncio
    async def test_retry_failed_leads_without_limit_retries_all(self, real_db, monkeypatch):
        lead = await real_db.create_lead(10, "User10", "1010", "Req10")
        for _ in range(10):
            await real_db.mark_lead_failed(lead.id, "Err")

        import services.outbox as outbox_mod

        monkeypatch.setattr(outbox_mod.db, "get_failed_leads", real_db.get_failed_leads)
        monkeypatch.setattr(outbox_mod.db, "mark_lead_sent", real_db.mark_lead_sent)
        monkeypatch.setattr(outbox_mod.db, "mark_lead_failed", real_db.mark_lead_failed)

        sheet_mock = AsyncMock(return_value=True)
        monkeypatch.setattr(outbox_mod.sheets, "add_lead_to_sheet", sheet_mock)

        sent, failed = await retry_failed_leads(max_attempts=None)
        assert sent == 1
        assert failed == 0

        async with real_db.async_session() as session:
            l = await session.get(Lead, lead.id)
        assert l.status == "sent"

    @pytest.mark.asyncio
    async def test_retry_failed_leads_mutual_exclusion(self, monkeypatch):
        import services.outbox as outbox_mod

        active = 0
        max_active = 0

        async def slow_get_failed_leads(*args, **kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.05)
            active -= 1
            return []

        monkeypatch.setattr(outbox_mod.db, "get_failed_leads", slow_get_failed_leads)

        await asyncio.gather(
            retry_failed_leads(),
            retry_failed_leads(),
        )

        assert max_active == 1

    @pytest.mark.asyncio
    async def test_worker_lifecycle_start_and_stop(self):
        worker = OutboxWorker(interval_sec=0.01, max_attempts=3)
        assert worker._task is None
        assert worker._running is False

        with patch("services.outbox.retry_failed_leads", new_callable=AsyncMock) as mock_retry:
            worker.start()
            assert worker._running is True
            assert worker._task is not None
            task = worker._task

            worker.start()
            assert worker._task is task

            await asyncio.sleep(0.04)
            assert mock_retry.await_count >= 1

            await worker.stop()
            assert worker._running is False
            assert worker._task is None

            await worker.stop()

    @pytest.mark.asyncio
    async def test_worker_handles_unexpected_exceptions_in_loop(self):
        worker = OutboxWorker(interval_sec=0.01, max_attempts=3)
        calls = 0

        async def faulty_retry(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("Temporary Sheets failure")
            return (0, 0)

        with patch("services.outbox.retry_failed_leads", side_effect=faulty_retry):
            worker.start()
            await asyncio.sleep(0.04)
            await worker.stop()

        assert calls >= 2

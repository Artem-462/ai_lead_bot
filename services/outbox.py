import asyncio

import config
from services import db, sheets

# Share one retry lock between the worker and admin callbacks in this process.
outbox_lock = asyncio.Lock()


async def retry_failed_leads(max_attempts: int | None = None) -> tuple[int, int]:
    async with outbox_lock:
        leads = await db.get_failed_leads(max_attempts=max_attempts)
        sent_count = 0
        failed_count = 0
        for lead in leads:
            try:
                success = await sheets.add_lead_to_sheet(
                    lead.name, lead.phone, lead.request_text, lead_uuid=lead.lead_uuid
                )
                if not success:
                    raise RuntimeError("Google Sheets did not accept the lead")
            except Exception as error:  # noqa: BLE001
                failed_count += 1
                await db.mark_lead_failed(lead.id, f"{type(error).__name__}: {error}")
                config.logger.warning(
                    "Failed lead retry unsuccessful (lead_uuid=%s, error=%s)",
                    lead.lead_uuid,
                    type(error).__name__,
                )
            else:
                sent_count += 1
                await db.mark_lead_sent(lead.id)
        return sent_count, failed_count


class OutboxWorker:
    def __init__(
        self,
        interval_sec: float | None = None,
        max_attempts: int | None = None,
    ) -> None:
        self.interval_sec = (
            interval_sec
            if interval_sec is not None
            else getattr(config, "OUTBOX_RETRY_INTERVAL_SEC", 300)
        )
        self.max_attempts = (
            max_attempts
            if max_attempts is not None
            else getattr(config, "OUTBOX_MAX_ATTEMPTS", 5)
        )
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def _run(self) -> None:
        while self._running:
            try:
                await retry_failed_leads(max_attempts=self.max_attempts)
            except Exception:  # noqa: BLE001
                config.logger.exception("Unexpected error in OutboxWorker")
            try:
                await asyncio.sleep(self.interval_sec)
            except asyncio.CancelledError:
                break

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="outbox_worker")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            finally:
                self._task = None

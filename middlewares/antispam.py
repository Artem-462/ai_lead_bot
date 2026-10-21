import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message

import config
from i18n import get_lang, t

TTL_SECONDS = 120
CLEANUP_INTERVAL_SECONDS = 300


class AntiSpamMiddleware(BaseMiddleware):
    def __init__(self, limit_seconds: int = 2):
        self.limit_seconds = limit_seconds
        self.users_time: dict[int, float] = {}
        self.warned_time: dict[int, float] = {}
        self._last_cleanup: float = time.time()

    def _cleanup(self) -> None:
        now = time.time()
        if now - self._last_cleanup < CLEANUP_INTERVAL_SECONDS:
            return
        cutoff = now - TTL_SECONDS
        self.users_time = {uid: ts for uid, ts in self.users_time.items() if ts > cutoff}
        self.warned_time = {uid: ts for uid, ts in self.warned_time.items() if ts > cutoff}
        self._last_cleanup = now

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        self._cleanup()
        user_id = event.from_user.id
        if config.ADMIN_ID and user_id == config.ADMIN_ID:
            return await handler(event, data)

        now = time.time()

        if user_id in self.users_time and (now - self.users_time[user_id]) < self.limit_seconds:
            last_warn = self.warned_time.get(user_id, 0.0)
            if now - last_warn > 30:
                lang = get_lang(event.from_user.language_code)
                await event.answer(t("antispam_warn", lang))
                self.warned_time[user_id] = now
            return

        self.users_time[user_id] = now
        return await handler(event, data)
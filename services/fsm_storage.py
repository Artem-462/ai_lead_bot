from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey
from sqlalchemy.ext.asyncio import async_sessionmaker

from services import db
from services.models import FSMState, utc_now


class SQLAlchemyStorage(BaseStorage):
    def __init__(self, session_maker: async_sessionmaker | None = None) -> None:
        self._session_maker = session_maker

    def _get_session_maker(self) -> async_sessionmaker:
        return self._session_maker if self._session_maker is not None else db.async_session

    @staticmethod
    def _build_key(key: StorageKey) -> str:
        return f"{key.bot_id}:{key.chat_id}:{key.user_id}:{key.thread_id}:{key.destiny}"

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        key_str = self._build_key(key)
        if state is None:
            state_str = None
        elif hasattr(state, "state"):
            state_str = str(state.state)
        else:
            state_str = str(state)

        sessionmaker = self._get_session_maker()
        async with sessionmaker.begin() as session:
            record = await session.get(FSMState, key_str)
            if state_str is None:
                if record is not None:
                    try:
                        data = json.loads(record.data_json) if record.data_json else {}
                    except (json.JSONDecodeError, TypeError):
                        data = {}
                    if not data:
                        await session.delete(record)
                    else:
                        record.state = None
                        record.updated_at = utc_now()
            else:
                if record is not None:
                    record.state = state_str
                    record.updated_at = utc_now()
                else:
                    session.add(
                        FSMState(
                            storage_key=key_str,
                            state=state_str,
                            data_json="{}",
                            updated_at=utc_now(),
                        )
                    )

    async def get_state(self, key: StorageKey) -> str | None:
        key_str = self._build_key(key)
        sessionmaker = self._get_session_maker()
        async with sessionmaker() as session:
            record = await session.get(FSMState, key_str)
            return record.state if record is not None else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        key_str = self._build_key(key)
        data_dict = dict(data)
        sessionmaker = self._get_session_maker()
        async with sessionmaker.begin() as session:
            record = await session.get(FSMState, key_str)
            if not data_dict:
                if record is not None:
                    if record.state is None:
                        await session.delete(record)
                    else:
                        record.data_json = "{}"
                        record.updated_at = utc_now()
            else:
                data_json = json.dumps(data_dict, ensure_ascii=False)
                if record is not None:
                    record.data_json = data_json
                    record.updated_at = utc_now()
                else:
                    session.add(
                        FSMState(
                            storage_key=key_str,
                            state=None,
                            data_json=data_json,
                            updated_at=utc_now(),
                        )
                    )

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        key_str = self._build_key(key)
        sessionmaker = self._get_session_maker()
        async with sessionmaker() as session:
            record = await session.get(FSMState, key_str)
            if record is not None and record.data_json:
                try:
                    return json.loads(record.data_json)
                except (json.JSONDecodeError, TypeError):
                    return {}
            return {}

    async def close(self) -> None:
        pass

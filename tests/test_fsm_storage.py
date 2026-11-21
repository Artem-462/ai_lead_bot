from unittest.mock import MagicMock

import pytest
from sqlalchemy import func, select

from services.fsm_storage import SQLAlchemyStorage
from services.models import FSMState


def make_key(user_id: int = 100, chat_id: int = 200, bot_id: int = 1) -> MagicMock:
    key = MagicMock()
    key.bot_id = bot_id
    key.chat_id = chat_id
    key.user_id = user_id
    key.thread_id = None
    key.destiny = "default"
    return key


class TestSQLAlchemyStorage:
    def test_build_key_format(self):
        key = make_key(user_id=123, chat_id=456, bot_id=789)
        assert SQLAlchemyStorage._build_key(key) == "789:456:123:None:default"

    @pytest.mark.asyncio
    async def test_set_and_get_state(self, real_db):
        storage = SQLAlchemyStorage(session_maker=real_db.async_session)
        key = make_key()

        assert await storage.get_state(key) is None
        await storage.set_state(key, "Form:name")
        assert await storage.get_state(key) == "Form:name"

        await storage.set_state(key, "Form:phone")
        assert await storage.get_state(key) == "Form:phone"

    @pytest.mark.asyncio
    async def test_set_and_get_data(self, real_db):
        storage = SQLAlchemyStorage(session_maker=real_db.async_session)
        key = make_key()

        assert await storage.get_data(key) == {}
        await storage.set_data(key, {"name": "Alex", "age": 30})
        assert await storage.get_data(key) == {"name": "Alex", "age": 30}

        await storage.set_data(key, {"phone": "+380000000000"})
        assert await storage.get_data(key) == {"phone": "+380000000000"}

    @pytest.mark.asyncio
    async def test_update_data(self, real_db):
        storage = SQLAlchemyStorage(session_maker=real_db.async_session)
        key = make_key()

        await storage.set_data(key, {"first": 1})
        res = await storage.update_data(key, {"second": 2})
        assert res == {"first": 1, "second": 2}
        assert await storage.get_data(key) == {"first": 1, "second": 2}

    @pytest.mark.asyncio
    async def test_cleanup_when_state_and_data_empty(self, real_db):
        storage = SQLAlchemyStorage(session_maker=real_db.async_session)
        key = make_key()

        await storage.set_state(key, "Form:state")
        await storage.set_data(key, {"field": "value"})

        async with real_db.async_session() as session:
            count = await session.scalar(select(func.count(FSMState.storage_key)))
        assert count == 1

        await storage.set_state(key, None)
        assert await storage.get_state(key) is None
        assert await storage.get_data(key) == {"field": "value"}
        async with real_db.async_session() as session:
            count = await session.scalar(select(func.count(FSMState.storage_key)))
        assert count == 1

        await storage.set_data(key, {})
        assert await storage.get_data(key) == {}
        async with real_db.async_session() as session:
            count = await session.scalar(select(func.count(FSMState.storage_key)))
        assert count == 0

    @pytest.mark.asyncio
    async def test_cleanup_reverse_order(self, real_db):
        storage = SQLAlchemyStorage(session_maker=real_db.async_session)
        key = make_key()

        await storage.set_state(key, "Form:state")
        await storage.set_data(key, {"field": "value"})

        await storage.set_data(key, {})
        assert await storage.get_state(key) == "Form:state"
        assert await storage.get_data(key) == {}
        async with real_db.async_session() as session:
            count = await session.scalar(select(func.count(FSMState.storage_key)))
        assert count == 1

        await storage.set_state(key, None)
        assert await storage.get_state(key) is None
        async with real_db.async_session() as session:
            count = await session.scalar(select(func.count(FSMState.storage_key)))
        assert count == 0

    @pytest.mark.asyncio
    async def test_cleanup_state_only(self, real_db):
        storage = SQLAlchemyStorage(session_maker=real_db.async_session)
        key = make_key()

        await storage.set_state(key, "Form:state")
        async with real_db.async_session() as session:
            assert await session.scalar(select(func.count(FSMState.storage_key))) == 1

        await storage.set_state(key, None)
        async with real_db.async_session() as session:
            assert await session.scalar(select(func.count(FSMState.storage_key))) == 0

    @pytest.mark.asyncio
    async def test_cleanup_data_only(self, real_db):
        storage = SQLAlchemyStorage(session_maker=real_db.async_session)
        key = make_key()

        await storage.set_data(key, {"a": 1})
        async with real_db.async_session() as session:
            assert await session.scalar(select(func.count(FSMState.storage_key))) == 1

        await storage.set_data(key, {})
        async with real_db.async_session() as session:
            assert await session.scalar(select(func.count(FSMState.storage_key))) == 0

    @pytest.mark.asyncio
    async def test_isolation_between_keys(self, real_db):
        storage = SQLAlchemyStorage(session_maker=real_db.async_session)
        key1 = make_key(user_id=1, chat_id=10)
        key2 = make_key(user_id=2, chat_id=20)

        await storage.set_state(key1, "State1")
        await storage.set_data(key1, {"user": 1})
        await storage.set_state(key2, "State2")
        await storage.set_data(key2, {"user": 2})

        assert await storage.get_state(key1) == "State1"
        assert await storage.get_data(key1) == {"user": 1}
        assert await storage.get_state(key2) == "State2"
        assert await storage.get_data(key2) == {"user": 2}

        await storage.set_state(key1, None)
        await storage.set_data(key1, {})
        assert await storage.get_state(key1) is None
        assert await storage.get_data(key1) == {}
        assert await storage.get_state(key2) == "State2"
        assert await storage.get_data(key2) == {"user": 2}

    @pytest.mark.asyncio
    async def test_close_storage(self, real_db):
        storage = SQLAlchemyStorage(session_maker=real_db.async_session)
        await storage.close()

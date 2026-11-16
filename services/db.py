import os
from typing import Any
from uuid import uuid4

import numpy as np
from alembic.config import Config
from sqlalchemy import Connection, case, delete, event, func, select, update
from sqlalchemy.dialects.sqlite import Insert, insert
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool, StaticPool

from alembic import command
from services.models import Document, DocumentChunk, History, Lead, User, utc_now

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_FILE = os.path.join(DATA_DIR, "chat_history.db")

_chunks_cache: list[dict[str, Any]] | None = None


def get_database_url() -> URL:
    database = DB_FILE if DB_FILE == ":memory:" else os.path.abspath(DB_FILE)
    return URL.create("sqlite+aiosqlite", database=database)


def create_db_engine(url: str | URL) -> AsyncEngine:
    url = make_url(url)
    engine = create_async_engine(
        url,
        connect_args={"timeout": 10.0} if url.get_backend_name() == "sqlite" else {},
        poolclass=StaticPool if url.database == ":memory:" else NullPool,
    )
    if url.get_backend_name() == "sqlite":

        @event.listens_for(engine.sync_engine, "connect")
        def configure_sqlite(connection: Any, _: Any) -> None:
            cursor = connection.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()
    return engine


engine = create_db_engine(get_database_url())
async_session = async_sessionmaker(engine, expire_on_commit=False)


def invalidate_chunks_cache() -> None:
    global _chunks_cache
    _chunks_cache = None


def _run_migrations(connection: Connection) -> None:
    config = Config(os.path.join(BASE_DIR, "alembic.ini"))
    config.attributes["connection"] = connection
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")


async def init_db() -> None:
    global engine, async_session
    url = get_database_url()
    if url.database != ":memory:":
        os.makedirs(os.path.dirname(url.database), exist_ok=True)
    if engine.url != url:
        await engine.dispose()
        engine = create_db_engine(url)
        async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.connect() as connection:
        await connection.run_sync(_run_migrations)
    invalidate_chunks_cache()


def _user_upsert(user_id: int, username: str | None, language: str | None = None) -> Insert:
    now = utc_now()
    changes: dict[str, Any] = {"last_active": now}
    if username is not None:
        changes["username"] = username
    if language is not None:
        changes["language"] = language
    return (
        insert(User)
        .values(
            user_id=user_id,
            username=username,
            language=language or "uk",
            first_seen=now,
            last_active=now,
        )
        .on_conflict_do_update(index_elements=[User.user_id], set_=changes)
    )


async def upsert_user(user_id: int, username: str | None = None, language: str | None = None) -> None:
    async with async_session.begin() as session:
        await session.execute(_user_upsert(user_id, username, language))


async def log_dialog(
    user_id: int, username: str | None, user_message: str, bot_response: str
) -> None:
    now = utc_now()
    async with async_session.begin() as session:
        await session.execute(_user_upsert(user_id, username))
        session.add_all(
            [
                History(user_id=user_id, role="user", content=user_message, timestamp=now),
                History(user_id=user_id, role="assistant", content=bot_response, timestamp=now),
            ]
        )


async def get_dialog_stats() -> dict[str, int]:
    today = func.date(History.timestamp, "localtime") == func.date("now", "localtime")
    statement = select(
        func.count(History.id),
        func.count(func.distinct(History.user_id)),
        func.coalesce(func.sum(case((today, 1), else_=0)), 0),
    ).where(History.role == "user")
    async with async_session() as session:
        total, users, today_count = (await session.execute(statement)).one()
    return {"total": int(total), "users": int(users), "today": int(today_count)}


get_stats = get_dialog_stats


async def set_user_language(user_id: int, language: str) -> None:
    await upsert_user(user_id, language=language)


async def get_user_language(user_id: int) -> str | None:
    async with async_session() as session:
        return await session.scalar(select(User.language).where(User.user_id == user_id))


def _embedding_bytes(embedding: Any) -> bytes:
    if isinstance(embedding, (np.ndarray, list, tuple)):
        return np.asarray(embedding, dtype=np.float32).tobytes()
    return bytes(embedding)


async def upsert_document(
    filename: str,
    file_type: str,
    chunks: list[dict[str, Any]],
    *,
    embedding_model: str = "gemini-embedding-001",
    embedding_dim: int = 3072,
) -> dict[str, Any]:
    filename = filename.strip()
    async with async_session.begin() as session:
        doc_id = await session.scalar(
            select(Document.id).where(Document.filename.collate("NOCASE") == filename)
        )
        if doc_id is not None:
            await session.execute(delete(Document).where(Document.id == doc_id))
        document = Document(
            filename=filename,
            file_type=file_type,
            chunk_count=len(chunks),
            embedding_model=embedding_model,
            embedding_dim=embedding_dim,
        )
        session.add(document)
        await session.flush()
        session.add_all(
            [
                DocumentChunk(
                    doc_id=document.id,
                    page_number=chunk["page_number"],
                    chunk_index=chunk["chunk_index"],
                    content=chunk["content"],
                    embedding=_embedding_bytes(chunk["embedding"]),
                )
                for chunk in chunks
            ]
        )
    invalidate_chunks_cache()
    return {"is_replaced": doc_id is not None, "chunk_count": len(chunks)}


async def delete_document_by_name(filename: str) -> bool:
    async with async_session.begin() as session:
        result = await session.execute(
            delete(Document).where(Document.filename.collate("NOCASE") == filename.strip())
        )
    if result.rowcount:
        invalidate_chunks_cache()
    return bool(result.rowcount)


async def delete_document_by_id(doc_id: int) -> bool:
    async with async_session.begin() as session:
        result = await session.execute(delete(Document).where(Document.id == doc_id))
    if result.rowcount:
        invalidate_chunks_cache()
    return bool(result.rowcount)


async def delete_document(filename: str | int) -> bool:
    if isinstance(filename, int):
        return await delete_document_by_id(filename)
    return await delete_document_by_name(filename)


async def clear_all_documents() -> int:
    async with async_session.begin() as session:
        result = await session.execute(delete(Document))
    invalidate_chunks_cache()
    return result.rowcount


async def list_documents() -> list[dict[str, Any]]:
    async with async_session() as session:
        documents = await session.scalars(select(Document).order_by(Document.id.desc()))
        return [
            {
                "id": document.id,
                "filename": document.filename,
                "file_type": document.file_type,
                "chunk_count": document.chunk_count,
                "uploaded_at": str(document.uploaded_at),
            }
            for document in documents
        ]


async def has_documents() -> bool:
    if _chunks_cache:
        return True
    async with async_session() as session:
        return await session.scalar(select(Document.id).limit(1)) is not None


async def get_all_chunks_with_metadata() -> list[dict[str, Any]]:
    global _chunks_cache
    if _chunks_cache is not None:
        return _chunks_cache
    statement = (
        select(
            DocumentChunk.content,
            DocumentChunk.page_number,
            DocumentChunk.chunk_index,
            DocumentChunk.embedding,
            Document.filename,
        )
        .join(Document, DocumentChunk.doc_id == Document.id)
        .order_by(DocumentChunk.doc_id, DocumentChunk.chunk_index)
    )
    async with async_session() as session:
        rows = (await session.execute(statement)).all()
    _chunks_cache = [
        {
            "content": row.content,
            "page_number": row.page_number,
            "chunk_index": row.chunk_index,
            "embedding": np.frombuffer(row.embedding, dtype=np.float32),
            "filename": row.filename,
        }
        for row in rows
    ]
    return _chunks_cache


get_all_chunks = get_all_chunks_with_metadata


async def create_lead(
    user_id: int,
    name: str,
    phone: str,
    request_text: str,
    username: str | None = None,
    lead_uuid: str | None = None,
) -> Lead:
    lead_uuid = lead_uuid or str(uuid4())
    statement = (
        insert(Lead)
        .values(
            lead_uuid=lead_uuid,
            user_id=user_id,
            username=username,
            name=name,
            phone=phone,
            request_text=request_text,
        )
        .on_conflict_do_nothing(index_elements=[Lead.lead_uuid])
    )
    async with async_session.begin() as session:
        await session.execute(statement)
        lead = (await session.scalars(select(Lead).where(Lead.lead_uuid == lead_uuid))).one()
    return lead


async def mark_lead_sent(lead_id: int) -> None:
    statement = (
        update(Lead)
        .where(Lead.id == lead_id, Lead.status != "sent")
        .values(status="sent", attempts=Lead.attempts + 1, last_error=None, sent_at=utc_now())
    )
    async with async_session.begin() as session:
        await session.execute(statement)


async def mark_lead_failed(lead_id: int, error: str) -> None:
    statement = (
        update(Lead)
        .where(Lead.id == lead_id, Lead.status != "sent")
        .values(status="failed", attempts=Lead.attempts + 1, last_error=error, sent_at=None)
    )
    async with async_session.begin() as session:
        await session.execute(statement)


async def get_pending_leads(limit: int = 20) -> list[Lead]:
    if limit <= 0:
        return []
    statement = (
        select(Lead)
        .where(Lead.status.in_(("pending", "failed")))
        .order_by(Lead.created_at, Lead.id)
        .limit(limit)
    )
    async with async_session() as session:
        return list(await session.scalars(statement))

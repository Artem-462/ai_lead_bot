import asyncio
import io
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest
from alembic.config import Config
from sqlalchemy import (
    BigInteger,
    DateTime,
    Integer,
    LargeBinary,
    String,
    Text,
    delete,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase

from alembic import command
from services.models import (
    Base,
    Document,
    DocumentChunk,
    FSMState,
    History,
    Lead,
    User,
    utc_now,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LEGACY_SCHEMA = """
    CREATE TABLE users (
        user_id INTEGER PRIMARY KEY, language TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT,
        user_message TEXT, bot_response TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT UNIQUE COLLATE NOCASE NOT NULL,
        file_type TEXT NOT NULL, chunk_count INTEGER NOT NULL DEFAULT 0,
        uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE document_chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        page_number INTEGER NOT NULL, chunk_index INTEGER NOT NULL,
        content TEXT NOT NULL, embedding BLOB NOT NULL
    );
"""


def make_chunk(content="Text 1", index=0, embedding=None):
    return {
        "page_number": 1, "chunk_index": index, "content": content,
        "embedding": [0.1, 0.2] if embedding is None else embedding,
    }


def migration_config(db_file):
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    url = f"sqlite+aiosqlite:///{Path(db_file).as_posix()}"
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    config.attributes["configure_logger"] = False
    return config


async def assert_no_schema_diff(db):
    config = migration_config(db.DB_FILE)

    def check(connection):
        config.attributes["connection"] = connection
        command.check(config)

    async with db.engine.connect() as connection:
        await connection.run_sync(check)


class TestModels:
    @pytest.mark.parametrize("model", [User, History, Document, DocumentChunk, Lead, FSMState])
    def test_declarative_mapping(self, model):
        assert issubclass(Base, DeclarativeBase)
        assert inspect(model).local_table is Base.metadata.tables[model.__tablename__]

    @pytest.mark.parametrize("column,column_type", [
        (User.user_id, BigInteger), (History.user_id, BigInteger), (Lead.user_id, BigInteger),
        (History.id, Integer), (History.content, Text), (History.timestamp, DateTime),
        (Document.filename, String), (DocumentChunk.embedding, LargeBinary),
        (FSMState.storage_key, String), (FSMState.data_json, Text), (FSMState.updated_at, DateTime),
    ])
    def test_column_types(self, column, column_type):
        assert isinstance(column.property.columns[0].type, column_type)

    def test_chunk_foreign_key_cascades(self):
        foreign_key = next(iter(DocumentChunk.__table__.c.doc_id.foreign_keys))
        assert foreign_key.target_fullname == "documents.id"
        assert foreign_key.ondelete == "CASCADE"

    async def test_defaults_and_committed_attributes(self, real_db):
        user = User(user_id=10**12)
        document = Document(filename="test.pdf", file_type="pdf", chunk_count=0)
        lead = Lead(lead_uuid="defaults", user_id=user.user_id, name="Test", phone="1234567", request_text="Request")
        history = History(user_id=user.user_id, role="user", content="Message")
        async with real_db.async_session.begin() as session:
            session.add_all([user, document, lead, history])

        assert user.language == "uk"
        assert user.username is None
        assert isinstance(user.first_seen, datetime)
        assert isinstance(user.last_active, datetime)
        assert isinstance(history.timestamp, datetime)
        assert document.embedding_model == "gemini-embedding-001"
        assert document.embedding_dim == 3072
        assert isinstance(document.uploaded_at, datetime)
        assert lead.status == "pending"
        assert lead.attempts == 0
        assert lead.last_error is None
        assert lead.sent_at is None
        assert isinstance(lead.created_at, datetime)
        assert inspect(lead).detached

    async def test_invalid_lead_status_is_rejected(self, real_db):
        with pytest.raises(IntegrityError):
            async with real_db.async_session.begin() as session:
                session.add(Lead(
                    lead_uuid="invalid", user_id=1, name="Test", phone="1234567",
                    request_text="Request", status="unknown",
                ))


class TestInitDb:
    async def test_creates_all_tables(self, db_module):
        await db_module.init_db()
        async with db_module.engine.connect() as connection:
            tables = await connection.run_sync(lambda connection: inspect(connection).get_table_names())
        assert set(Base.metadata.tables) | {"alembic_version"} <= set(tables)

    @pytest.mark.parametrize("model", [User, History, Document, DocumentChunk, Lead, FSMState])
    async def test_tables_have_expected_columns(self, real_db, model):
        async with real_db.engine.connect() as connection:
            columns = await connection.run_sync(lambda connection: inspect(connection).get_columns(model.__tablename__))
        assert {column["name"] for column in columns} == set(model.__table__.columns.keys())

    async def test_required_indexes_exist(self, real_db):
        for model in (History, DocumentChunk, Lead):
            async with real_db.engine.connect() as connection:
                indexes = await connection.run_sync(
                    lambda connection, model=model: inspect(connection).get_indexes(model.__tablename__)
                )
            assert {index["name"] for index in indexes} >= {index.name for index in model.__table__.indexes}

    async def test_calling_init_db_twice_preserves_rows(self, real_db):
        await real_db.log_dialog(1, "user", "question", "answer")
        lead = await real_db.create_lead(1, "Test", "1234567", "Request")
        await real_db.upsert_document("test.pdf", "pdf", [make_chunk()])
        await real_db.init_db()
        assert await real_db.get_stats() == {"total": 1, "users": 1, "today": 1}
        assert [row.id for row in await real_db.get_pending_leads()] == [lead.id]
        assert len(await real_db.get_all_chunks()) == 1

    async def test_new_tables_are_empty(self, real_db):
        async with real_db.async_session() as session:
            for model in (User, History, Document, DocumentChunk, Lead, FSMState):
                assert await session.scalar(select(func.count()).select_from(model)) == 0

    async def test_wal_and_foreign_keys_on_every_connection(self, real_db):
        for _ in range(2):
            async with real_db.engine.connect() as connection:
                assert await connection.scalar(text("PRAGMA journal_mode")) == "wal"
                assert await connection.scalar(text("PRAGMA foreign_keys")) == 1

    async def test_schema_matches_metadata(self, real_db):
        await assert_no_schema_diff(real_db)

    async def test_migrates_legacy_data_without_losing_chunks(self, db_module, tmp_db):
        embedding = np.array([0.1, 0.2], dtype=np.float32).tobytes()
        with closing(sqlite3.connect(tmp_db)) as connection, connection:
            connection.executescript(LEGACY_SCHEMA)
            connection.execute("INSERT INTO users VALUES (1, 'en', '2026-01-01 10:00:00', '2026-02-01 11:00:00')")
            connection.execute("""
                INSERT INTO history (user_id, username, user_message, bot_response, timestamp)
                VALUES (1, 'alice', 'Question', 'Answer', '2026-02-02 12:00:00'),
                       (2, 'bob', 'Second question', 'Second answer', '2026-02-03 12:00:00')
            """)
            connection.execute("""
                INSERT INTO documents (id, filename, file_type, chunk_count, uploaded_at)
                VALUES (7, 'Legacy.pdf', 'pdf', 1, '2026-01-01 10:00:00'),
                       (9, 'Deleted.pdf', 'pdf', 0, '2026-01-01 10:00:00')
            """)
            connection.execute("DELETE FROM documents WHERE id = 9")
            connection.execute("""
                INSERT INTO document_chunks (id, doc_id, page_number, chunk_index, content, embedding)
                VALUES (11, 7, 1, 0, 'Preserved text', ?)
            """, (embedding,))

        await db_module.init_db()
        await db_module.init_db()
        async with db_module.async_session() as session:
            user = await session.get(User, 1)
            assert user.language == "en"
            assert user.username == "alice"
            assert user.first_seen == datetime(2026, 1, 1, 10)
            assert user.last_active == datetime(2026, 2, 1, 11)
            orphan_user = await session.get(User, 2)
            assert orphan_user.username == "bob"
            assert orphan_user.language == "uk"
            history = list(await session.scalars(select(History).order_by(History.id)))
            assert [(row.role, row.content) for row in history] == [
                ("user", "Question"), ("assistant", "Answer"),
                ("user", "Second question"), ("assistant", "Second answer"),
            ]
            document = await session.get(Document, 7)
            assert document.embedding_model == "gemini-embedding-001"
            assert document.embedding_dim == 3072
            assert document.uploaded_at == datetime(2026, 1, 1, 10)
            chunk = await session.get(DocumentChunk, 11)
            assert chunk.embedding == embedding
            assert chunk.content == "Preserved text"
        assert await db_module.get_stats() == {"total": 2, "users": 2, "today": 0}
        await assert_no_schema_diff(db_module)
        lead = await db_module.create_lead(1, "Test", "1234567", "Request")
        assert lead.status == "pending"
        await db_module.upsert_document("New.pdf", "pdf", [make_chunk()])
        assert (await db_module.list_documents())[0]["id"] > 9
        async with db_module.async_session.begin() as session:
            await session.execute(delete(Document).where(Document.id == 7))
        async with db_module.async_session() as session:
            assert await session.get(DocumentChunk, 11) is None

    @pytest.mark.parametrize("existing_column,value", [("embedding_model", "custom-model"), ("embedding_dim", 1024)])
    async def test_migrates_partially_added_embedding_metadata(self, db_module, tmp_db, existing_column, value):
        column_type = "TEXT" if existing_column == "embedding_model" else "INTEGER"
        with closing(sqlite3.connect(tmp_db)) as connection, connection:
            connection.executescript(f"""
                CREATE TABLE documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT UNIQUE COLLATE NOCASE NOT NULL,
                    file_type TEXT NOT NULL, chunk_count INTEGER NOT NULL DEFAULT 0,
                    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, {existing_column} {column_type}
                );
            """)
            connection.execute(f"INSERT INTO documents (filename, file_type, {existing_column}) VALUES ('test.pdf', 'pdf', ?)", (value,))
        await db_module.init_db()
        async with db_module.async_session() as session:
            document = (await session.scalars(select(Document))).one()
        assert getattr(document, existing_column) == value
        assert document.embedding_model == (value if existing_column == "embedding_model" else "gemini-embedding-001")
        assert document.embedding_dim == (value if existing_column == "embedding_dim" else 3072)
        await assert_no_schema_diff(db_module)

    async def test_invalid_foreign_keys_roll_back_the_migration(self, db_module, tmp_db):
        with closing(sqlite3.connect(tmp_db)) as connection, connection:
            connection.executescript(LEGACY_SCHEMA)
            connection.execute("""
                INSERT INTO document_chunks (doc_id, page_number, chunk_index, content, embedding)
                VALUES (99, 1, 0, 'Orphan', X'0000803F')
            """)
        with pytest.raises(RuntimeError, match="invalid foreign keys"):
            await db_module.init_db()
        with closing(sqlite3.connect(tmp_db)) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(documents)")}
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            assert "embedding_model" not in columns
            assert "leads" not in tables
            assert "alembic_version" not in tables
            assert not any(name.startswith("_alembic_tmp_") for name in tables)
            assert connection.execute("SELECT content FROM document_chunks").fetchone()[0] == "Orphan"
        async with db_module.engine.connect() as connection:
            assert await connection.scalar(text("PRAGMA foreign_keys")) == 1

    def test_standalone_async_upgrade_and_downgrade(self, tmp_db):
        config = migration_config(tmp_db)
        command.upgrade(config, "head")
        command.check(config)
        with closing(sqlite3.connect(tmp_db)) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0002_fsm_states"
        command.downgrade(config, "base")
        with closing(sqlite3.connect(tmp_db)) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert not set(Base.metadata.tables) & tables

    def test_offline_upgrade_can_be_generated(self, tmp_db):
        config = migration_config(tmp_db)
        config.output_buffer = io.StringIO()
        command.upgrade(config, "head", sql=True)
        sql = config.output_buffer.getvalue()
        assert "CREATE TABLE leads" in sql
        assert "CREATE TABLE fsm_states" in sql
        assert "ON DELETE CASCADE" in sql


class TestLogDialog:
    async def test_inserts_user_and_assistant_messages(self, real_db):
        await real_db.log_dialog(111, "alice", "hello", "hi there")
        async with real_db.async_session() as session:
            rows = list(await session.scalars(select(History).order_by(History.id)))
            user = await session.get(User, 111)
        assert len(rows) == 2
        assert [(row.user_id, row.role, row.content) for row in rows] == [
            (111, "user", "hello"), (111, "assistant", "hi there"),
        ]
        assert rows[0].timestamp == rows[1].timestamp
        assert user.username == "alice"

    async def test_multiple_inserts(self, real_db):
        for i in range(5):
            await real_db.log_dialog(i, f"user{i}", f"msg{i}", f"resp{i}")
        async with real_db.async_session() as session:
            assert await session.scalar(select(func.count()).select_from(History)) == 10
        assert (await real_db.get_stats())["total"] == 5

    async def test_none_username_accepted(self, real_db):
        await real_db.log_dialog(333, None, "hi", "hello")
        async with real_db.async_session() as session:
            user = await session.get(User, 333)
        assert user.username is None

    async def test_preserves_selected_language(self, real_db):
        await real_db.set_user_language(1, "en")
        await real_db.log_dialog(1, "alice", "hello", "answer")
        assert await real_db.get_user_language(1) == "en"

    async def test_clear_user_history_removes_only_target_user_rows(self, real_db):
        await real_db.log_dialog(1, "alice", "q1", "a1")
        await real_db.log_dialog(1, "alice", "q2", "a2")
        await real_db.log_dialog(2, "bob", "q3", "a3")

        deleted = await real_db.clear_user_history(1)
        assert deleted == 4
        assert await real_db.clear_user_history(1) == 0

        async with real_db.async_session() as session:
            remaining = list(await session.scalars(select(History).order_by(History.id)))
        assert [(row.user_id, row.role, row.content) for row in remaining] == [
            (2, "user", "q3"),
            (2, "assistant", "a3"),
        ]


class TestGetDialogStats:
    async def test_empty_db_all_zeros(self, real_db):
        assert await real_db.get_dialog_stats() == {"total": 0, "users": 0, "today": 0}

    async def test_all_values_are_non_negative_ints(self, real_db):
        stats = await real_db.get_stats()
        assert set(stats) == {"total", "users", "today"}
        assert all(isinstance(value, int) and value >= 0 for value in stats.values())

    async def test_stats_count_dialogs_not_messages(self, real_db):
        await real_db.log_dialog(1, "u1", "m1", "r1")
        await real_db.log_dialog(1, "u1", "m2", "r2")
        await real_db.log_dialog(2, "u2", "m3", "r3")
        assert await real_db.get_dialog_stats() == {"total": 3, "users": 2, "today": 3}

    async def test_today_count_increments_with_each_dialog(self, real_db):
        for i in range(4):
            await real_db.log_dialog(i, f"u{i}", "msg", "resp")
        assert (await real_db.get_dialog_stats())["today"] == 4

    async def test_old_messages_are_not_counted_today(self, real_db):
        async with real_db.async_session.begin() as session:
            session.add_all([
                History(user_id=1, role="user", content="Old", timestamp=utc_now() - timedelta(days=2)),
                History(user_id=1, role="assistant", content="Answer", timestamp=utc_now()),
            ])
        await real_db.log_dialog(2, None, "New", "Answer")
        assert await real_db.get_stats() == {"total": 2, "users": 2, "today": 1}


class TestUserLanguage:
    async def test_get_language_unset_returns_none(self, real_db):
        assert await real_db.get_user_language(12345) is None

    @pytest.mark.parametrize("language", ["uk", "en"])
    async def test_set_and_get_language(self, real_db, language):
        await real_db.set_user_language(12345, language)
        assert await real_db.get_user_language(12345) == language

    async def test_update_language_upsert(self, real_db):
        await real_db.set_user_language(111, "uk")
        await real_db.set_user_language(111, "en")
        assert await real_db.get_user_language(111) == "en"
        async with real_db.async_session() as session:
            assert await session.scalar(select(func.count()).select_from(User)) == 1

    async def test_multiple_users_independent_languages(self, real_db):
        await real_db.set_user_language(1, "uk")
        await real_db.set_user_language(2, "en")
        assert await real_db.get_user_language(1) == "uk"
        assert await real_db.get_user_language(2) == "en"

    async def test_upsert_user_preserves_first_seen_and_username(self, real_db):
        user_id = 10**12
        await real_db.upsert_user(user_id, "alice", "en")
        async with real_db.async_session() as session:
            first_seen = (await session.get(User, user_id)).first_seen
        await real_db.upsert_user(user_id, "new_username")
        await real_db.set_user_language(user_id, "uk")
        async with real_db.async_session() as session:
            user = await session.get(User, user_id)
        assert user.first_seen == first_seen
        assert user.last_active >= first_seen
        assert user.username == "new_username"
        assert user.language == "uk"


class TestChunksCache:
    async def test_has_documents_empty_and_populated(self, real_db):
        assert await real_db.has_documents() is False
        await real_db.upsert_document("doc1.pdf", "pdf", [make_chunk()])
        assert await real_db.has_documents() is True

    async def test_document_without_chunks_is_still_present(self, real_db):
        await real_db.upsert_document("empty.pdf", "pdf", [])
        assert await real_db.get_all_chunks() == []
        assert await real_db.has_documents() is True

    async def test_chunks_cache_caching_and_invalidation(self, real_db):
        await real_db.upsert_document("doc1.pdf", "pdf", [make_chunk()])
        assert real_db._chunks_cache is None
        first = await real_db.get_all_chunks_with_metadata()
        assert len(first) == 1
        assert real_db._chunks_cache is first
        assert await real_db.get_all_chunks() is first
        real_db.invalidate_chunks_cache()
        assert real_db._chunks_cache is None
        assert await real_db.get_all_chunks() is not first
        await real_db.upsert_document("doc2.pdf", "pdf", [make_chunk()])
        assert real_db._chunks_cache is None
        await real_db.get_all_chunks()
        await real_db.delete_document_by_name("doc1.pdf")
        assert real_db._chunks_cache is None


class TestDocumentsManagement:
    async def test_list_documents_includes_id(self, real_db):
        await real_db.upsert_document("doc1.pdf", "pdf", [make_chunk()])
        documents = await real_db.list_documents()
        assert len(documents) == 1
        assert isinstance(documents[0]["id"], int)
        assert documents[0]["filename"] == "doc1.pdf"
        assert set(documents[0]) == {"id", "filename", "file_type", "chunk_count", "uploaded_at"}

    @pytest.mark.parametrize("by_id", [True, False])
    async def test_delete_document_success(self, real_db, by_id):
        await real_db.upsert_document("doc1.pdf", "pdf", [make_chunk()])
        document = (await real_db.list_documents())[0]
        target = document["id"] if by_id else "DOC1.PDF"
        assert await real_db.delete_document(target) is True
        assert await real_db.list_documents() == []
        async with real_db.async_session() as session:
            assert await session.scalar(select(func.count()).select_from(DocumentChunk)) == 0

    async def test_delete_document_not_found(self, real_db):
        assert await real_db.delete_document_by_id(99999) is False
        assert await real_db.delete_document_by_name("missing.pdf") is False

    async def test_clear_all_documents(self, real_db):
        await real_db.upsert_document("doc1.pdf", "pdf", [make_chunk()])
        await real_db.upsert_document("doc2.docx", "docx", [make_chunk("Text 2")])
        await real_db.get_all_chunks()
        assert await real_db.clear_all_documents() == 2
        assert await real_db.list_documents() == []
        assert await real_db.has_documents() is False
        assert real_db._chunks_cache is None
        async with real_db.async_session() as session:
            assert await session.scalar(select(func.count()).select_from(DocumentChunk)) == 0
        assert await real_db.clear_all_documents() == 0

    async def test_orm_delete_cascades_to_chunks(self, real_db):
        await real_db.upsert_document("doc.pdf", "pdf", [make_chunk(), make_chunk("Text 2", 1)])
        async with real_db.async_session.begin() as session:
            document = (await session.scalars(select(Document))).one()
            await session.delete(document)
        async with real_db.async_session() as session:
            assert await session.scalar(select(func.count()).select_from(DocumentChunk)) == 0

    async def test_orphan_chunk_is_rejected(self, real_db):
        with pytest.raises(IntegrityError):
            async with real_db.async_session.begin() as session:
                session.add(DocumentChunk(doc_id=99, page_number=1, chunk_index=0, content="Text", embedding=b""))

    async def test_replacement_is_case_insensitive_and_keeps_only_new_chunks(self, real_db):
        await real_db.upsert_document(" Report.pdf ", "pdf", [make_chunk(), make_chunk("Old", 1)])
        old_id = (await real_db.list_documents())[0]["id"]
        result = await real_db.upsert_document("report.PDF", "pdf", [make_chunk("New")])
        assert result == {"is_replaced": True, "chunk_count": 1}
        documents = await real_db.list_documents()
        assert len(documents) == 1
        assert documents[0]["id"] > old_id
        chunks = await real_db.get_all_chunks()
        assert [chunk["content"] for chunk in chunks] == ["New"]

    async def test_failed_replacement_rolls_back(self, real_db):
        await real_db.upsert_document("doc.pdf", "pdf", [make_chunk("Original")])
        invalid_chunk = make_chunk("Replacement")
        del invalid_chunk["embedding"]
        with pytest.raises(KeyError):
            await real_db.upsert_document("doc.pdf", "pdf", [invalid_chunk])
        assert [chunk["content"] for chunk in await real_db.get_all_chunks()] == ["Original"]

    @pytest.mark.parametrize("embedding", [
        [0.1, 0.2], (0.1, 0.2), np.array([0.1, 0.2], dtype=np.float64),
        np.array([0.1, 0.2], dtype=np.float32).tobytes(),
        bytearray(np.array([0.1, 0.2], dtype=np.float32).tobytes()),
    ])
    async def test_embedding_round_trip(self, real_db, embedding):
        await real_db.upsert_document("doc.pdf", "pdf", [make_chunk(embedding=embedding)])
        chunks = await real_db.get_all_chunks()
        np.testing.assert_allclose(chunks[0]["embedding"], [0.1, 0.2])
        assert chunks[0]["embedding"].dtype == np.float32

    async def test_custom_embedding_metadata(self, real_db):
        await real_db.upsert_document(
            "doc.pdf", "pdf", [make_chunk()], embedding_model="custom-model", embedding_dim=2
        )
        async with real_db.async_session() as session:
            document = (await session.scalars(select(Document))).one()
        assert document.embedding_model == "custom-model"
        assert document.embedding_dim == 2


class TestLeads:
    async def test_create_pending_lead(self, real_db):
        lead = await real_db.create_lead(
            user_id=10**12, username="alice", name="Anna", phone="+380991234567", request_text="Request"
        )
        assert isinstance(lead, Lead)
        assert lead.id > 0
        assert UUID(lead.lead_uuid)
        assert lead.status == "pending"
        assert lead.attempts == 0
        assert lead.last_error is None
        assert lead.sent_at is None
        assert isinstance(lead.created_at, datetime)
        assert lead.username == "alice"
        assert lead.user_id == 10**12
        assert inspect(lead).detached
        assert [row.id for row in await real_db.get_pending_leads()] == [lead.id]

    async def test_pending_to_sent(self, real_db):
        lead = await real_db.create_lead(1, "Test", "1234567", "Request")
        await real_db.mark_lead_sent(lead.id)
        async with real_db.async_session() as session:
            saved = await session.get(Lead, lead.id)
        assert saved.status == "sent"
        assert saved.attempts == 1
        assert saved.last_error is None
        assert isinstance(saved.sent_at, datetime)
        assert await real_db.get_pending_leads() == []

    async def test_pending_to_failed(self, real_db):
        lead = await real_db.create_lead(1, "Test", "1234567", "Request")
        await real_db.mark_lead_failed(lead.id, "TimeoutError: Sheets timed out")
        saved = (await real_db.get_pending_leads())[0]
        assert saved.status == "failed"
        assert saved.attempts == 1
        assert saved.last_error == "TimeoutError: Sheets timed out"
        assert saved.sent_at is None
        assert saved.name == "Test"
        assert saved.phone == "1234567"
        assert saved.request_text == "Request"

    async def test_retries_update_attempts_and_clear_last_error(self, real_db):
        lead = await real_db.create_lead(1, "Test", "1234567", "Request")
        await real_db.mark_lead_failed(lead.id, "First error")
        await real_db.mark_lead_failed(lead.id, "Second error")
        await real_db.mark_lead_sent(lead.id)
        async with real_db.async_session() as session:
            saved = await session.get(Lead, lead.id)
        assert saved.status == "sent"
        assert saved.attempts == 3
        assert saved.last_error is None
        assert isinstance(saved.sent_at, datetime)

    async def test_sent_status_does_not_regress(self, real_db):
        lead = await real_db.create_lead(1, "Test", "1234567", "Request")
        await real_db.mark_lead_sent(lead.id)
        await real_db.mark_lead_sent(lead.id)
        await real_db.mark_lead_failed(lead.id, "Late timeout")
        async with real_db.async_session() as session:
            saved = await session.get(Lead, lead.id)
        assert saved.status == "sent"
        assert saved.attempts == 1
        assert saved.last_error is None

    async def test_create_is_idempotent(self, real_db):
        lead = await real_db.create_lead(1, "Original", "1234567", "Request", lead_uuid="same-key")
        await real_db.mark_lead_sent(lead.id)
        duplicate = await real_db.create_lead(1, "Changed", "7654321", "Different", lead_uuid="same-key")
        assert duplicate.id == lead.id
        assert duplicate.name == "Original"
        assert duplicate.status == "sent"
        async with real_db.async_session() as session:
            assert await session.scalar(select(func.count()).select_from(Lead)) == 1

    async def test_concurrent_creation_is_idempotent(self, real_db):
        leads = await asyncio.gather(*[
            real_db.create_lead(1, "Test", "1234567", "Request", lead_uuid="concurrent-key")
            for _ in range(5)
        ])
        assert len({lead.id for lead in leads}) == 1

    async def test_pending_leads_include_failures_and_respect_limit(self, real_db):
        leads = [await real_db.create_lead(i, "Test", "1234567", "Request") for i in range(25)]
        await real_db.mark_lead_sent(leads[0].id)
        await real_db.mark_lead_failed(leads[1].id, "Retry later")
        assert [lead.id for lead in await real_db.get_pending_leads()] == [lead.id for lead in leads[1:21]]
        assert [lead.id for lead in await real_db.get_pending_leads(2)] == [lead.id for lead in leads[1:3]]
        assert await real_db.get_pending_leads(0) == []
        assert await real_db.get_pending_leads(-1) == []

    async def test_failed_leads_query_and_count(self, real_db):
        pending = await real_db.create_lead(1, "Pending", "1234567", "Request 1")
        sent = await real_db.create_lead(2, "Sent", "1234568", "Request 2")
        failed_first = await real_db.create_lead(3, "Failed One", "1234569", "Request 3")
        failed_second = await real_db.create_lead(4, "Failed Two", "1234570", "Request 4")
        await real_db.mark_lead_sent(sent.id)
        await real_db.mark_lead_failed(failed_first.id, "TimeoutError: first")
        await real_db.mark_lead_failed(failed_second.id, "OSError: second")

        assert pending.status == "pending"
        assert await real_db.count_failed_leads() == 2
        assert [lead.id for lead in await real_db.get_failed_leads()] == [
            failed_first.id,
            failed_second.id,
        ]
        assert [lead.id for lead in await real_db.get_failed_leads(1)] == [failed_first.id]
        assert await real_db.get_failed_leads(0) == []
        assert await real_db.get_failed_leads(-1) == []

        await real_db.mark_lead_sent(failed_first.id)
        assert await real_db.count_failed_leads() == 1
        assert [lead.id for lead in await real_db.get_failed_leads()] == [failed_second.id]

    async def test_failed_leads_filter_by_max_attempts(self, real_db):
        lead1 = await real_db.create_lead(10, "Lead 10", "123", "Req")
        lead2 = await real_db.create_lead(20, "Lead 20", "456", "Req")
        await real_db.mark_lead_failed(lead1.id, "Err 1")  # attempts = 1
        await real_db.mark_lead_failed(lead2.id, "Err 2")
        await real_db.mark_lead_failed(lead2.id, "Err 2")
        await real_db.mark_lead_failed(lead2.id, "Err 2")  # attempts = 3

        assert len(await real_db.get_failed_leads(max_attempts=2)) == 1
        assert (await real_db.get_failed_leads(max_attempts=2))[0].id == lead1.id
        assert len(await real_db.get_failed_leads(max_attempts=4)) == 2
        assert await real_db.count_failed_leads(max_attempts=2) == 1
        assert await real_db.count_failed_leads(max_attempts=4) == 2


class TestUserDailyLimit:
    async def test_count_user_messages_today_empty(self, real_db):
        assert await real_db.count_user_messages_today(12345) == 0

    async def test_count_user_messages_today_counts_only_today_and_user_role(self, real_db):
        user_id = 98765
        now = utc_now()
        yesterday = now - timedelta(days=1)

        async with real_db.async_session.begin() as session:
            session.add_all([
                History(user_id=user_id, role="user", content="old user msg", timestamp=yesterday),
                History(user_id=user_id, role="assistant", content="old bot reply", timestamp=yesterday),
                History(user_id=user_id, role="assistant", content="today bot reply", timestamp=now),
                History(user_id=user_id, role="user", content="msg 1", timestamp=now),
                History(user_id=user_id, role="user", content="msg 2", timestamp=now),
                History(user_id=user_id, role="user", content="msg 3", timestamp=now),
                History(user_id=11111, role="user", content="other user", timestamp=now),
            ])

        count = await real_db.count_user_messages_today(user_id)
        assert count == 3
        assert await real_db.count_user_messages_today(11111) == 1
        assert await real_db.count_user_messages_today(99999) == 0

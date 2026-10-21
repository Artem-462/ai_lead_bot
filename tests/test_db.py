import importlib.util
import os
import sqlite3
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

_db_path = os.path.join(PROJECT_ROOT, "services", "db.py")
_spec = importlib.util.spec_from_file_location("services.db.real", _db_path)
db_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(db_module)


def _patch_db(monkeypatch, tmp_db: str):
    monkeypatch.setattr(db_module, "DB_FILE", tmp_db)


class TestInitDb:
    async def test_creates_history_table(self, monkeypatch, tmp_db):
        _patch_db(monkeypatch, tmp_db)
        await db_module.init_db()
        with sqlite3.connect(tmp_db) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='history'"
            )
            row = cursor.fetchone()
        assert row is not None, "history table should exist after init_db()"
        with sqlite3.connect(tmp_db) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
            )
            row = cursor.fetchone()
        assert row is not None, "users table should exist after init_db()"

    async def test_users_table_has_expected_columns(self, monkeypatch, tmp_db):
        _patch_db(monkeypatch, tmp_db)
        await db_module.init_db()
        with sqlite3.connect(tmp_db) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(users)")
            cols = {row[1] for row in cursor.fetchall()}
        expected = {"user_id", "language", "created_at", "updated_at"}
        assert expected == cols

    async def test_history_table_has_expected_columns(self, monkeypatch, tmp_db):
        _patch_db(monkeypatch, tmp_db)
        await db_module.init_db()
        with sqlite3.connect(tmp_db) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(history)")
            cols = {row[1] for row in cursor.fetchall()}
        expected = {"id", "user_id", "username", "user_message", "bot_response", "timestamp"}
        assert expected == cols

    async def test_calling_init_db_twice_does_not_raise(self, monkeypatch, tmp_db):
        _patch_db(monkeypatch, tmp_db)
        await db_module.init_db()
        await db_module.init_db()

    async def test_init_db_on_fresh_db_leaves_table_empty(self, monkeypatch, tmp_db):
        _patch_db(monkeypatch, tmp_db)
        await db_module.init_db()
        with sqlite3.connect(tmp_db) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM history")
            count = cursor.fetchone()[0]
        assert count == 0


class TestLogDialog:
    async def test_inserts_row(self, monkeypatch, tmp_db):
        _patch_db(monkeypatch, tmp_db)
        await db_module.init_db()
        await db_module.log_dialog(111, "alice", "hello", "hi there")
        with sqlite3.connect(tmp_db) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM history")
            count = cursor.fetchone()[0]
        assert count == 1

    async def test_row_has_correct_values(self, monkeypatch, tmp_db):
        _patch_db(monkeypatch, tmp_db)
        await db_module.init_db()
        await db_module.log_dialog(222, "bob", "question?", "answer!")
        with sqlite3.connect(tmp_db) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT user_id, username, user_message, bot_response FROM history"
            )
            row = cursor.fetchone()
        assert row[0] == 222
        assert row[1] == "bob"
        assert row[2] == "question?"
        assert row[3] == "answer!"

    async def test_multiple_inserts(self, monkeypatch, tmp_db):
        _patch_db(monkeypatch, tmp_db)
        await db_module.init_db()
        for i in range(5):
            await db_module.log_dialog(i, f"user{i}", f"msg{i}", f"resp{i}")
        with sqlite3.connect(tmp_db) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM history")
            count = cursor.fetchone()[0]
        assert count == 5

    async def test_none_username_accepted(self, monkeypatch, tmp_db):
        _patch_db(monkeypatch, tmp_db)
        await db_module.init_db()
        await db_module.log_dialog(333, None, "hi", "hello")
        with sqlite3.connect(tmp_db) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT username FROM history")
            row = cursor.fetchone()
        assert row[0] is None


class TestGetDialogStats:
    async def test_returns_dict_with_required_keys(self, monkeypatch, tmp_db):
        _patch_db(monkeypatch, tmp_db)
        await db_module.init_db()
        stats = await db_module.get_dialog_stats()
        assert isinstance(stats, dict)
        assert "total" in stats
        assert "users" in stats
        assert "today" in stats

    async def test_empty_db_all_zeros(self, monkeypatch, tmp_db):
        _patch_db(monkeypatch, tmp_db)
        await db_module.init_db()
        stats = await db_module.get_dialog_stats()
        assert stats["total"] == 0
        assert stats["users"] == 0
        assert stats["today"] == 0

    async def test_all_values_are_ints(self, monkeypatch, tmp_db):
        _patch_db(monkeypatch, tmp_db)
        await db_module.init_db()
        stats = await db_module.get_dialog_stats()
        assert isinstance(stats["total"], int)
        assert isinstance(stats["users"], int)
        assert isinstance(stats["today"], int)

    async def test_all_values_non_negative(self, monkeypatch, tmp_db):
        _patch_db(monkeypatch, tmp_db)
        await db_module.init_db()
        stats = await db_module.get_dialog_stats()
        assert stats["total"] >= 0
        assert stats["users"] >= 0
        assert stats["today"] >= 0

    async def test_stats_after_insertions(self, monkeypatch, tmp_db):
        _patch_db(monkeypatch, tmp_db)
        await db_module.init_db()
        await db_module.log_dialog(1, "u1", "m1", "r1")
        await db_module.log_dialog(1, "u1", "m2", "r2")
        await db_module.log_dialog(2, "u2", "m3", "r3")
        stats = await db_module.get_dialog_stats()
        assert stats["total"] == 3
        assert stats["users"] == 2
        assert stats["today"] == 3

    async def test_today_count_increments_with_each_insert(self, monkeypatch, tmp_db):
        _patch_db(monkeypatch, tmp_db)
        await db_module.init_db()
        for i in range(4):
            await db_module.log_dialog(i, f"u{i}", "msg", "resp")
        stats = await db_module.get_dialog_stats()
        assert stats["today"] == 4


class TestUserLanguage:
    async def test_get_language_unset_returns_none(self, monkeypatch, tmp_db):
        _patch_db(monkeypatch, tmp_db)
        await db_module.init_db()
        assert await db_module.get_user_language(12345) is None

    async def test_set_and_get_language_uk(self, monkeypatch, tmp_db):
        _patch_db(monkeypatch, tmp_db)
        await db_module.init_db()
        await db_module.set_user_language(12345, "uk")
        assert await db_module.get_user_language(12345) == "uk"

    async def test_set_and_get_language_en(self, monkeypatch, tmp_db):
        _patch_db(monkeypatch, tmp_db)
        await db_module.init_db()
        await db_module.set_user_language(67890, "en")
        assert await db_module.get_user_language(67890) == "en"

    async def test_update_language_upsert(self, monkeypatch, tmp_db):
        _patch_db(monkeypatch, tmp_db)
        await db_module.init_db()
        await db_module.set_user_language(111, "uk")
        assert await db_module.get_user_language(111) == "uk"
        await db_module.set_user_language(111, "en")
        assert await db_module.get_user_language(111) == "en"

    async def test_multiple_users_independent_languages(self, monkeypatch, tmp_db):
        _patch_db(monkeypatch, tmp_db)
        await db_module.init_db()
        await db_module.set_user_language(1, "uk")
        await db_module.set_user_language(2, "en")
        assert await db_module.get_user_language(1) == "uk"
        assert await db_module.get_user_language(2) == "en"


class TestChunksCache:
    async def test_has_documents_empty_and_populated(self, monkeypatch, tmp_db):
        _patch_db(monkeypatch, tmp_db)
        await db_module.init_db()
        assert await db_module.has_documents() is False

        sample_chunks = [
            {"page_number": 1, "chunk_index": 0, "content": "Sample content", "embedding": [0.1, 0.2]}
        ]
        await db_module.upsert_document("doc1.pdf", "pdf", sample_chunks)
        assert await db_module.has_documents() is True

    async def test_chunks_cache_caching_and_invalidation(self, monkeypatch, tmp_db):
        _patch_db(monkeypatch, tmp_db)
        await db_module.init_db()

        sample_chunks = [
            {"page_number": 1, "chunk_index": 0, "content": "Text 1", "embedding": [0.1, 0.2]}
        ]
        await db_module.upsert_document("doc1.pdf", "pdf", sample_chunks)

        # Cache is initially invalidated (None)
        assert db_module._chunks_cache is None

        # First read populates cache
        chunks1 = await db_module.get_all_chunks_with_metadata()
        assert len(chunks1) == 1
        assert db_module._chunks_cache is not None
        assert db_module._chunks_cache is chunks1

        # Second read returns same cached object without hitting sqlite
        chunks2 = await db_module.get_all_chunks_with_metadata()
        assert chunks2 is chunks1

        # Invalidate manually
        db_module.invalidate_chunks_cache()
        assert db_module._chunks_cache is None

        # Re-populates on next call
        chunks3 = await db_module.get_all_chunks_with_metadata()
        assert chunks3 is not None
        assert db_module._chunks_cache is not None

        # Upsert invalidates cache
        await db_module.upsert_document("doc2.pdf", "pdf", sample_chunks)
        assert db_module._chunks_cache is None

        # Re-populate
        await db_module.get_all_chunks_with_metadata()
        assert db_module._chunks_cache is not None

        # Delete invalidates cache
        await db_module.delete_document_by_name("doc1.pdf")
        assert db_module._chunks_cache is None


class TestDocumentsManagement:
    async def test_list_documents_includes_id(self, monkeypatch, tmp_db):
        _patch_db(monkeypatch, tmp_db)
        await db_module.init_db()
        sample_chunks = [
            {"page_number": 1, "chunk_index": 0, "content": "Text 1", "embedding": [0.1, 0.2]}
        ]
        await db_module.upsert_document("doc1.pdf", "pdf", sample_chunks)
        docs = await db_module.list_documents()
        assert len(docs) == 1
        assert "id" in docs[0]
        assert isinstance(docs[0]["id"], int)
        assert docs[0]["filename"] == "doc1.pdf"

    async def test_delete_document_by_id_success(self, monkeypatch, tmp_db):
        _patch_db(monkeypatch, tmp_db)
        await db_module.init_db()
        sample_chunks = [
            {"page_number": 1, "chunk_index": 0, "content": "Text 1", "embedding": [0.1, 0.2]}
        ]
        await db_module.upsert_document("doc1.pdf", "pdf", sample_chunks)
        docs = await db_module.list_documents()
        doc_id = docs[0]["id"]
        res = await db_module.delete_document_by_id(doc_id)
        assert res is True
        remaining = await db_module.list_documents()
        assert len(remaining) == 0

    async def test_delete_document_by_id_not_found(self, monkeypatch, tmp_db):
        _patch_db(monkeypatch, tmp_db)
        await db_module.init_db()
        res = await db_module.delete_document_by_id(99999)
        assert res is False

    async def test_clear_all_documents(self, monkeypatch, tmp_db):
        _patch_db(monkeypatch, tmp_db)
        await db_module.init_db()
        chunks1 = [
            {"page_number": 1, "chunk_index": 0, "content": "Text 1", "embedding": [0.1, 0.2]}
        ]
        chunks2 = [
            {"page_number": 1, "chunk_index": 0, "content": "Text 2", "embedding": [0.3, 0.4]}
        ]
        await db_module.upsert_document("doc1.pdf", "pdf", chunks1)
        await db_module.upsert_document("doc2.docx", "docx", chunks2)

        docs = await db_module.list_documents()
        assert len(docs) == 2
        assert await db_module.has_documents() is True

        deleted_count = await db_module.clear_all_documents()
        assert deleted_count == 2
        assert await db_module.list_documents() == []
        assert await db_module.has_documents() is False

    async def test_clear_all_documents_success(self, monkeypatch, tmp_db):
        _patch_db(monkeypatch, tmp_db)
        await db_module.init_db()
        sample_chunks = [
            {"page_number": 1, "chunk_index": 0, "content": "Text 1", "embedding": [0.1, 0.2]}
        ]
        await db_module.upsert_document("doc1.pdf", "pdf", sample_chunks)
        await db_module.upsert_document("doc2.docx", "docx", sample_chunks)
        assert len(await db_module.list_documents()) == 2

        count = await db_module.clear_all_documents()
        assert count == 2
        assert len(await db_module.list_documents()) == 0
        assert await db_module.has_documents() is False
        assert db_module._chunks_cache is None


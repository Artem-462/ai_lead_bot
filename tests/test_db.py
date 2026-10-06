"""
tests/test_db.py
Tests for services/db.py using a temporary SQLite file.
The module-level DB_FILE is monkeypatched before every call so no real DB is touched.
"""
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

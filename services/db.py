import os
from typing import Any

import aiosqlite
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_FILE = os.path.join(DATA_DIR, "chat_history.db")

_chunks_cache: list[dict[str, Any]] | None = None


def invalidate_chunks_cache() -> None:
    global _chunks_cache
    _chunks_cache = None


async def init_db() -> None:
    async with aiosqlite.connect(DB_FILE, timeout=10.0) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                user_message TEXT,
                bot_response TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                language TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT UNIQUE COLLATE NOCASE NOT NULL,
                file_type TEXT NOT NULL,
                chunk_count INTEGER NOT NULL DEFAULT 0,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS document_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                page_number INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                embedding BLOB NOT NULL
            )
        """)
        await conn.commit()
    invalidate_chunks_cache()


async def log_dialog(
    user_id: int, username: str | None, user_message: str, bot_response: str
) -> None:
    async with aiosqlite.connect(DB_FILE, timeout=10.0) as conn:
        await conn.execute(
            """
            INSERT INTO history (user_id, username, user_message, bot_response)
            VALUES (?, ?, ?, ?)
        """,
            (user_id, username, user_message, bot_response),
        )
        await conn.commit()


async def get_dialog_stats() -> dict[str, int]:
    async with aiosqlite.connect(DB_FILE, timeout=10.0) as conn:
        async with conn.execute("SELECT COUNT(*) FROM history") as cursor:
            row = await cursor.fetchone()
            total = row[0] if row else 0

        async with conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM history"
        ) as cursor:
            row = await cursor.fetchone()
            users = row[0] if row else 0

        async with conn.execute(
            "SELECT COUNT(*) FROM history WHERE DATE(timestamp, 'localtime') = DATE('now', 'localtime')"
        ) as cursor:
            row = await cursor.fetchone()
            today = row[0] if row else 0

    return {"total": total, "users": users, "today": today}


get_stats = get_dialog_stats


async def set_user_language(user_id: int, language: str) -> None:
    async with aiosqlite.connect(DB_FILE, timeout=10.0) as conn:
        await conn.execute(
            """
            INSERT INTO users (user_id, language, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                language = excluded.language,
                updated_at = CURRENT_TIMESTAMP
        """,
            (user_id, language),
        )
        await conn.commit()


async def get_user_language(user_id: int) -> str | None:
    async with (
        aiosqlite.connect(DB_FILE, timeout=10.0) as conn,
        conn.execute(
            "SELECT language FROM users WHERE user_id = ?", (user_id,)
        ) as cursor,
    ):
        row = await cursor.fetchone()
        return row[0] if row else None


async def upsert_document(
    filename: str, file_type: str, chunks: list[dict[str, Any]]
) -> dict[str, Any]:
    filename = filename.strip()
    async with aiosqlite.connect(DB_FILE, timeout=10.0) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        async with conn.execute(
            "SELECT id FROM documents WHERE filename = ? COLLATE NOCASE", (filename,)
        ) as cursor:
            existing = await cursor.fetchone()

        is_replaced = False
        if existing:
            is_replaced = True
            doc_id = existing[0]
            await conn.execute(
                "DELETE FROM document_chunks WHERE doc_id = ?", (doc_id,)
            )
            await conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

        cursor = await conn.execute(
            "INSERT INTO documents (filename, file_type, chunk_count) VALUES (?, ?, ?)",
            (filename, file_type, len(chunks)),
        )
        new_doc_id = cursor.lastrowid

        for c in chunks:
            emb = c["embedding"]
            if isinstance(emb, np.ndarray):
                emb_bytes = emb.astype(np.float32).tobytes()
            elif isinstance(emb, (list, tuple)):
                emb_bytes = np.array(emb, dtype=np.float32).tobytes()
            elif isinstance(emb, bytes):
                emb_bytes = emb
            else:
                emb_bytes = bytes(emb)

            await conn.execute(
                """
                INSERT INTO document_chunks (doc_id, page_number, chunk_index, content, embedding)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    new_doc_id,
                    c["page_number"],
                    c["chunk_index"],
                    c["content"],
                    emb_bytes,
                ),
            )

        await conn.commit()
    invalidate_chunks_cache()
    return {"is_replaced": is_replaced, "chunk_count": len(chunks)}


async def delete_document_by_name(filename: str) -> bool:
    filename = filename.strip()
    async with aiosqlite.connect(DB_FILE, timeout=10.0) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        async with conn.execute(
            "SELECT id FROM documents WHERE filename = ? COLLATE NOCASE", (filename,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return False
            doc_id = row[0]

        await conn.execute("DELETE FROM document_chunks WHERE doc_id = ?", (doc_id,))
        await conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        await conn.commit()
    invalidate_chunks_cache()
    return True


async def delete_document_by_id(doc_id: int) -> bool:
    async with aiosqlite.connect(DB_FILE, timeout=10.0) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        async with conn.execute(
            "SELECT id FROM documents WHERE id = ?", (doc_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return False

        await conn.execute("DELETE FROM document_chunks WHERE doc_id = ?", (doc_id,))
        await conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        await conn.commit()
    invalidate_chunks_cache()
    return True


async def clear_all_documents() -> int:
    async with aiosqlite.connect(DB_FILE, timeout=10.0) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        async with conn.execute("SELECT COUNT(*) FROM documents") as cursor:
            row = await cursor.fetchone()
            count = int(row[0]) if row else 0

        await conn.execute("DELETE FROM document_chunks")
        await conn.execute("DELETE FROM documents")
        await conn.commit()
    invalidate_chunks_cache()
    return count


async def list_documents() -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_FILE, timeout=10.0) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT id, filename, file_type, chunk_count, uploaded_at FROM documents ORDER BY id DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "id": row["id"],
                    "filename": row["filename"],
                    "file_type": row["file_type"],
                    "chunk_count": row["chunk_count"],
                    "uploaded_at": str(row["uploaded_at"]),
                }
                for row in rows
            ]


async def has_documents() -> bool:
    if _chunks_cache is not None:
        return bool(_chunks_cache)
    async with (
        aiosqlite.connect(DB_FILE, timeout=10.0) as conn,
        conn.execute("SELECT 1 FROM documents LIMIT 1") as cursor,
    ):
        row = await cursor.fetchone()
        return row is not None


async def get_all_chunks_with_metadata() -> list[dict[str, Any]]:
    global _chunks_cache
    if _chunks_cache is not None:
        return _chunks_cache

    async with aiosqlite.connect(DB_FILE, timeout=10.0) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("""
            SELECT c.content, c.page_number, c.chunk_index, c.embedding, d.filename
            FROM document_chunks c
            JOIN documents d ON c.doc_id = d.id
            ORDER BY c.doc_id, c.chunk_index
        """) as cursor:
            rows = await cursor.fetchall()
            _chunks_cache = [
                {
                    "content": row["content"],
                    "page_number": row["page_number"],
                    "chunk_index": row["chunk_index"],
                    "embedding": np.frombuffer(row["embedding"], dtype=np.float32),
                    "filename": row["filename"],
                }
                for row in rows
            ]
            return _chunks_cache



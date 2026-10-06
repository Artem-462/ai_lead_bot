import os

import aiosqlite

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_FILE = os.path.join(DATA_DIR, "chat_history.db")


async def init_db() -> None:
    async with aiosqlite.connect(DB_FILE, timeout=10.0) as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                user_message TEXT,
                bot_response TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                language TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await conn.commit()


async def log_dialog(user_id: int, username: str | None, user_message: str, bot_response: str) -> None:
    async with aiosqlite.connect(DB_FILE, timeout=10.0) as conn:
        await conn.execute('''
            INSERT INTO history (user_id, username, user_message, bot_response)
            VALUES (?, ?, ?, ?)
        ''', (user_id, username, user_message, bot_response))
        await conn.commit()


async def get_dialog_stats() -> dict[str, int]:
    async with aiosqlite.connect(DB_FILE, timeout=10.0) as conn:
        async with conn.execute("SELECT COUNT(*) FROM history") as cursor:
            row = await cursor.fetchone()
            total = row[0] if row else 0

        async with conn.execute("SELECT COUNT(DISTINCT user_id) FROM history") as cursor:
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
        await conn.execute('''
            INSERT INTO users (user_id, language, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                language = excluded.language,
                updated_at = CURRENT_TIMESTAMP
        ''', (user_id, language))
        await conn.commit()


async def get_user_language(user_id: int) -> str | None:
    async with (
        aiosqlite.connect(DB_FILE, timeout=10.0) as conn,
        conn.execute("SELECT language FROM users WHERE user_id = ?", (user_id,)) as cursor,
    ):
        row = await cursor.fetchone()
        return row[0] if row else None
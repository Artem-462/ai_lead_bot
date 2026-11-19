import logging
import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
raw_admin_id = os.getenv("ADMIN_ID")
ADMIN_ID = int(raw_admin_id) if raw_admin_id and raw_admin_id.isdigit() else None
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Leads")

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001").strip()
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "3072"))
if not EMBEDDING_MODEL or EMBEDDING_DIM <= 0:
    raise ValueError("EMBEDDING_MODEL must be non-empty and EMBEDDING_DIM must be positive")

MAX_HISTORY_CHARS: int = int(os.getenv("MAX_HISTORY_CHARS", "6000"))
MAX_OUTPUT_TOKENS: int = int(os.getenv("MAX_OUTPUT_TOKENS", "1024"))
if MAX_HISTORY_CHARS <= 0 or MAX_OUTPUT_TOKENS <= 0:
    raise ValueError("MAX_HISTORY_CHARS and MAX_OUTPUT_TOKENS must be positive")

DEFAULT_GEMINI_MODELS = (
    "gemini-2.5-flash",
    "gemini-3.8-flash",
    "gemini-flash-latest",
)

raw_models = os.getenv("GEMINI_MODELS")
if raw_models:
    GEMINI_MODELS = [m.strip() for m in raw_models.split(",") if m.strip()]
else:
    primary = os.getenv("GEMINI_MODEL")
    GEMINI_MODELS = [primary] + [m for m in DEFAULT_GEMINI_MODELS if m != primary] if primary else list(DEFAULT_GEMINI_MODELS)

GEMINI_MODEL = GEMINI_MODELS[0]

OUTBOX_RETRY_INTERVAL_SEC: int = int(os.getenv("OUTBOX_RETRY_INTERVAL_SEC", "300"))
OUTBOX_MAX_ATTEMPTS: int = int(os.getenv("OUTBOX_MAX_ATTEMPTS", "5"))
if OUTBOX_RETRY_INTERVAL_SEC <= 0 or OUTBOX_MAX_ATTEMPTS <= 0:
    raise ValueError("OUTBOX_RETRY_INTERVAL_SEC and OUTBOX_MAX_ATTEMPTS must be positive")

DAILY_USER_MESSAGE_LIMIT: int = int(os.getenv("DAILY_USER_MESSAGE_LIMIT", "30"))
if DAILY_USER_MESSAGE_LIMIT < 0:
    raise ValueError("DAILY_USER_MESSAGE_LIMIT must be non-negative")

if not BOT_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Critical tokens missing! Please check .env file")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
logger = logging.getLogger(__name__)

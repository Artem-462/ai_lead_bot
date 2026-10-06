import logging
import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
raw_admin_id = os.getenv("ADMIN_ID")
ADMIN_ID = int(raw_admin_id) if raw_admin_id and raw_admin_id.isdigit() else None
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Leads")

DEFAULT_GEMINI_MODELS = [
    "gemini-3.8-flash",
    "gemini-2.5-flash",
    "gemini-flash-latest",
]

raw_models = os.getenv("GEMINI_MODELS")
if raw_models:
    GEMINI_MODELS = [m.strip() for m in raw_models.split(",") if m.strip()]
else:
    primary = os.getenv("GEMINI_MODEL")
    GEMINI_MODELS = [primary] + [m for m in DEFAULT_GEMINI_MODELS if m != primary] if primary else DEFAULT_GEMINI_MODELS.copy()

GEMINI_MODEL = GEMINI_MODELS[0]

if not BOT_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Критичні токени не знайдені! Перевір файл .env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
logger = logging.getLogger(__name__)
import logging
import os

import gspread

import config

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREDS_PATH = os.path.join(BASE_DIR, "credentials.json")

_gc: gspread.Client | None = None


def get_gc() -> gspread.Client:
    global _gc
    if _gc is None:
        _gc = gspread.service_account(filename=CREDS_PATH)
    return _gc


def add_lead_to_sheet(name: str, phone: str, request: str) -> bool:
    try:
        sh = get_gc().open(config.GOOGLE_SHEET_NAME)
        sh.sheet1.append_row([name, phone, request], value_input_option="RAW")
        logger.info("Lead saved to Google Sheets: %s (%s)", name, phone)
        return True
    except (gspread.GSpreadException, OSError) as e:
        logger.error("Failed to append lead to Google Sheet: %s", e)
        return False
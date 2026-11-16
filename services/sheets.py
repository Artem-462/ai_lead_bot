import asyncio
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
        _gc.set_timeout(10.0)
    return _gc


def _append_lead(name: str, phone: str, request: str) -> None:
    sheet = get_gc().open(config.GOOGLE_SHEET_NAME)
    sheet.sheet1.append_row([name, phone, request], value_input_option="RAW")


async def add_lead_to_sheet(
    name: str, phone: str, request: str, *, lead_uuid: str | None = None
) -> bool:
    try:
        await asyncio.wait_for(
            asyncio.to_thread(_append_lead, name, phone, request), timeout=10.0
        )
    except Exception as error:
        logger.warning(
            "Google Sheets sync failed (lead_uuid=%s, error=%s)", lead_uuid, type(error).__name__
        )
        raise
    logger.info("Lead synced to Google Sheets (lead_uuid=%s)", lead_uuid)
    return True

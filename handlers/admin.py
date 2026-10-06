from aiogram import Router, types
from aiogram.filters import Command

import config
from i18n import get_lang, t
from services.db import get_dialog_stats, get_user_language

router = Router()


@router.message(Command("stats"))
async def cmd_stats(message: types.Message) -> None:
    user_id = message.from_user.id
    lang = await get_user_language(user_id) or get_lang(message.from_user.language_code)

    if not config.ADMIN_ID or user_id != config.ADMIN_ID:
        await message.answer(t("stats_no_access", lang))
        return

    stats = await get_dialog_stats()
    await message.answer(t("stats", lang, **stats))

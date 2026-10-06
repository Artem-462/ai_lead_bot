from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from i18n import Language, t


def get_language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🇺🇦 Українська", callback_data="lang:uk"),
                InlineKeyboardButton(text="🇬🇧 English", callback_data="lang:en"),
            ]
        ]
    )


def get_main_keyboard(lang: Language = "uk") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("ibtn_ask_ai", lang), callback_data="menu:ask_ai")],
            [InlineKeyboardButton(text=t("ibtn_lead", lang), callback_data="menu:lead")],
            [
                InlineKeyboardButton(text=t("ibtn_about", lang), callback_data="menu:about"),
                InlineKeyboardButton(text=t("ibtn_language", lang), callback_data="menu:language"),
            ],
        ]
    )


def get_cancel_keyboard(lang: Language = "uk") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("ibtn_cancel", lang), callback_data="form:cancel")]
        ]
    )
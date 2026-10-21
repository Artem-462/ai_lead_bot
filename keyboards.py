from typing import Any

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


def get_main_keyboard(lang: Language = "uk", is_admin: bool = False) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(text=t("ibtn_ask_ai", lang), callback_data="menu:ask_ai")],
        [InlineKeyboardButton(text=t("ibtn_lead", lang), callback_data="menu:lead")],
        [
            InlineKeyboardButton(text=t("ibtn_about", lang), callback_data="menu:about"),
            InlineKeyboardButton(text=t("ibtn_language", lang), callback_data="menu:language"),
        ],
    ]
    if is_admin:
        keyboard.append([InlineKeyboardButton(text=t("ibtn_admin", lang), callback_data="admin:panel")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_cancel_keyboard(lang: Language = "uk") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("ibtn_cancel", lang), callback_data="form:cancel")]
        ]
    )


def get_admin_panel_keyboard(lang: Language = "uk") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("admin_btn_stats", lang), callback_data="admin:stats")],
            [InlineKeyboardButton(text=t("admin_btn_docs", lang), callback_data="admin:docs")],
            [InlineKeyboardButton(text=t("admin_btn_upload", lang), callback_data="admin:upload_hint")],
            [InlineKeyboardButton(text=t("admin_btn_back_main", lang), callback_data="admin:back_main")],
        ]
    )


def get_admin_docs_keyboard(docs: list[dict[str, Any]], lang: Language = "uk") -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(
                text=t("admin_del_btn", lang, filename=doc["filename"]),
                callback_data=f"admin:del:{doc.get('id', doc['filename'])}",
            )
        ]
        for doc in docs
    ]
    if docs:
        keyboard.append([
            InlineKeyboardButton(text=t("admin_btn_clear_all", lang), callback_data="admin:clear_all_docs")
        ])
    keyboard.append([InlineKeyboardButton(text=t("admin_btn_back", lang), callback_data="admin:panel")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_admin_back_keyboard(lang: Language = "uk") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("admin_btn_back", lang), callback_data="admin:panel")]
        ]
    )

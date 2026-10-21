import asyncio
import html
import io
import os

from aiogram import F, Router, types
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command

import config
from i18n import get_lang, t
from keyboards import (
    get_admin_back_keyboard,
    get_admin_docs_keyboard,
    get_admin_panel_keyboard,
    get_main_keyboard,
)
from services.db import (
    clear_all_documents,
    delete_document_by_id,
    delete_document_by_name,
    get_dialog_stats,
    get_user_language,
    list_documents,
)
from services.rag import index_document

router = Router()
_index_lock = asyncio.Lock()


@router.message(Command("stats"))
async def cmd_stats(message: types.Message) -> None:
    user_id = message.from_user.id
    lang = await get_user_language(user_id) or get_lang(message.from_user.language_code)

    if not config.ADMIN_ID or user_id != config.ADMIN_ID:
        await message.answer(t("stats_no_access", lang))
        return

    stats = await get_dialog_stats()
    await message.answer(t("stats", lang, **stats))


@router.message(Command("docs"))
async def cmd_docs(message: types.Message) -> None:
    user_id = message.from_user.id
    lang = await get_user_language(user_id) or get_lang(message.from_user.language_code)

    if not config.ADMIN_ID or user_id != config.ADMIN_ID:
        await message.answer(t("stats_no_access", lang))
        return

    docs = await list_documents()
    if not docs:
        await message.answer(t("doc_list_empty", lang))
        return

    doc_lines: list[str] = []
    for d in docs:
        safe_fn = html.escape(d["filename"])
        doc_lines.append(
            f"• <b>{safe_fn}</b> ({d['file_type'].upper()}) — {d['chunk_count']} chunks ({d['uploaded_at'][:10]})"
        )
    await message.answer(t("doc_list_header", lang, docs="\n".join(doc_lines)))


@router.message(Command("del_doc"))
async def cmd_del_doc(message: types.Message) -> None:
    user_id = message.from_user.id
    lang = await get_user_language(user_id) or get_lang(message.from_user.language_code)

    if not config.ADMIN_ID or user_id != config.ADMIN_ID:
        await message.answer(t("stats_no_access", lang))
        return

    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        await message.answer(t("doc_not_found", lang, filename=""))
        return

    filename = args[1].strip()
    deleted = await delete_document_by_name(filename)
    safe_filename = html.escape(filename)
    if deleted:
        await message.answer(t("doc_deleted", lang, filename=safe_filename))
    else:
        await message.answer(t("doc_not_found", lang, filename=safe_filename))


@router.message(Command("admin"))
@router.callback_query(F.data == "admin:panel")
async def show_admin_panel(event: types.Message | types.CallbackQuery) -> None:
    user_id = event.from_user.id
    lang = await get_user_language(user_id) or get_lang(event.from_user.language_code)

    if not config.ADMIN_ID or user_id != config.ADMIN_ID:
        if isinstance(event, types.CallbackQuery):
            await event.answer(t("stats_no_access", lang), show_alert=True)
        else:
            await event.answer(t("stats_no_access", lang))
        return

    text = t("admin_panel_title", lang)
    reply_markup = get_admin_panel_keyboard(lang)
    if isinstance(event, types.CallbackQuery):
        await event.answer()
        if event.message:
            await event.message.edit_text(text, reply_markup=reply_markup)
    else:
        await event.answer(text, reply_markup=reply_markup)


@router.callback_query(F.data == "admin:stats")
async def cb_admin_stats(callback: types.CallbackQuery) -> None:
    user_id = callback.from_user.id
    lang = await get_user_language(user_id) or get_lang(callback.from_user.language_code)

    if not config.ADMIN_ID or user_id != config.ADMIN_ID:
        await callback.answer(t("stats_no_access", lang), show_alert=True)
        return

    stats = await get_dialog_stats()
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            t("stats", lang, **stats),
            reply_markup=get_admin_back_keyboard(lang),
        )


@router.callback_query(F.data == "admin:docs")
async def cb_admin_docs(callback: types.CallbackQuery) -> None:
    user_id = callback.from_user.id
    lang = await get_user_language(user_id) or get_lang(callback.from_user.language_code)

    if not config.ADMIN_ID or user_id != config.ADMIN_ID:
        await callback.answer(t("stats_no_access", lang), show_alert=True)
        return

    docs = await list_documents()
    await callback.answer()
    if not docs:
        if callback.message:
            await callback.message.edit_text(
                t("doc_list_empty", lang),
                reply_markup=get_admin_back_keyboard(lang),
            )
        return

    doc_lines = [
        f"• <b>{html.escape(d['filename'])}</b> ({d['file_type'].upper()}) — {d['chunk_count']} chunks ({d['uploaded_at'][:10]})"
        for d in docs
    ]
    if callback.message:
        await callback.message.edit_text(
            t("doc_list_header", lang, docs="\n".join(doc_lines)),
            reply_markup=get_admin_docs_keyboard(docs, lang),
        )


@router.callback_query(F.data == "admin:upload_hint")
async def cb_admin_upload_hint(callback: types.CallbackQuery) -> None:
    user_id = callback.from_user.id
    lang = await get_user_language(user_id) or get_lang(callback.from_user.language_code)

    if not config.ADMIN_ID or user_id != config.ADMIN_ID:
        await callback.answer(t("stats_no_access", lang), show_alert=True)
        return

    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            t("admin_upload_hint", lang),
            reply_markup=get_admin_back_keyboard(lang),
        )


@router.callback_query(F.data.startswith("admin:del:"))
async def cb_admin_delete_doc(callback: types.CallbackQuery) -> None:
    user_id = callback.from_user.id
    lang = await get_user_language(user_id) or get_lang(callback.from_user.language_code)

    if not config.ADMIN_ID or user_id != config.ADMIN_ID:
        await callback.answer(t("stats_no_access", lang), show_alert=True)
        return

    target = callback.data.split("admin:del:", 1)[1].strip()
    docs = await list_documents()
    target_filename = target
    if target.isdigit():
        doc_id = int(target)
        found_doc = next((d for d in docs if d.get("id") == doc_id), None)
        if found_doc:
            target_filename = found_doc["filename"]
        deleted = await delete_document_by_id(doc_id)
    else:
        deleted = await delete_document_by_name(target)

    if deleted:
        await callback.answer(t("doc_deleted", lang, filename=target_filename))
    else:
        await callback.answer(t("doc_not_found", lang, filename=target_filename))

    updated_docs = await list_documents()
    if not updated_docs:
        text = t("doc_list_empty", lang)
        markup = get_admin_back_keyboard(lang)
    else:
        doc_lines = [
            f"• <b>{html.escape(d['filename'])}</b> ({d['file_type'].upper()}) — {d['chunk_count']} chunks ({d['uploaded_at'][:10]})"
            for d in updated_docs
        ]
        text = t("doc_list_header", lang, docs="\n".join(doc_lines))
        markup = get_admin_docs_keyboard(updated_docs, lang)

    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=markup)
        except TelegramAPIError:
            pass


@router.callback_query(F.data == "admin:clear_all_docs")
async def cb_admin_clear_all_docs(callback: types.CallbackQuery) -> None:
    user_id = callback.from_user.id
    lang = await get_user_language(user_id) or get_lang(callback.from_user.language_code)

    if not config.ADMIN_ID or user_id != config.ADMIN_ID:
        await callback.answer(t("stats_no_access", lang), show_alert=True)
        return

    count = await clear_all_documents()
    await callback.answer(t("doc_all_cleared", lang, count=count), show_alert=True)
    if callback.message:
        await callback.message.edit_text(
            t("doc_list_empty", lang),
            reply_markup=get_admin_back_keyboard(lang),
        )


@router.callback_query(F.data == "admin:back_main")
async def cb_admin_back_main(callback: types.CallbackQuery) -> None:
    user_id = callback.from_user.id
    lang = await get_user_language(user_id) or get_lang(callback.from_user.language_code)

    if not config.ADMIN_ID or user_id != config.ADMIN_ID:
        await callback.answer(t("stats_no_access", lang), show_alert=True)
        return

    await callback.answer()
    if callback.message:
        name = callback.from_user.first_name or ""
        await callback.message.edit_text(
            t("welcome", lang, name=name),
            reply_markup=get_main_keyboard(lang, is_admin=True),
        )


@router.message(F.document)
async def handle_document_upload(message: types.Message) -> None:
    user_id = message.from_user.id
    lang = await get_user_language(user_id) or get_lang(message.from_user.language_code)

    if not config.ADMIN_ID or user_id != config.ADMIN_ID:
        await message.answer(t("non_text", lang))
        return

    doc = message.document
    if not doc or not doc.file_name:
        await message.answer(t("doc_unsupported", lang))
        return

    filename = doc.file_name
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".pdf", ".docx"):
        await message.answer(t("doc_unsupported", lang))
        return

    if isinstance(doc.file_size, (int, float)) and doc.file_size > 20 * 1024 * 1024:
        await message.answer(t("doc_too_large", lang))
        return

    safe_filename = html.escape(filename)
    wait_msg = await message.answer(t("doc_indexing", lang, filename=safe_filename))
    try:
        file_io = await message.bot.download(doc)
        if isinstance(file_io, io.BytesIO):
            file_bytes = file_io.getvalue()
        elif hasattr(file_io, "read"):
            file_bytes = file_io.read()
        else:
            file_bytes = bytes(file_io)

        async with _index_lock:
            res = await index_document(filename, file_bytes)
        chunks_count = res.get("chunk_count", 0)
        is_replaced = res.get("is_replaced", False)

        msg_key = "doc_indexed_replaced" if is_replaced else "doc_indexed_new"
        await wait_msg.edit_text(t(msg_key, lang, filename=safe_filename, chunks=chunks_count))
    except ValueError as e:
        config.logger.warning("Empty document %s: %s", filename, e)
        await wait_msg.edit_text(t("doc_empty", lang, filename=safe_filename))
    except (TelegramAPIError, OSError, RuntimeError) as e:
        config.logger.error("Failed to index document %s: %s", filename, e)
        await wait_msg.edit_text(t("server_error", lang))

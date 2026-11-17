import asyncio
import io
import logging
import os
import re
from typing import Any

import numpy as np
import pdfplumber
import pdfplumber.utils
from aiohttp import ClientError as AiohttpClientError
from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from google.genai import errors
from httpx import TransportError

from config import EMBEDDING_DIM, EMBEDDING_MODEL
from services.db import get_all_chunks_with_metadata, upsert_document
from services.llm import client

logger = logging.getLogger(__name__)

_HEADER_LABELS = frozenset({
    "key", "value", "parameter", "parameters", "name", "service", "product", "feature",
    "description", "price", "cost", "rate", "tariff", "plan", "duration", "term", "quantity",
    "qty", "unit", "status", "type", "date", "amount", "total", "code", "id", "number",
    "ключ", "значення", "значение", "параметр", "параметри", "параметры", "назва",
    "название", "найменування", "наименование", "послуга", "услуга", "товар",
    "характеристика", "опис", "описание", "ціна", "цена", "вартість", "стоимость",
    "тариф", "термін", "срок", "кількість", "количество", "одиниця", "единица",
    "статус", "тип", "дата", "сума", "сумма", "код", "номер",
})
_TABLE_PREFIX = "[Table Row | "
_EMBEDDING_ATTEMPTS = 3
_EMBEDDING_RETRY_DELAY = 1.5


class EmbeddingError(RuntimeError):
    pass


def _cell_text(cell: Any) -> str:
    return str(cell).strip().replace("\r", " ").replace("\n", " ") if cell is not None else ""


def _header_label(cell: str) -> str:
    return re.split(r"[\(\[,]", " ".join(cell.casefold().split()), maxsplit=1)[0].strip(" :.;")


def _looks_like_table_header(row: list[str]) -> bool:
    labels = [_header_label(cell) for cell in row if cell]
    if len(labels) < 2:
        return False
    recognized = sum(label in _HEADER_LABELS for label in labels)
    return recognized >= max(2, (len(labels) + 1) // 2) and not any(
        re.search(r"\d|[$€₴£%]", label) for label in labels if label not in _HEADER_LABELS
    )


def _serialize_table(
    table_data: list[list[Any]],
    previous_headers: list[str] | None = None,
    *,
    explicit_header: bool = False,
) -> tuple[list[str], list[str] | None]:
    rows = []
    for row in table_data:
        if row:
            values = [_cell_text(cell) for cell in row]
            if any(values):
                rows.append(values)
    if not rows:
        return [], None

    column_count = max(len(row) for row in rows)
    rows = [row + [""] * (column_count - len(row)) for row in rows]
    matching_previous = previous_headers is not None and len(previous_headers) == column_count
    repeats_header = matching_previous and [
        _header_label(cell) for cell in rows[0]
    ] == [_header_label(cell) for cell in previous_headers]

    if explicit_header or repeats_header or _looks_like_table_header(rows[0]):
        headers = [cell or f"Col_{i + 1}" for i, cell in enumerate(rows[0])]
        data_rows = rows[1:]
    elif matching_previous:
        headers = previous_headers
        data_rows = rows
    elif column_count == 2:
        lines = []
        key = "Col_1"
        for row in rows:
            key = row[0] or key
            lines.append(f"{key}: {row[1]}")
        return lines, None
    elif len(rows) > 1:
        headers = [cell or f"Col_{i + 1}" for i, cell in enumerate(rows[0])]
        data_rows = rows[1:]
    else:
        headers = [f"Col_{i + 1}" for i in range(column_count)]
        data_rows = rows

    lines = [
        _format_table_row(headers, row) for row in data_rows
        if [_header_label(cell) for cell in row] != [_header_label(cell) for cell in headers]
    ]
    return lines, headers


def _pdf_table_has_header(page: Any, table: Any) -> bool:
    rows = getattr(table, "rows", None)
    chars = getattr(page, "chars", None)
    if not isinstance(rows, list) or not rows or not isinstance(chars, list):
        return False
    bbox = getattr(rows[0], "bbox", None)
    if not isinstance(bbox, (tuple, list)) or len(bbox) != 4:
        return False
    x0, top, x1, bottom = bbox
    header_chars = [
        char for char in chars
        if char.get("text", "").strip()
        and x0 <= (char["x0"] + char["x1"]) / 2 <= x1
        and top <= (char["top"] + char["bottom"]) / 2 < bottom
    ]
    return bool(header_chars) and all(
        "bold" in char.get("fontname", "").casefold() for char in header_chars
    )


def _docx_table_has_header(table: Table) -> bool:
    if not table.rows:
        return False
    if any(
        node.get(qn("w:val"), "true") not in ("0", "false", "off")
        for node in table.rows[0]._tr.xpath("./w:trPr/w:tblHeader")
    ):
        return True
    for cell in table.rows[0].cells:
        runs = [run for paragraph in cell.paragraphs for run in paragraph.runs if run.text.strip()]
        if not runs or not all(run.bold for run in runs):
            return False
    return True


def _format_table_row(headers: list[str], row: list[Any]) -> str:
    cells: list[str] = []
    for i, cell in enumerate(row):
        col = headers[i] if i < len(headers) else f"Col_{i + 1}"
        val = _cell_text(cell)
        cells.append(f"{col}: {val}")
    return f"[Table Row | {' | '.join(cells)}]"


def extract_pdf_pages(file_bytes: bytes) -> list[tuple[int, str]]:
    pages_data: list[tuple[int, str]] = []
    last_headers: list[str] | None = None
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for idx, page in enumerate(pdf.pages):
            page_num = idx + 1
            parts: list[str] = []
            previous_headers = last_headers
            last_headers = None

            found_tables = page.find_tables() or []
            table_bboxes = [
                tbl.bbox for tbl in found_tables if getattr(tbl, "bbox", None)
            ]

            if table_bboxes:

                def not_in_table(
                    obj: dict[str, Any], bboxes: list[Any] = table_bboxes
                ) -> bool:
                    obj_bbox = pdfplumber.utils.obj_to_bbox(obj)
                    return not any(
                        pdfplumber.utils.get_bbox_overlap(obj_bbox, bbox) is not None
                        for bbox in bboxes
                    )

                filtered_page = page.filter(not_in_table)
                text = filtered_page.extract_text()
            else:
                text = page.extract_text()

            if text and text.strip():
                parts.append(text.strip())

            for table_index, tbl in enumerate(found_tables):
                table_data = tbl.extract() if hasattr(tbl, "extract") else []
                lines, last_headers = _serialize_table(
                    table_data or [],
                    previous_headers if table_index == 0 else None,
                    explicit_header=_pdf_table_has_header(page, tbl),
                )
                parts.extend(lines)

            content = "\n\n".join(parts).strip()
            if content:
                pages_data.append((page_num, content))
    return pages_data


def extract_docx_pages(file_bytes: bytes) -> list[tuple[int, str]]:
    doc = Document(io.BytesIO(file_bytes))
    pages: dict[int, list[str]] = {}
    current_page = 1
    chars_on_page = 0

    for child in doc.element.body:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag

        has_page_break = bool(
            child.xpath(
                './/*[local-name()="br" and @*[local-name()="type"]="page"] | '
                './/*[local-name()="lastRenderedPageBreak"]'
            )
        )
        if has_page_break and chars_on_page > 0:
            current_page += 1
            chars_on_page = 0

        element_lines: list[str] = []
        if tag == "p":
            p = Paragraph(child, doc)
            text = p.text.strip()
            if text:
                element_lines.append(text)
        elif tag == "tbl":
            table = Table(child, doc)
            lines, _ = _serialize_table(
                [[cell.text for cell in row.cells] for row in table.rows],
                explicit_header=_docx_table_has_header(table),
            )
            element_lines.extend(lines)

        for line in element_lines:
            line_len = len(line)
            if chars_on_page + line_len > 1800 and chars_on_page > 0:
                current_page += 1
                chars_on_page = 0

            pages.setdefault(current_page, []).append(line)
            chars_on_page += line_len

    return [(pg, "\n".join(lines)) for pg, lines in sorted(pages.items()) if lines]


def _split_text(text: str, size: int, overlap: int) -> list[str]:
    overlap = min(overlap, (size - 1) // 2)
    parts = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = text.rfind(" ", start + size // 2, end)
            if boundary > start:
                end = boundary
        part = text[start:end].strip()
        if part:
            parts.append(part)
        if end == len(text):
            break
        start = max(start + 1, end - overlap)
    return parts


def _split_labelled_field(field: str, size: int, overlap: int) -> list[str]:
    if len(field) <= size:
        return [field]
    label, separator, value = field.partition(": ")
    prefix = f"{label}{separator}"
    value_size = size - len(prefix)
    if not separator or value_size <= 0:
        return _split_text(field, size, overlap)
    return [f"{prefix}{part}" for part in _split_text(value, value_size, overlap)]


def _split_table_row(row: str, chunk_size: int, overlap: int) -> list[str]:
    if len(row) <= chunk_size:
        return [row]
    payload_size = chunk_size - len(_TABLE_PREFIX) - 1
    if payload_size < 5:
        return _split_text(row, chunk_size, overlap)
    fields = row[len(_TABLE_PREFIX):].removesuffix("]").split(" | ")
    anchor_size = max(1, payload_size // 3)
    anchor = fields[0]
    if len(anchor) > anchor_size:
        anchor = f"{anchor[:anchor_size - 1]}…"

    fragments = []
    current: list[str] = []
    for index, field in enumerate(fields):
        field_size = payload_size if index == 0 else payload_size - len(anchor) - 3
        for piece in _split_labelled_field(field, max(1, field_size), overlap):
            if len(" | ".join([*current, piece])) > payload_size:
                fragments.append(f"{_TABLE_PREFIX}{' | '.join(current)}]")
                current = []
            if not current and index > 0:
                current.append(anchor)
            current.append(piece)
    if current:
        fragments.append(f"{_TABLE_PREFIX}{' | '.join(current)}]")
    return fragments


def chunk_pages(
    pages: list[tuple[int, str]],
    chunk_size: int = 800,
    overlap: int = 150,
    overlap_size: int | None = None,
) -> list[dict[str, Any]]:
    if overlap_size is not None:
        overlap = overlap_size
    if chunk_size <= 0 or not 0 <= overlap < chunk_size:
        raise ValueError("chunk_size must be positive and overlap must be between 0 and chunk_size - 1")
    chunks: list[dict[str, Any]] = []
    chunk_index = 0

    for page_num, text in pages:
        stripped = text.strip()
        if not stripped:
            continue

        raw_lines = stripped.split("\n")
        blocks: list[str] = []
        for line in raw_lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith(_TABLE_PREFIX):
                blocks.extend(_split_table_row(line, chunk_size, overlap))
            elif len(line) <= chunk_size:
                blocks.append(line)
            else:
                blocks.extend(_split_labelled_field(line, chunk_size, overlap))

        current_blocks: list[str] = []
        current_len = 0
        for block in blocks:
            block_len = len(block) + (1 if current_blocks else 0)
            if current_blocks and current_len + block_len > chunk_size:
                chunks.append(
                    {
                        "page_number": page_num,
                        "chunk_index": chunk_index,
                        "content": "\n".join(current_blocks),
                    }
                )
                chunk_index += 1

                overlap_blocks: list[str] = []
                overlap_len = 0
                for prev in reversed(current_blocks):
                    prev_len = len(prev) + (1 if overlap_blocks else 0)
                    if overlap_len + prev_len <= overlap:
                        overlap_blocks.insert(0, prev)
                        overlap_len += prev_len
                    else:
                        break

                if overlap_len + len(block) + bool(overlap_blocks) > chunk_size:
                    current_blocks = []
                    current_len = 0
                else:
                    current_blocks = overlap_blocks
                    current_len = overlap_len

            current_len += len(block) + (1 if current_blocks else 0)
            current_blocks.append(block)

        if current_blocks:
            chunks.append(
                {
                    "page_number": page_num,
                    "chunk_index": chunk_index,
                    "content": "\n".join(current_blocks),
                }
            )
            chunk_index += 1

    return chunks


async def get_embedding(text: str) -> np.ndarray:
    embeddings = await get_embeddings([text])
    return embeddings[0]


async def _request_embeddings(batch_texts: list[str]) -> Any:
    for attempt in range(_EMBEDDING_ATTEMPTS):
        try:
            return await asyncio.wait_for(
                client.aio.models.embed_content(
                    model=EMBEDDING_MODEL,
                    contents=batch_texts,
                    config={"output_dimensionality": EMBEDDING_DIM},
                ),
                timeout=20.0,
            )
        except (errors.APIError, errors.ClientError, TimeoutError, OSError, TransportError, AiohttpClientError) as exc:
            code = getattr(exc, "code", None)
            retryable = isinstance(exc, (TimeoutError, OSError, TransportError, AiohttpClientError)) or (
                isinstance(code, int) and (code in (408, 429) or 500 <= code < 600)
            )
            if not retryable or attempt == _EMBEDDING_ATTEMPTS - 1:
                logger.error("Embedding request failed: model=%s, attempt=%d: %s", EMBEDDING_MODEL, attempt + 1, exc)
                raise EmbeddingError(f"Embedding request failed for {EMBEDDING_MODEL}: {exc}") from exc
            delay = _EMBEDDING_RETRY_DELAY * 2 ** attempt
            logger.warning(
                "Embedding request failed: model=%s, attempt=%d/%d; retrying the same model in %.1fs: %s",
                EMBEDDING_MODEL, attempt + 1, _EMBEDDING_ATTEMPTS, delay, exc,
            )
            await asyncio.sleep(delay)


def _validate_embeddings(embeddings: list[np.ndarray], expected_count: int, context: str) -> None:
    if len(embeddings) != expected_count:
        message = (
            f"{context}: embedding count mismatch for {EMBEDDING_MODEL}: "
            f"expected {expected_count}, got {len(embeddings)}"
        )
        logger.error(message)
        raise EmbeddingError(message)
    for index, vector in enumerate(embeddings):
        if vector.ndim != 1 or vector.size != EMBEDDING_DIM:
            message = (
                f"{context}: embedding dimension mismatch for {EMBEDDING_MODEL}, vector {index}: "
                f"expected ({EMBEDDING_DIM},), got {vector.shape}"
            )
            logger.error(message)
            raise EmbeddingError(message)
        if not np.isfinite(vector).all():
            message = f"{context}: non-finite embedding values for {EMBEDDING_MODEL}, vector {index}"
            logger.error(message)
            raise EmbeddingError(message)


async def get_embeddings(texts: list[str], batch_size: int = 32) -> list[np.ndarray]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if not texts:
        return []

    results: list[np.ndarray] = []
    for i in range(0, len(texts), batch_size):
        batch_texts = texts[i : i + batch_size]
        res = await _request_embeddings(batch_texts)
        raw_embeddings = getattr(res, "embeddings", None)
        if raw_embeddings is None:
            embedding = getattr(res, "embedding", None)
            raw_embeddings = [embedding] if embedding is not None else []
        try:
            batch_embeddings = [np.asarray(embedding.values, dtype=np.float32) for embedding in raw_embeddings]
        except (AttributeError, TypeError, ValueError) as exc:
            logger.error("Invalid embedding response: model=%s, batch offset=%d: %s", EMBEDDING_MODEL, i, exc)
            raise EmbeddingError(f"Invalid embedding response for {EMBEDDING_MODEL}, batch offset {i}") from exc
        _validate_embeddings(batch_embeddings, len(batch_texts), f"Batch offset {i}")
        results.extend(batch_embeddings)

    return results


async def index_document(filename: str, file_bytes: bytes) -> dict[str, Any]:
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".pdf":
        file_type = "pdf"
        pages = await asyncio.to_thread(extract_pdf_pages, file_bytes)
    elif ext == ".docx":
        file_type = "docx"
        pages = await asyncio.to_thread(extract_docx_pages, file_bytes)
    else:
        raise ValueError(f"Unsupported file extension: {ext}")

    if not pages:
        raise ValueError(f"No text extracted from document: {filename}")

    chunks = await asyncio.to_thread(chunk_pages, pages)
    if not chunks:
        raise ValueError(f"No chunks produced from document: {filename}")

    texts = [c["content"] for c in chunks]
    embeddings = await get_embeddings(texts)
    _validate_embeddings(embeddings, len(chunks), f"Document {filename}")
    for c, emb in zip(chunks, embeddings, strict=True):
        c["embedding"] = emb

    return await upsert_document(
        filename, file_type, chunks,
        embedding_model=EMBEDDING_MODEL,
        embedding_dim=int(embeddings[0].size),
    )


async def search_knowledge_base(
    query: str,
    top_k: int = 4,
    min_score: float = 0.52,
) -> list[dict[str, Any]]:
    chunks = await get_all_chunks_with_metadata()
    compatible_chunks = []
    for chunk in chunks:
        vector = chunk["embedding"]
        if (
            chunk.get("embedding_model") != EMBEDDING_MODEL
            or chunk.get("embedding_dim") != EMBEDDING_DIM
            or vector.ndim != 1
            or vector.size != EMBEDDING_DIM
            or not np.isfinite(vector).all()
        ):
            logger.warning(
                "Skipping incompatible chunk: file=%s, chunk=%s, model=%s, dim=%s, shape=%s; "
                "expected model=%s, dim=%d. Reindex the document.",
                chunk["filename"], chunk.get("chunk_index"), chunk.get("embedding_model"),
                chunk.get("embedding_dim"), vector.shape, EMBEDDING_MODEL, EMBEDDING_DIM,
            )
            continue
        compatible_chunks.append(chunk)
    chunks = compatible_chunks
    if not chunks:
        return []

    query_emb = await get_embedding(query)
    _validate_embeddings([query_emb], 1, "Query")
    q_norm = np.linalg.norm(query_emb)
    if q_norm == 0:
        return []

    chunk_embs = np.stack([c["embedding"] for c in chunks])
    chunk_norms = np.linalg.norm(chunk_embs, axis=1) * q_norm
    chunk_norms[chunk_norms == 0] = 1e-9

    sims = np.dot(chunk_embs, query_emb) / chunk_norms
    results: list[dict[str, Any]] = []

    for idx, sim in enumerate(sims):
        score = float(sim)
        if score >= min_score:
            results.append(
                {
                    "content": chunks[idx]["content"],
                    "filename": chunks[idx]["filename"],
                    "page_number": chunks[idx]["page_number"],
                    "score": score,
                }
            )

    results.sort(key=lambda x: x["score"], reverse=True)
    if not results:
        return []

    top_score = results[0]["score"]
    cutoff = max(min_score, top_score - 0.08)
    filtered = [r for r in results if r["score"] >= cutoff]
    return filtered[:top_k]

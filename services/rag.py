import asyncio
import io
import logging
import os
from typing import Any

import numpy as np
import pdfplumber
import pdfplumber.utils
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from google.genai import errors

from services.db import get_all_chunks_with_metadata, upsert_document
from services.llm import client

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "gemini-embedding-001"
FALLBACK_EMBEDDING_MODEL = "gemini-embedding-2"
EMBEDDING_MODELS = (EMBEDDING_MODEL, FALLBACK_EMBEDDING_MODEL)


def _format_table_row(headers: list[str], row: list[Any]) -> str:
    cells: list[str] = []
    for i, cell in enumerate(row):
        col = headers[i] if i < len(headers) else f"Col_{i + 1}"
        val = str(cell).strip().replace("\r", " ").replace("\n", " ") if cell is not None else ""
        cells.append(f"{col}: {val}")
    return f"[Table Row | {' | '.join(cells)}]"


def extract_pdf_pages(file_bytes: bytes) -> list[tuple[int, str]]:
    pages_data: list[tuple[int, str]] = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for idx, page in enumerate(pdf.pages):
            page_num = idx + 1
            parts: list[str] = []

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

            for tbl in found_tables:
                table_data = tbl.extract() if hasattr(tbl, "extract") else []
                if not table_data or len(table_data) < 2:
                    continue
                headers = [
                    str(c).strip().replace("\r", " ").replace("\n", " ") if c else f"Col_{i + 1}"
                    for i, c in enumerate(table_data[0])
                ]
                for row in table_data[1:]:
                    if row and any(row):
                        parts.append(_format_table_row(headers, row))

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
            if table.rows and len(table.rows) >= 2:
                headers = [
                    c.text.strip().replace("\r", " ").replace("\n", " ") for c in table.rows[0].cells
                ]
                for row in table.rows[1:]:
                    row_vals = [c.text.strip().replace("\r", " ").replace("\n", " ") for c in row.cells]
                    if any(row_vals):
                        element_lines.append(_format_table_row(headers, row_vals))

        for line in element_lines:
            line_len = len(line)
            if chars_on_page + line_len > 1800 and chars_on_page > 0:
                current_page += 1
                chars_on_page = 0

            pages.setdefault(current_page, []).append(line)
            chars_on_page += line_len

    return [(pg, "\n".join(lines)) for pg, lines in sorted(pages.items()) if lines]


def chunk_pages(
    pages: list[tuple[int, str]],
    chunk_size: int = 800,
    overlap: int = 150,
    overlap_size: int | None = None,
) -> list[dict[str, Any]]:
    if overlap_size is not None:
        overlap = overlap_size
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
            if line.startswith("[Table Row |") or len(line) <= chunk_size:
                blocks.append(line)
            else:
                for start in range(0, len(line), chunk_size - overlap):
                    sub = line[start : start + chunk_size].strip()
                    if sub:
                        blocks.append(sub)

        current_blocks: list[str] = []
        current_len = 0
        i = 0

        while i < len(blocks):
            block = blocks[i]
            block_len = len(block) + (1 if current_blocks else 0)

            if current_len + block_len <= chunk_size or not current_blocks:
                current_blocks.append(block)
                current_len += block_len
                i += 1
            else:
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

                if overlap_len + len(blocks[i]) > chunk_size:
                    current_blocks = []
                    current_len = 0
                else:
                    current_blocks = overlap_blocks
                    current_len = overlap_len

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
    try:
        res = await client.aio.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=text,
        )
    except (errors.APIError, errors.ClientError, TimeoutError):
        res = await client.aio.models.embed_content(
            model=FALLBACK_EMBEDDING_MODEL,
            contents=text,
        )
    values = (
        res.embedding.values
        if getattr(res, "embedding", None)
        else res.embeddings[0].values
    )
    return np.array(values, dtype=np.float32)


async def get_embeddings(texts: list[str], batch_size: int = 32) -> list[np.ndarray]:
    if not texts:
        return []

    results: list[np.ndarray] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        try:
            res = await client.aio.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=batch,
            )
        except (errors.APIError, errors.ClientError, TimeoutError):
            res = await client.aio.models.embed_content(
                model=FALLBACK_EMBEDDING_MODEL,
                contents=batch,
            )

        if getattr(res, "embeddings", None):
            batch_embs = [np.array(e.values, dtype=np.float32) for e in res.embeddings]
        else:
            batch_embs = [np.array(res.embedding.values, dtype=np.float32)]
        results.extend(batch_embs)

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
    for c, emb in zip(chunks, embeddings):
        c["embedding"] = emb

    return await upsert_document(filename, file_type, chunks)


async def search_knowledge_base(
    query: str,
    top_k: int = 4,
    min_score: float = 0.52,
) -> list[dict[str, Any]]:
    chunks = await get_all_chunks_with_metadata()
    if not chunks:
        return []

    query_emb = await get_embedding(query)
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
    return results[:top_k]

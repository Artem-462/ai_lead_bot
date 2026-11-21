import importlib.util
import io
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import numpy as np
import pytest
from docx import Document
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement
from sqlalchemy import select

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

_db_path = os.path.join(PROJECT_ROOT, "services", "db.py")
_db_spec = importlib.util.spec_from_file_location("services.db.real", _db_path)
real_db = importlib.util.module_from_spec(_db_spec)
_db_spec.loader.exec_module(real_db)

_rag_path = os.path.join(PROJECT_ROOT, "services", "rag.py")
_rag_spec = importlib.util.spec_from_file_location("services.rag", _rag_path)
rag = importlib.util.module_from_spec(_rag_spec)
sys.modules["services.rag"] = rag
_rag_spec.loader.exec_module(rag)

_llm_path = os.path.join(PROJECT_ROOT, "services", "llm.py")
_llm_spec = importlib.util.spec_from_file_location("services.llm.real", _llm_path)
real_llm = importlib.util.module_from_spec(_llm_spec)
_llm_spec.loader.exec_module(real_llm)

import handlers.admin as admin_handler
from i18n import t
from services.models import Document as StoredDocument
from tests.test_handlers import make_message


def embedding_response(vectors):
    return SimpleNamespace(embeddings=[SimpleNamespace(values=vector) for vector in vectors])


def pdf_with_tables(table_pages):
    pages = []
    for tables_data in table_pages:
        page = MagicMock()
        tables = []
        for data in tables_data:
            table = MagicMock()
            table.bbox = (10, 10, 100, 100)
            table.extract.return_value = data
            tables.append(table)
        page.find_tables.return_value = tables
        page.extract_text.return_value = None
        page.filter.return_value.extract_text.return_value = None
        pages.append(page)
    pdf = MagicMock()
    pdf.pages = pages
    pdf.__enter__.return_value = pdf
    return pdf


class TestPdfExtraction:
    def test_extract_pdf_pages_with_tables_and_text(self):
        page1 = MagicMock()
        table1 = MagicMock()
        table1.bbox = (10, 10, 100, 100)
        table1.extract.return_value = [
            ["Service", "Price"], ["Telegram Bot", "$300"], ["Support Assistant", "$500"],
        ]
        page1.find_tables.return_value = [table1]
        filtered_page1 = MagicMock()
        filtered_page1.extract_text.return_value = "Overview of developer services."
        page1.filter.return_value = filtered_page1
        page1.extract_text.return_value = "Overview of developer services."

        page2 = MagicMock()
        page2.find_tables.return_value = []
        page2.extract_text.return_value = "Terms and conditions on page 2."

        fake_pdf = MagicMock()
        fake_pdf.pages = [page1, page2]
        fake_pdf.__enter__.return_value = fake_pdf
        fake_pdf.__exit__.return_value = None

        with patch("pdfplumber.open", return_value=fake_pdf):
            pages = rag.extract_pdf_pages(b"%PDF-mock")

        assert len(pages) == 2
        p1_num, p1_text = pages[0]
        assert p1_num == 1
        assert "Overview of developer services." in p1_text
        assert "[Table Row | Service: Telegram Bot | Price: $300]" in p1_text
        assert "[Table Row | Service: Support Assistant | Price: $500]" in p1_text

        p2_num, p2_text = pages[1]
        assert p2_num == 2
        assert "Terms and conditions on page 2." in p2_text

    def test_extract_pdf_pages_empty_scanned_pdf(self):
        page1 = MagicMock()
        page1.find_tables.return_value = []
        page1.extract_text.return_value = None

        fake_pdf = MagicMock()
        fake_pdf.pages = [page1]
        fake_pdf.__enter__.return_value = fake_pdf
        fake_pdf.__exit__.return_value = None

        with patch("pdfplumber.open", return_value=fake_pdf):
            pages = rag.extract_pdf_pages(b"%PDF-scanned")

        assert pages == []

    def test_format_table_row_multiline_and_carriage_return(self):
        headers = ["Col 1", "Col 2"]
        row = ["Line 1\r\nLine 2", "Text\nwith\rbreaks"]
        formatted = rag._format_table_row(headers, row)
        assert "\n" not in formatted
        assert "\r" not in formatted
        assert "[Table Row | Col 1: Line 1  Line 2 | Col 2: Text with breaks]" in formatted

    @pytest.mark.parametrize("continued_rows", [
        [["Support", "$50"]],
        [["Support", "$50"], ["Hosting", "$20"]],
        [["Service", "Price"], ["Support", "$50"]],
    ])
    def test_table_header_continues_on_next_page(self, continued_rows):
        pdf = pdf_with_tables([
            [[["Service", "Price"], ["Telegram Bot", "$300"]]],
            [continued_rows],
        ])
        with patch("pdfplumber.open", return_value=pdf):
            pages = rag.extract_pdf_pages(b"pdf")
        assert pages[1][0] == 2
        assert "[Table Row | Service: Support | Price: $50]" in pages[1][1]
        assert "Service: Service" not in pages[1][1]
        if ["Hosting", "$20"] in continued_rows:
            assert "[Table Row | Service: Hosting | Price: $20]" in pages[1][1]

    def test_continuation_uses_last_table_on_previous_page(self):
        pdf = pdf_with_tables([
            [
                [["Feature", "Status"], ["Search", "Active"]],
                [["Service", "Price"], ["Bot", "$300"]],
            ],
            [[["Support", "$50"]]],
        ])
        with patch("pdfplumber.open", return_value=pdf):
            pages = rag.extract_pdf_pages(b"pdf")
        assert pages[1][1] == "[Table Row | Service: Support | Price: $50]"

    def test_new_header_is_not_replaced_by_previous_header(self):
        pdf = pdf_with_tables([
            [[["Service", "Price"], ["Bot", "$300"]]],
            [[["Feature", "Status"], ["Search", "Active"]]],
        ])
        with patch("pdfplumber.open", return_value=pdf):
            pages = rag.extract_pdf_pages(b"pdf")
        assert pages[1][1] == "[Table Row | Feature: Search | Status: Active]"

    def test_header_does_not_carry_over_a_page_without_tables(self):
        pdf = pdf_with_tables([
            [[["Service", "Price"], ["Bot", "$300"]]],
            [],
            [[["Гарантія", "1 тиждень"]]],
        ])
        with patch("pdfplumber.open", return_value=pdf):
            pages = rag.extract_pdf_pages(b"pdf")
        assert pages[1] == (3, "Гарантія: 1 тиждень")

    def test_different_column_count_does_not_inherit_header(self):
        pdf = pdf_with_tables([
            [[["Service", "Price", "Duration"], ["Bot", "$300", "3 days"]]],
            [[["Гарантія", "1 тиждень"]]],
        ])
        with patch("pdfplumber.open", return_value=pdf):
            pages = rag.extract_pdf_pages(b"pdf")
        assert pages[1][1] == "Гарантія: 1 тиждень"

    @pytest.mark.parametrize("rows", [
        [["Гарантія", "1 тиждень"], ["Підтримка", "5$ на місяць"]],
        [["Підтримка", "Активна"], ["Періодичність", "Щомісяця"]],
        [["Передоплата", "50%"]],
    ])
    def test_pdf_key_value_table_preserves_first_data_row(self, rows):
        with patch("pdfplumber.open", return_value=pdf_with_tables([[rows]])):
            pages = rag.extract_pdf_pages(b"pdf")
        assert pages == [(1, "\n\n".join(f"{key}: {value}" for key, value in rows))]
        assert "[Table Row |" not in pages[0][1]

    def test_pdf_key_value_table_with_explicit_header(self):
        rows = [["Параметр", "Значення"], ["Передоплата", "50%"]]
        with patch("pdfplumber.open", return_value=pdf_with_tables([[rows]])):
            pages = rag.extract_pdf_pages(b"pdf")
        assert pages == [(1, "[Table Row | Параметр: Передоплата | Значення: 50%]")]

    def test_pdf_bold_header_with_nonstandard_labels(self):
        pdf = pdf_with_tables([[[["Варіант виконання", "Деталі реалізації"], ["Бот", "Меню"]]]])
        pdf.pages[0].find_tables.return_value[0].rows = [SimpleNamespace(bbox=(10, 10, 100, 20))]
        pdf.pages[0].chars = [
            {"text": "В", "x0": 11, "x1": 12, "top": 11, "bottom": 19, "fontname": "Arial-BoldMT"},
            {"text": "Б", "x0": 11, "x1": 12, "top": 21, "bottom": 29, "fontname": "Arial"},
        ]
        with patch("pdfplumber.open", return_value=pdf):
            pages = rag.extract_pdf_pages(b"pdf")
        assert pages[0][1] == "[Table Row | Варіант виконання: Бот | Деталі реалізації: Меню]"


class TestDocxExtraction:
    def test_docx_xml_page_break_and_tables(self):
        doc = Document()
        doc.add_paragraph("First page content")

        p_break = doc.add_paragraph("Paragraph before break")
        p_break.add_run().add_break(WD_BREAK.PAGE)

        doc.add_paragraph("Second page content")

        table = doc.add_table(rows=2, cols=2)
        table.rows[0].cells[0].text = "Feature"
        table.rows[0].cells[1].text = "Status"
        table.rows[1].cells[0].text = "RAG Engine"
        table.rows[1].cells[1].text = "Active"

        p_rendered = doc.add_paragraph()
        run_rendered = p_rendered.add_run()
        rendered_break = OxmlElement("w:lastRenderedPageBreak")
        run_rendered._r.append(rendered_break)
        doc.add_paragraph("Third page content")

        buf = io.BytesIO()
        doc.save(buf)

        pages = rag.extract_docx_pages(buf.getvalue())
        assert len(pages) >= 2
        all_text = " ".join(p[1] for p in pages)
        assert "First page content" in all_text
        assert "Second page content" in all_text
        assert "[Table Row | Feature: RAG Engine | Status: Active]" in all_text

    def test_docx_fallback_pagination_1800_chars(self):
        doc = Document()
        for _ in range(30):
            doc.add_paragraph("A" * 90)

        buf = io.BytesIO()
        doc.save(buf)

        pages = rag.extract_docx_pages(buf.getvalue())
        assert len(pages) >= 2
        assert pages[0][0] == 1
        assert pages[1][0] == 2

    @pytest.mark.parametrize("rows", [
        [["Гарантія", "1 тиждень"], ["Підтримка", "5$ на місяць"]],
        [["Підтримка", "Активна"], ["Періодичність", "Щомісяця"]],
        [["Передоплата", "50%"]],
    ])
    def test_docx_key_value_table_preserves_first_data_row(self, rows):
        doc = Document()
        table = doc.add_table(rows=len(rows), cols=2)
        for table_row, values in zip(table.rows, rows, strict=True):
            for cell, value in zip(table_row.cells, values, strict=True):
                cell.text = value
        buf = io.BytesIO()
        doc.save(buf)
        pages = rag.extract_docx_pages(buf.getvalue())
        assert pages == [(1, "\n".join(f"{key}: {value}" for key, value in rows))]

    @pytest.mark.parametrize("header_style", ["repeat", "bold"])
    def test_docx_explicit_header_with_nonstandard_labels(self, header_style):
        doc = Document()
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "Варіант виконання"
        table.cell(0, 1).text = "Деталі реалізації"
        table.cell(1, 0).text = "Бот"
        table.cell(1, 1).text = "Меню"
        if header_style == "repeat":
            table.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
        else:
            for cell in table.rows[0].cells:
                cell.paragraphs[0].runs[0].bold = True
        buf = io.BytesIO()
        doc.save(buf)
        pages = rag.extract_docx_pages(buf.getvalue())
        assert pages == [(1, "[Table Row | Варіант виконання: Бот | Деталі реалізації: Меню]")]

class TestChunkPages:
    def test_chunking_preserves_table_rows_and_metadata(self):
        pages = [
            (
                1,
                (
                    "Introduction line.\n"
                    "[Table Row | Service: Web Bot | Price: $200]\n"
                    "[Table Row | Service: Voice Bot | Price: $400]\n"
                    "Conclusion line."
                ),
            )
        ]
        chunks = rag.chunk_pages(pages, chunk_size=300, overlap=50)
        assert len(chunks) >= 1
        for c in chunks:
            assert c["page_number"] == 1
            assert isinstance(c["chunk_index"], int)
            if "[Table Row |" in c["content"]:
                assert "Service:" in c["content"]
                assert "Price:" in c["content"]

    def test_chunking_wide_table_row_does_not_infinite_loop(self):
        wide_row = "[Table Row | " + "Detail: Data " * 80 + "]"
        pages = [
            (
                1,
                (
                    "Header line before table.\n"
                    "Another preparatory text.\n"
                    f"{wide_row}\n"
                    "Following paragraph text after the wide row."
                ),
            )
        ]
        chunks = rag.chunk_pages(pages, chunk_size=400, overlap=100)
        assert len(chunks) >= 2
        all_content = "\n".join(c["content"] for c in chunks)
        assert all(len(c["content"]) <= 400 for c in chunks)
        assert "[Table Row | Detail:" in all_content
        assert "Data" in all_content
        assert "Following paragraph text after the wide row." in all_content

    def test_chunk_pages_large_table_row_no_infinite_loop(self):
        short_paragraph = "A" * 100
        table_row = "[Table Row | " + "X" * (720 - len("[Table Row | ") - 1) + "]"
        assert len(short_paragraph) == 100
        assert len(table_row) == 720
        pages = [(1, f"{short_paragraph}\n{table_row}")]
        chunks = rag.chunk_pages(pages, chunk_size=800, overlap_size=150)
        assert len(chunks) == 2
        assert chunks[0]["content"] == short_paragraph
        assert chunks[1]["content"] == table_row

    @pytest.mark.parametrize("chunk_size", [100, 200, 400, 800])
    def test_wide_table_fragments_preserve_row_and_column_context(self, chunk_size):
        values = [f"detail{i:03d}" for i in range(200)]
        row = rag._format_table_row(
            ["Service", "Description", "Price", "Warranty"],
            ["Web Bot", " ".join(values), "$300", "1 week"],
        )
        chunks = rag.chunk_pages([(7, row)], chunk_size=chunk_size, overlap=20)
        assert len(chunks) > 1
        for index, chunk in enumerate(chunks):
            assert len(chunk["content"]) <= chunk_size
            assert chunk["page_number"] == 7
            assert chunk["chunk_index"] == index
            for fragment in chunk["content"].splitlines():
                assert fragment.startswith("[Table Row | Service: Web Bot |")
                assert fragment.endswith("]")
                if "detail" in fragment:
                    assert "Description:" in fragment
        content = "\n".join(chunk["content"] for chunk in chunks)
        assert all(value in content for value in values)
        assert "Price: $300" in content
        assert "Warranty: 1 week" in content

    def test_long_single_cell_repeats_column_label(self):
        row = rag._format_table_row(["Description"], ["Long service details " * 150])
        chunks = rag.chunk_pages([(1, row)], chunk_size=200, overlap=20)
        assert len(chunks) > 1
        assert all(len(chunk["content"]) <= 200 for chunk in chunks)
        assert all(chunk["content"].startswith("[Table Row | Description:") for chunk in chunks)

    def test_long_key_value_line_repeats_parameter_name(self):
        chunks = rag.chunk_pages([(2, "Підтримка: " + "Оновлення сервісу " * 150)], chunk_size=200, overlap=20)
        assert len(chunks) > 1
        assert all(len(chunk["content"]) <= 200 for chunk in chunks)
        assert all(chunk["content"].startswith("Підтримка: ") for chunk in chunks)

    def test_overlap_accounts_for_newline_size(self):
        pages = [(1, "a" * 60 + "\n" + "b" * 20 + "\n" + "c" * 80)]
        chunks = rag.chunk_pages(pages, chunk_size=100, overlap=20)
        assert [chunk["content"] for chunk in chunks] == ["a" * 60 + "\n" + "b" * 20, "c" * 80]

    @pytest.mark.parametrize("size,overlap", [(0, 0), (100, -1), (100, 100), (100, 101)])
    def test_invalid_chunk_limits_are_rejected(self, size, overlap):
        with pytest.raises(ValueError, match="chunk_size"):
            rag.chunk_pages([(1, "text")], chunk_size=size, overlap=overlap)


class TestEmbeddingConfiguration:
    def load_config(self, monkeypatch):
        monkeypatch.setenv("BOT_TOKEN", "test-token")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        spec = importlib.util.spec_from_file_location("config.testing", os.path.join(PROJECT_ROOT, "config.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_default_model_and_dimension(self, monkeypatch):
        monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
        monkeypatch.delenv("EMBEDDING_DIM", raising=False)
        module = self.load_config(monkeypatch)
        assert module.EMBEDDING_MODEL == "gemini-embedding-001"
        assert module.EMBEDDING_DIM == 3072

    def test_configured_model_and_dimension(self, monkeypatch):
        monkeypatch.setenv("EMBEDDING_MODEL", " configured-model ")
        monkeypatch.setenv("EMBEDDING_DIM", "768")
        module = self.load_config(monkeypatch)
        assert module.EMBEDDING_MODEL == "configured-model"
        assert module.EMBEDDING_DIM == 768

    @pytest.mark.parametrize("model,dimension", [("", "3072"), ("model", "0"), ("model", "-1"), ("model", "invalid")])
    def test_invalid_embedding_configuration(self, monkeypatch, model, dimension):
        monkeypatch.setenv("EMBEDDING_MODEL", model)
        monkeypatch.setenv("EMBEDDING_DIM", dimension)
        with pytest.raises(ValueError):
            self.load_config(monkeypatch)

    def test_default_history_and_output_token_limits(self, monkeypatch):
        monkeypatch.delenv("MAX_HISTORY_CHARS", raising=False)
        monkeypatch.delenv("MAX_OUTPUT_TOKENS", raising=False)
        module = self.load_config(monkeypatch)
        assert module.MAX_HISTORY_CHARS == 6000
        assert module.MAX_OUTPUT_TOKENS == 1024

    def test_configured_history_and_output_token_limits(self, monkeypatch):
        monkeypatch.setenv("MAX_HISTORY_CHARS", "3500")
        monkeypatch.setenv("MAX_OUTPUT_TOKENS", "512")
        module = self.load_config(monkeypatch)
        assert module.MAX_HISTORY_CHARS == 3500
        assert module.MAX_OUTPUT_TOKENS == 512

    @pytest.mark.parametrize("history_chars,output_tokens", [("0", "1024"), ("-10", "1024"), ("6000", "0"), ("6000", "abc")])
    def test_invalid_token_and_history_limits(self, monkeypatch, history_chars, output_tokens):
        monkeypatch.setenv("MAX_HISTORY_CHARS", history_chars)
        monkeypatch.setenv("MAX_OUTPUT_TOKENS", output_tokens)
        with pytest.raises(ValueError):
            self.load_config(monkeypatch)


class TestEmbeddings:
    @pytest.fixture(autouse=True)
    def embedding_dimension(self, monkeypatch):
        monkeypatch.setattr(rag, "EMBEDDING_DIM", 3)

    def test_embedding_constants(self):
        assert rag.EMBEDDING_MODEL == "gemini-embedding-001"
        assert not hasattr(rag, "FALLBACK_EMBEDDING_MODEL")

    @pytest.mark.parametrize("error", [
        rag.errors.APIError(message="Rate limit", code=429),
        rag.errors.APIError(message="Service unavailable", code=503),
        TimeoutError("Request timed out"),
        OSError("Connection reset"),
        rag.TransportError("Connection failed"),
        rag.AiohttpClientError("Connection failed"),
    ])
    @pytest.mark.asyncio
    async def test_get_embedding_retries_same_model(self, error):
        response = embedding_response([[0.1, 0.2, 0.3]])
        with patch.object(rag.client.aio.models, "embed_content", new_callable=AsyncMock) as embed, \
             patch.object(rag.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            embed.side_effect = [error, response]
            result = await rag.get_embedding("test text")
        assert [item.kwargs["model"] for item in embed.await_args_list] == [rag.EMBEDDING_MODEL] * 2
        assert all(item.kwargs["config"] == {"output_dimensionality": 3} for item in embed.await_args_list)
        sleep.assert_awaited_once_with(1.5)
        assert np.allclose(result, np.array([0.1, 0.2, 0.3], dtype=np.float32))

    @pytest.mark.asyncio
    async def test_batch_embedding_retries_same_model(self):
        response = embedding_response([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
        with patch.object(rag.client.aio.models, "embed_content", new_callable=AsyncMock) as embed, \
             patch.object(rag.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            embed.side_effect = [rag.errors.APIError(message="Unavailable", code=500), response]
            results = await rag.get_embeddings(["text1", "text2"])
        assert len(results) == 2
        assert [item.kwargs["model"] for item in embed.await_args_list] == [rag.EMBEDDING_MODEL] * 2
        assert all(item.kwargs["contents"] == ["text1", "text2"] for item in embed.await_args_list)
        sleep.assert_awaited_once_with(1.5)

    @pytest.mark.parametrize("code", [400, 401, 404])
    @pytest.mark.asyncio
    async def test_permanent_api_error_does_not_retry_or_switch(self, code):
        error = rag.errors.APIError(message="Invalid request", code=code)
        with (
            patch.object(rag.client.aio.models, "embed_content", new_callable=AsyncMock, side_effect=error) as embed,
            patch.object(rag.asyncio, "sleep", new_callable=AsyncMock) as sleep,
            pytest.raises(rag.EmbeddingError, match=rag.EMBEDDING_MODEL) as raised,
        ):
            await rag.get_embedding("text")
        assert raised.value.__cause__ is error
        embed.assert_awaited_once()
        sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_retries_are_bounded(self):
        error = rag.errors.APIError(message="Unavailable", code=503)
        with (
            patch.object(rag.client.aio.models, "embed_content", new_callable=AsyncMock, side_effect=error) as embed,
            patch.object(rag.asyncio, "sleep", new_callable=AsyncMock) as sleep,
            pytest.raises(rag.EmbeddingError),
        ):
            await rag.get_embedding("text")
        assert [item.kwargs["model"] for item in embed.await_args_list] == [rag.EMBEDDING_MODEL] * 3
        assert sleep.await_args_list == [call(1.5), call(3.0)]

    @pytest.mark.parametrize("count", [0, 1, 3])
    @pytest.mark.asyncio
    async def test_batch_embedding_count_mismatch_is_explicit(self, count, caplog):
        response = embedding_response([[0.1, 0.2, 0.3]] * count)
        with (
            patch.object(rag.client.aio.models, "embed_content", new_callable=AsyncMock, return_value=response),
            pytest.raises(rag.EmbeddingError, match=f"count mismatch.*expected 2, got {count}"),
        ):
            await rag.get_embeddings(["text1", "text2"])
        assert "Batch offset 0" in caplog.text
        assert "KeyError" not in caplog.text

    @pytest.mark.asyncio
    async def test_aggregated_batch_vector_is_rejected(self):
        response = SimpleNamespace(embeddings=None, embedding=SimpleNamespace(values=[0.1, 0.2, 0.3]))
        with (
            patch.object(rag.client.aio.models, "embed_content", new_callable=AsyncMock, return_value=response),
            pytest.raises(rag.EmbeddingError, match="count mismatch.*expected 2, got 1"),
        ):
            await rag.get_embeddings(["text1", "text2"])

    @pytest.mark.asyncio
    async def test_missing_embedding_response_is_rejected(self):
        with (
            patch.object(rag.client.aio.models, "embed_content", new_callable=AsyncMock, return_value=SimpleNamespace()),
            pytest.raises(rag.EmbeddingError, match="count mismatch.*expected 1, got 0"),
        ):
            await rag.get_embedding("text")

    @pytest.mark.parametrize("values", [[0.1, 0.2], [[0.1, 0.2, 0.3]], 0.1])
    @pytest.mark.asyncio
    async def test_wrong_embedding_dimension_is_rejected(self, values):
        with (
            patch.object(rag.client.aio.models, "embed_content", new_callable=AsyncMock, return_value=embedding_response([values])),
            pytest.raises(rag.EmbeddingError, match=r"dimension mismatch.*expected \(3,\)"),
        ):
            await rag.get_embedding("text")

    @pytest.mark.asyncio
    async def test_non_finite_vector_is_rejected(self):
        with (
            patch.object(rag.client.aio.models, "embed_content", new_callable=AsyncMock,
                         return_value=embedding_response([[float("nan"), 0.2, 0.3]])),
            pytest.raises(rag.EmbeddingError, match="non-finite"),
        ):
            await rag.get_embedding("text")

    @pytest.mark.asyncio
    async def test_batch_order_and_final_short_batch(self):
        responses = [
            embedding_response([[0, 1, 2], [1, 2, 3]]),
            embedding_response([[2, 3, 4], [3, 4, 5]]),
            embedding_response([[4, 5, 6]]),
        ]
        with patch.object(rag.client.aio.models, "embed_content", new_callable=AsyncMock, side_effect=responses) as embed:
            results = await rag.get_embeddings(["a", "b", "c", "d", "e"], batch_size=2)
        assert [item.kwargs["contents"] for item in embed.await_args_list] == [["a", "b"], ["c", "d"], ["e"]]
        assert [vector.tolist() for vector in results] == [[i, i + 1, i + 2] for i in range(5)]

    @pytest.mark.asyncio
    async def test_incomplete_later_batch_does_not_return_partial_results(self):
        responses = [embedding_response([[0, 1, 2], [1, 2, 3]]), embedding_response([])]
        with (
            patch.object(rag.client.aio.models, "embed_content", new_callable=AsyncMock, side_effect=responses),
            pytest.raises(rag.EmbeddingError, match="Batch offset 2.*expected 1, got 0"),
        ):
            await rag.get_embeddings(["a", "b", "c"], batch_size=2)

    @pytest.mark.asyncio
    async def test_empty_input_does_not_call_api(self):
        with patch.object(rag.client.aio.models, "embed_content", new_callable=AsyncMock) as embed:
            assert await rag.get_embeddings([]) == []
        embed.assert_not_awaited()

    @pytest.mark.parametrize("batch_size", [0, -1])
    @pytest.mark.asyncio
    async def test_invalid_batch_size_is_rejected(self, batch_size):
        with pytest.raises(ValueError, match="batch_size"):
            await rag.get_embeddings(["text"], batch_size=batch_size)


class TestDocumentIndexing:
    @pytest.fixture(autouse=True)
    def embedding_dimension(self, monkeypatch):
        monkeypatch.setattr(rag, "EMBEDDING_DIM", 3)

    @pytest.fixture
    def chunks(self):
        return [
            {"page_number": 1, "chunk_index": 0, "content": "First chunk"},
            {"page_number": 2, "chunk_index": 1, "content": "Second chunk"},
        ]

    @pytest.mark.parametrize("count", [1, 3])
    @pytest.mark.asyncio
    async def test_indexing_validates_count_before_assigning_vectors(self, chunks, count, caplog):
        vectors = [np.array([0.1, 0.2, 0.3], dtype=np.float32)] * count
        with (
            patch.object(rag, "extract_pdf_pages", return_value=[(1, "text")]),
            patch.object(rag, "chunk_pages", return_value=chunks),
            patch.object(rag, "get_embeddings", new_callable=AsyncMock, return_value=vectors),
            patch.object(rag, "upsert_document", new_callable=AsyncMock) as upsert,
            pytest.raises(rag.EmbeddingError, match=f"expected 2, got {count}"),
        ):
            await rag.index_document("guide.pdf", b"pdf")
        upsert.assert_not_awaited()
        assert all("embedding" not in chunk for chunk in chunks)
        assert "Document guide.pdf" in caplog.text

    @pytest.mark.asyncio
    async def test_partial_api_batch_never_reaches_database(self, chunks):
        with (
            patch.object(rag, "extract_pdf_pages", return_value=[(1, "text")]),
            patch.object(rag, "chunk_pages", return_value=chunks),
            patch.object(rag.client.aio.models, "embed_content", new_callable=AsyncMock,
                         return_value=embedding_response([[0.1, 0.2, 0.3]])),
            patch.object(rag, "upsert_document", new_callable=AsyncMock) as upsert,
            pytest.raises(rag.EmbeddingError, match="count mismatch"),
        ):
            await rag.index_document("guide.pdf", b"pdf")
        upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_indexing_validates_dimensions_before_database_write(self, chunks):
        vectors = [np.ones(3, dtype=np.float32), np.ones(2, dtype=np.float32)]
        with (
            patch.object(rag, "extract_pdf_pages", return_value=[(1, "text")]),
            patch.object(rag, "chunk_pages", return_value=chunks),
            patch.object(rag, "get_embeddings", new_callable=AsyncMock, return_value=vectors),
            patch.object(rag, "upsert_document", new_callable=AsyncMock) as upsert,
            pytest.raises(rag.EmbeddingError, match="dimension mismatch"),
        ):
            await rag.index_document("guide.pdf", b"pdf")
        upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_indexing_saves_actual_model_and_dimension(self, chunks, monkeypatch):
        monkeypatch.setattr(rag, "EMBEDDING_MODEL", "configured-embedding-model")
        vectors = [np.ones(3, dtype=np.float32)] * 2
        result = {"is_replaced": False, "chunk_count": 2}
        with patch.object(rag, "extract_docx_pages", return_value=[(1, "text")]), \
             patch.object(rag, "chunk_pages", return_value=chunks), \
             patch.object(rag, "get_embeddings", new_callable=AsyncMock, return_value=vectors), \
             patch.object(rag, "upsert_document", new_callable=AsyncMock, return_value=result) as upsert:
            assert await rag.index_document("guide.docx", b"docx") == result
        upsert.assert_awaited_once_with(
            "guide.docx", "docx", chunks, embedding_model="configured-embedding-model", embedding_dim=3
        )
        assert all(chunk["embedding"] is vector for chunk, vector in zip(chunks, vectors, strict=True))



class TestDbRAGStore:
    @pytest.mark.asyncio
    async def test_upsert_versioning_and_delete(self, monkeypatch, tmp_db):
        monkeypatch.setattr(real_db, "DB_FILE", tmp_db)
        await real_db.init_db()

        sample_emb = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        initial_chunks = [
            {"page_number": 1, "chunk_index": 0, "content": "Chunk 1", "embedding": sample_emb},
            {"page_number": 2, "chunk_index": 1, "content": "Chunk 2", "embedding": sample_emb},
        ]

        res1 = await real_db.upsert_document("catalog.pdf", "pdf", initial_chunks)
        assert res1["is_replaced"] is False
        assert res1["chunk_count"] == 2

        docs = await real_db.list_documents()
        assert len(docs) == 1
        assert docs[0]["filename"] == "catalog.pdf"
        assert docs[0]["chunk_count"] == 2

        all_chunks = await real_db.get_all_chunks_with_metadata()
        assert len(all_chunks) == 2

        updated_chunks = [
            {"page_number": 1, "chunk_index": 0, "content": "New Chunk Only", "embedding": sample_emb}
        ]
        res2 = await real_db.upsert_document("catalog.pdf", "pdf", updated_chunks)
        assert res2["is_replaced"] is True
        assert res2["chunk_count"] == 1

        docs_after = await real_db.list_documents()
        assert len(docs_after) == 1
        assert docs_after[0]["chunk_count"] == 1

        chunks_after = await real_db.get_all_chunks_with_metadata()
        assert len(chunks_after) == 1
        assert chunks_after[0]["content"] == "New Chunk Only"

        del_res = await real_db.delete_document_by_name("catalog.pdf")
        assert del_res is True
        assert await real_db.list_documents() == []
        assert await real_db.get_all_chunks_with_metadata() == []

        del_nonexistent = await real_db.delete_document_by_name("catalog.pdf")
        assert del_nonexistent is False

    @pytest.mark.asyncio
    async def test_upsert_and_delete_case_insensitive_collation(self, monkeypatch, tmp_db):
        monkeypatch.setattr(real_db, "DB_FILE", tmp_db)
        await real_db.init_db()

        sample_emb = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        initial_chunks = [
            {"page_number": 1, "chunk_index": 0, "content": "Version 1", "embedding": sample_emb}
        ]
        res1 = await real_db.upsert_document("Report.pdf", "pdf", initial_chunks)
        assert res1["is_replaced"] is False

        updated_chunks = [
            {"page_number": 1, "chunk_index": 0, "content": "Version 2", "embedding": sample_emb}
        ]
        res2 = await real_db.upsert_document("report.PDF", "pdf", updated_chunks)
        assert res2["is_replaced"] is True

        docs = await real_db.list_documents()
        assert len(docs) == 1

        deleted = await real_db.delete_document_by_name("REPORT.pdf")
        assert deleted is True
        assert await real_db.list_documents() == []

    async def test_saved_metadata_matches_actual_vectors(self, real_db):
        chunk = {"page_number": 1, "chunk_index": 0, "content": "Price", "embedding": [0.1, 0.2, 0.3]}
        await real_db.upsert_document("price.pdf", "pdf", [chunk], embedding_model="configured-model")
        async with real_db.async_session() as session:
            document = (await session.scalars(select(StoredDocument))).one()
        assert document.embedding_model == "configured-model"
        assert document.embedding_dim == 3
        chunks = await real_db.get_all_chunks_with_metadata()
        assert chunks[0]["embedding_model"] == document.embedding_model
        assert chunks[0]["embedding_dim"] == document.embedding_dim
        assert chunks[0]["embedding"].shape == (3,)

    async def test_mismatched_dimension_does_not_replace_document(self, real_db):
        chunk = {"page_number": 1, "chunk_index": 0, "content": "Original", "embedding": [0.1, 0.2, 0.3]}
        await real_db.upsert_document("price.pdf", "pdf", [chunk], embedding_dim=3)
        await real_db.get_all_chunks_with_metadata()
        with pytest.raises(ValueError, match="dimension mismatch.*expected 2, got 3"):
            await real_db.upsert_document("price.pdf", "pdf", [{**chunk, "content": "Replacement"}], embedding_dim=2)
        assert [item["content"] for item in await real_db.get_all_chunks_with_metadata()] == ["Original"]

    async def test_mixed_dimensions_cannot_be_saved(self, real_db):
        chunks = [
            {"page_number": 1, "chunk_index": 0, "content": "First", "embedding": [0.1, 0.2, 0.3]},
            {"page_number": 1, "chunk_index": 1, "content": "Second", "embedding": [0.1, 0.2]},
        ]
        with pytest.raises(ValueError, match="dimension mismatch.*expected 3, got 2"):
            await real_db.upsert_document("price.pdf", "pdf", chunks)
        assert await real_db.list_documents() == []

    @pytest.mark.parametrize("vector", [[], [[0.1, 0.2]], [float("nan"), 0.2]])
    async def test_invalid_vectors_cannot_be_saved(self, real_db, vector):
        chunk = {"page_number": 1, "chunk_index": 0, "content": "Text", "embedding": vector}
        with pytest.raises(ValueError, match="Embedding must be"):
            await real_db.upsert_document("guide.pdf", "pdf", [chunk])
        assert await real_db.has_documents() is False


class TestVectorSearch:
    @pytest.fixture(autouse=True)
    def embedding_dimension(self, monkeypatch):
        monkeypatch.setattr(rag, "EMBEDDING_DIM", 3)
        rag.clear_query_embedding_cache()
        yield
        rag.clear_query_embedding_cache()

    @pytest.mark.asyncio
    async def test_search_knowledge_base_cosine_threshold(self):
        query_vector = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        match_vector = np.array([0.9, 0.1, 0.0], dtype=np.float32)  # High similarity > 0.9
        low_vector = np.array([0.1, 0.9, 0.0], dtype=np.float32)    # Low similarity ~ 0.1

        mock_chunks = [
            {
                "content": "Relevant content",
                "filename": "guide.pdf",
                "page_number": 3,
                "chunk_index": 0,
                "embedding": match_vector,
                "embedding_model": rag.EMBEDDING_MODEL,
                "embedding_dim": 3,
            },
            {
                "content": "Irrelevant content",
                "filename": "other.docx",
                "page_number": 1,
                "chunk_index": 0,
                "embedding": low_vector,
                "embedding_model": rag.EMBEDDING_MODEL,
                "embedding_dim": 3,
            },
        ]

        with patch("services.rag.get_all_chunks_with_metadata", new_callable=AsyncMock, return_value=mock_chunks), \
             patch("services.rag.get_embedding", new_callable=AsyncMock, return_value=query_vector):
            results = await rag.search_knowledge_base("how to configure", top_k=2, min_score=0.52)

        assert len(results) == 1
        assert results[0]["filename"] == "guide.pdf"
        assert results[0]["page_number"] == 3
        assert results[0]["score"] > 0.52

    @pytest.mark.parametrize("metadata,vector", [
        ({"embedding_model": "gemini-embedding-2", "embedding_dim": 3}, [1, 0, 0]),
        ({"embedding_model": "gemini-embedding-001", "embedding_dim": 2}, [1, 0]),
        ({"embedding_model": "gemini-embedding-001", "embedding_dim": 3}, [1, 0]),
        ({"embedding_model": "gemini-embedding-001", "embedding_dim": 3}, [[1, 0, 0]]),
        ({"embedding_model": "gemini-embedding-001", "embedding_dim": 3}, [float("nan"), 0, 0]),
        ({}, [1, 0, 0]),
    ])
    @pytest.mark.asyncio
    async def test_incompatible_chunks_are_excluded_before_cosine_search(self, metadata, vector, caplog):
        compatible = {
            "content": "Current facts", "filename": "current.pdf", "page_number": 2, "chunk_index": 0,
            "embedding": np.array([1, 0, 0], dtype=np.float32),
            "embedding_model": rag.EMBEDDING_MODEL, "embedding_dim": 3,
        }
        incompatible = {
            "content": "Old facts", "filename": "old.pdf", "page_number": 1, "chunk_index": 0,
            "embedding": np.array(vector, dtype=np.float32), **metadata,
        }
        with patch.object(rag, "get_all_chunks_with_metadata", new_callable=AsyncMock,
                          return_value=[incompatible, compatible]), \
             patch.object(rag, "get_embedding", new_callable=AsyncMock, return_value=compatible["embedding"]):
            results = await rag.search_knowledge_base("price")
        assert [result["filename"] for result in results] == ["current.pdf"]
        assert "Skipping incompatible chunk" in caplog.text
        assert "old.pdf" in caplog.text
        assert "Reindex the document" in caplog.text

    @pytest.mark.asyncio
    async def test_incompatible_index_does_not_request_query_embedding(self):
        chunk = {
            "content": "Old facts", "filename": "old.pdf", "page_number": 1, "chunk_index": 0,
            "embedding": np.ones(3, dtype=np.float32), "embedding_model": "other-model", "embedding_dim": 3,
        }
        with patch.object(rag, "get_all_chunks_with_metadata", new_callable=AsyncMock, return_value=[chunk]), \
             patch.object(rag, "get_embedding", new_callable=AsyncMock) as embed:
            assert await rag.search_knowledge_base("price") == []
        embed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_query_dimension_is_validated(self):
        chunk = {
            "content": "Facts", "filename": "price.pdf", "page_number": 1, "chunk_index": 0,
            "embedding": np.ones(3, dtype=np.float32), "embedding_model": rag.EMBEDDING_MODEL, "embedding_dim": 3,
        }
        with (
            patch.object(rag, "get_all_chunks_with_metadata", new_callable=AsyncMock, return_value=[chunk]),
            patch.object(rag, "get_embedding", new_callable=AsyncMock, return_value=np.ones(2, dtype=np.float32)),
            pytest.raises(rag.EmbeddingError, match="Query.*dimension mismatch"),
        ):
            await rag.search_knowledge_base("price")

    @pytest.mark.asyncio
    async def test_empty_index_does_not_request_embedding(self):
        with patch.object(rag, "get_all_chunks_with_metadata", new_callable=AsyncMock, return_value=[]), \
             patch.object(rag, "get_embedding", new_callable=AsyncMock) as embed:
            assert await rag.search_knowledge_base("query") == []
        embed.assert_not_awaited()


class TestVectorStoreAbstraction:
    @pytest.mark.asyncio
    async def test_protocol_implementation_and_caching(self):
        from services.vector_store import SQLiteNumPyVectorStore, VectorStore

        loader_calls = 0

        async def mock_loader():
            nonlocal loader_calls
            loader_calls += 1
            return [
                {
                    "content": "Doc 1 text",
                    "filename": "doc1.pdf",
                    "page_number": 1,
                    "chunk_index": 0,
                    "embedding": np.array([3.0, 4.0, 0.0], dtype=np.float32),
                    "embedding_model": "test-model",
                    "embedding_dim": 3,
                }
            ]

        store = SQLiteNumPyVectorStore(
            loader=mock_loader,
            embedding_model="test-model",
            embedding_dim=3,
        )
        assert isinstance(store, VectorStore)
        assert store.matrix is None

        query = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        results = await store.search(query, top_k=2, min_score=0.5)
        assert len(results) == 1
        assert loader_calls == 1
        assert store.matrix is not None
        np.testing.assert_allclose(store.matrix[0], [0.6, 0.8, 0.0], rtol=1e-5)
        assert results[0]["score"] == pytest.approx(0.6, rel=1e-3)

        results2 = await store.search(query, top_k=2, min_score=0.5)
        assert len(results2) == 1
        assert loader_calls == 1

        store.invalidate()
        assert store.matrix is None
        assert store.is_empty is False

        results3 = await store.search(query, top_k=2, min_score=0.5)
        assert len(results3) == 1
        assert loader_calls == 2

    @pytest.mark.asyncio
    async def test_search_score_window_and_top_k(self):
        from services.vector_store import SQLiteNumPyVectorStore

        chunks = [
            {
                "content": "First",
                "filename": "doc1.pdf",
                "page_number": 1,
                "chunk_index": 0,
                "embedding": np.array([1.0, 0.0, 0.0], dtype=np.float32),
                "embedding_model": "m",
                "embedding_dim": 3,
            },
            {
                "content": "Second",
                "filename": "doc2.pdf",
                "page_number": 1,
                "chunk_index": 0,
                "embedding": np.array([0.95, 0.05, 0.0], dtype=np.float32),
                "embedding_model": "m",
                "embedding_dim": 3,
            },
            {
                "content": "Third",
                "filename": "doc3.pdf",
                "page_number": 1,
                "chunk_index": 0,
                "embedding": np.array([0.8, 0.2, 0.0], dtype=np.float32),
                "embedding_model": "m",
                "embedding_dim": 3,
            },
        ]
        store = SQLiteNumPyVectorStore(
            loader=AsyncMock(return_value=chunks),
            embedding_model="m",
            embedding_dim=3,
        )
        query = np.array([1.0, 0.0, 0.0], dtype=np.float32)

        res = await store.search(query, top_k=1, min_score=0.5, score_window=0.08)
        assert len(res) == 1
        assert res[0]["content"] == "First"

        res_window = await store.search(query, top_k=5, min_score=0.5, score_window=0.02)
        assert [item["content"] for item in res_window] == ["First", "Second"]

        res_all = await store.search(query, top_k=5, min_score=0.5, score_window=0.5)
        assert len(res_all) == 3


class TestQueryEmbeddingCache:
    @pytest.fixture(autouse=True)
    def setup_cache(self, monkeypatch):
        monkeypatch.setattr(rag, "EMBEDDING_DIM", 3)
        rag.clear_query_embedding_cache()
        yield
        rag.clear_query_embedding_cache()

    @pytest.mark.asyncio
    async def test_repeated_query_uses_cache_without_calling_api(self):
        chunk = {
            "content": "Tariff Standard is $300",
            "filename": "price.pdf",
            "page_number": 1,
            "chunk_index": 0,
            "embedding": np.array([1.0, 0.0, 0.0], dtype=np.float32),
            "embedding_model": rag.EMBEDDING_MODEL,
            "embedding_dim": 3,
        }
        response = embedding_response([[1.0, 0.0, 0.0]])
        with (
            patch.object(rag, "get_all_chunks_with_metadata", new_callable=AsyncMock, return_value=[chunk]),
            patch.object(rag.client.aio.models, "embed_content", new_callable=AsyncMock, return_value=response) as embed,
        ):
            first = await rag.search_knowledge_base("Яка ціна бота?")
            second = await rag.search_knowledge_base("  яка   ціна бота? ")

        assert len(first) == 1
        assert len(second) == 1
        embed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cache_evicts_least_recently_used_entry_over_limit(self):
        vector = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        with patch.object(rag, "get_embedding", new_callable=AsyncMock, return_value=vector) as embed:
            for i in range(128):
                await rag.get_query_embedding(f"query {i}")
            assert len(rag._query_embedding_cache) == 128
            assert embed.await_count == 128

            await rag.get_query_embedding("query 0")
            assert embed.await_count == 128

            await rag.get_query_embedding("query 128")
            assert len(rag._query_embedding_cache) == 128
            assert embed.await_count == 129

            await rag.get_query_embedding("query 0")
            assert embed.await_count == 129

            await rag.get_query_embedding("query 1")
            assert embed.await_count == 130

    @pytest.mark.asyncio
    async def test_cache_cleared_on_document_mutations(self, real_db):
        vector = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        chunk = {"page_number": 1, "chunk_index": 0, "content": "Price $300", "embedding": vector}

        with patch.object(rag, "get_embedding", new_callable=AsyncMock, return_value=vector) as embed:
            await rag.get_query_embedding("price")
            assert len(rag._query_embedding_cache) == 1
            assert embed.await_count == 1

            await real_db.upsert_document("price.pdf", "pdf", [chunk], embedding_dim=3)
            assert len(rag._query_embedding_cache) == 0

            await rag.get_query_embedding("price")
            assert embed.await_count == 2

            await real_db.delete_document_by_name("price.pdf")
            assert len(rag._query_embedding_cache) == 0

            await real_db.upsert_document("guide.pdf", "pdf", [chunk], embedding_dim=3)
            doc_id = (await real_db.list_documents())[0]["id"]
            await rag.get_query_embedding("guide")
            assert len(rag._query_embedding_cache) == 1

            await real_db.delete_document_by_id(doc_id)
            assert len(rag._query_embedding_cache) == 0

            await real_db.upsert_document("faq.pdf", "pdf", [chunk], embedding_dim=3)
            await rag.get_query_embedding("faq")
            assert len(rag._query_embedding_cache) == 1

            await real_db.clear_all_documents()
            assert len(rag._query_embedding_cache) == 0


class TestRagAnswers:
    @pytest.mark.asyncio
    async def test_missing_facts_in_both_sources_return_refusal(self):
        with patch.object(real_llm, "has_documents", new_callable=AsyncMock, return_value=True), \
             patch("services.rag.search_knowledge_base", new_callable=AsyncMock, return_value=[]), \
             patch.object(real_llm, "_call_model", new_callable=AsyncMock, return_value="NO_RAG_ANSWER") as model:
            res_uk = await real_llm.get_ai_answer(123, "What is the secret?", "uk")
            res_en = await real_llm.get_ai_answer(123, "What is the secret?", "en")

        assert res_uk == t("rag_no_answer", "uk")
        assert res_en == t("rag_no_answer", "en")
        assert model.await_count == 2

    @pytest.mark.asyncio
    async def test_rag_refusal_when_model_outputs_no_rag_answer(self):
        chunks = [{"content": "About prices", "filename": "price.pdf", "page_number": 1}]
        with patch.object(real_llm, "has_documents", new_callable=AsyncMock, return_value=True), \
             patch("services.rag.search_knowledge_base", new_callable=AsyncMock, return_value=chunks), \
             patch.object(real_llm, "_call_model", new_callable=AsyncMock, return_value="NO_RAG_ANSWER"):
            res = await real_llm.get_ai_answer(123, "Tell me a joke", "uk")

        assert res == t("rag_no_answer", "uk")

    @pytest.mark.asyncio
    async def test_rag_successful_answer_with_deterministic_footer(self):
        chunks = [
            {"content": "Bot cost is $300", "filename": "price.pdf", "page_number": 2},
            {"content": "Support is $50/mo", "filename": "price.pdf", "page_number": 3},
        ]
        with patch.object(real_llm, "has_documents", new_callable=AsyncMock, return_value=True), \
             patch("services.rag.search_knowledge_base", new_callable=AsyncMock, return_value=chunks), \
             patch.object(real_llm, "_call_model", new_callable=AsyncMock, return_value="Ціна бота від $300."):
            res_uk = await real_llm.get_ai_answer(123, "Яка ціна бота?", "uk")

        assert "Ціна бота від $300." in res_uk
        assert "📄 Джерело: price.pdf (стор. 2, 3)" in res_uk

        with patch.object(real_llm, "has_documents", new_callable=AsyncMock, return_value=True), \
             patch("services.rag.search_knowledge_base", new_callable=AsyncMock, return_value=chunks), \
             patch.object(real_llm, "_call_model", new_callable=AsyncMock, return_value="Bot price starts at $300."):
            res_en = await real_llm.get_ai_answer(123, "What is the bot price?", "en")

        assert "Bot price starts at $300." in res_en
        assert "📄 Source: price.pdf (p. 2, 3)" in res_en



class TestAdminRagHandlers:
    @pytest.mark.asyncio
    async def test_cmd_docs_non_admin_forbidden(self):
        msg = make_message(text="/docs", user_id=111)
        with patch("config.ADMIN_ID", 999):
            await admin_handler.cmd_docs(msg)
        assert msg.answer.await_count == 1
        assert msg.answer.call_args[0][0] == t("stats_no_access", "uk")

    @pytest.mark.asyncio
    async def test_cmd_docs_empty_and_populated(self):
        msg = make_message(text="/docs", user_id=999)
        with patch("config.ADMIN_ID", 999), \
             patch("handlers.admin.list_documents", new_callable=AsyncMock, return_value=[]):
            await admin_handler.cmd_docs(msg)
        assert msg.answer.call_args[0][0] == t("doc_list_empty", "uk")

        msg2 = make_message(text="/docs", user_id=999)
        docs = [{"filename": "info.pdf", "file_type": "pdf", "chunk_count": 5, "uploaded_at": "2026-10-20"}]
        with patch("config.ADMIN_ID", 999), \
             patch("handlers.admin.list_documents", new_callable=AsyncMock, return_value=docs):
            await admin_handler.cmd_docs(msg2)
        assert "info.pdf" in msg2.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_cmd_del_doc(self):
        msg = make_message(text="/del_doc guide.pdf", user_id=999)
        with patch("config.ADMIN_ID", 999), \
             patch("handlers.admin.delete_document_by_name", new_callable=AsyncMock, return_value=True):
            await admin_handler.cmd_del_doc(msg)
        assert t("doc_deleted", "uk", filename="guide.pdf") in msg.answer.call_args[0][0]

        msg_fail = make_message(text="/del_doc nonexistent.pdf", user_id=999)
        with patch("config.ADMIN_ID", 999), \
             patch("handlers.admin.delete_document_by_name", new_callable=AsyncMock, return_value=False):
            await admin_handler.cmd_del_doc(msg_fail)
        assert t("doc_not_found", "uk", filename="nonexistent.pdf") in msg_fail.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_handle_document_upload(self):
        msg_non_admin = make_message(user_id=111)
        msg_non_admin.document = MagicMock()
        msg_non_admin.document.file_name = "test.pdf"
        with patch("config.ADMIN_ID", 999):
            await admin_handler.handle_document_upload(msg_non_admin)
        assert msg_non_admin.answer.call_args[0][0] == t("non_text", "uk")

        msg_unsupported = make_message(user_id=999)
        msg_unsupported.document = MagicMock()
        msg_unsupported.document.file_name = "script.exe"
        with patch("config.ADMIN_ID", 999):
            await admin_handler.handle_document_upload(msg_unsupported)
        assert msg_unsupported.answer.call_args[0][0] == t("doc_unsupported", "uk")

        msg_pdf = make_message(user_id=999)
        msg_pdf.document = MagicMock()
        msg_pdf.document.file_name = "pricing.pdf"
        wait_msg = AsyncMock()
        msg_pdf.answer = AsyncMock(return_value=wait_msg)
        msg_pdf.bot.download = AsyncMock(return_value=io.BytesIO(b"dummy pdf bytes"))

        with patch("config.ADMIN_ID", 999), \
             patch("handlers.admin.index_document", new_callable=AsyncMock, return_value={"is_replaced": False, "chunk_count": 4}):
            await admin_handler.handle_document_upload(msg_pdf)

        wait_msg.edit_text.assert_awaited_once()
        assert t("doc_indexed_new", "uk", filename="pricing.pdf", chunks=4) in wait_msg.edit_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_admin_handlers_html_escape_filenames(self):
        msg_docs = make_message(text="/docs", user_id=999)
        docs = [{"filename": "<doc>&test.pdf", "file_type": "pdf", "chunk_count": 3, "uploaded_at": "2026-10-21"}]
        with patch("config.ADMIN_ID", 999), \
             patch("handlers.admin.list_documents", new_callable=AsyncMock, return_value=docs):
            await admin_handler.cmd_docs(msg_docs)
        assert "&lt;doc&gt;&amp;test.pdf" in msg_docs.answer.call_args[0][0]
        assert "<doc>" not in msg_docs.answer.call_args[0][0]

        msg_del = make_message(text="/del_doc <del>&doc.pdf", user_id=999)
        with patch("config.ADMIN_ID", 999), \
             patch("handlers.admin.delete_document_by_name", new_callable=AsyncMock, return_value=True):
            await admin_handler.cmd_del_doc(msg_del)
        assert "&lt;del&gt;&amp;doc.pdf" in msg_del.answer.call_args[0][0]
        assert "<del>" not in msg_del.answer.call_args[0][0]

        msg_upload = make_message(user_id=999)
        msg_upload.document = MagicMock()
        msg_upload.document.file_name = "<upload>&file.pdf"
        wait_msg = AsyncMock()
        msg_upload.answer = AsyncMock(return_value=wait_msg)
        msg_upload.bot.download = AsyncMock(return_value=io.BytesIO(b"dummy pdf bytes"))

        with patch("config.ADMIN_ID", 999), \
             patch("handlers.admin.index_document", new_callable=AsyncMock, return_value={"is_replaced": False, "chunk_count": 2}):
            await admin_handler.handle_document_upload(msg_upload)

        indexing_text = msg_upload.answer.call_args[0][0]
        assert "&lt;upload&gt;&amp;file.pdf" in indexing_text
        assert "<upload>" not in indexing_text
        result_text = wait_msg.edit_text.call_args[0][0]
        assert "&lt;upload&gt;&amp;file.pdf" in result_text
        assert "<upload>" not in result_text

    @pytest.mark.asyncio
    async def test_handle_document_upload_file_too_large(self):
        msg = make_message(user_id=999)
        msg.document = MagicMock()
        msg.document.file_name = "huge.pdf"
        msg.document.file_size = 25 * 1024 * 1024

        with patch("config.ADMIN_ID", 999):
            await admin_handler.handle_document_upload(msg)

        msg.answer.assert_awaited_once()
        assert t("doc_too_large", "uk") in msg.answer.call_args[0][0]
        msg.bot.download.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_document_upload_empty_document(self):
        msg = make_message(user_id=999)
        msg.document = MagicMock()
        msg.document.file_name = "empty.pdf"
        msg.document.file_size = 1024
        wait_msg = AsyncMock()
        msg.answer = AsyncMock(return_value=wait_msg)
        msg.bot.download = AsyncMock(return_value=io.BytesIO(b"empty bytes"))

        with patch("config.ADMIN_ID", 999), \
             patch("handlers.admin.index_document", new_callable=AsyncMock, side_effect=ValueError("No text extracted from document: empty.pdf")):
            await admin_handler.handle_document_upload(msg)

        wait_msg.edit_text.assert_awaited_once()
        assert t("doc_empty", "uk", filename="empty.pdf") in wait_msg.edit_text.call_args[0][0]

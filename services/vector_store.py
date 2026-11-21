import logging
import sys
import weakref
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

import numpy as np

logger = logging.getLogger(__name__)


@runtime_checkable
class VectorStore(Protocol):
    async def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 4,
        min_score: float = 0.52,
        score_window: float = 0.08,
    ) -> list[dict[str, Any]]: ...

    def invalidate(self) -> None: ...


_active_vector_stores: weakref.WeakSet["SQLiteNumPyVectorStore"] = weakref.WeakSet()


def invalidate_vector_store_cache() -> None:
    for store in list(_active_vector_stores):
        store.invalidate()


class SQLiteNumPyVectorStore:
    def __init__(
        self,
        loader: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None,
        embedding_model: str | None = None,
        embedding_dim: int | None = None,
    ) -> None:
        self._loader = loader
        self._embedding_model = embedding_model
        self._embedding_dim = embedding_dim
        self._matrix: np.ndarray | None = None
        self._metadata: list[dict[str, Any]] | None = None
        self._is_loaded: bool = False
        _active_vector_stores.add(self)

    @property
    def is_loaded(self) -> bool:
        return self._is_loaded

    @property
    def is_empty(self) -> bool:
        return self._is_loaded and (self._matrix is None or len(self._matrix) == 0)

    @property
    def matrix(self) -> np.ndarray | None:
        return self._matrix

    @property
    def metadata(self) -> list[dict[str, Any]] | None:
        return self._metadata

    def invalidate(self) -> None:
        self._matrix = None
        self._metadata = None
        self._is_loaded = False

    def _get_expected_model_and_dim(self) -> tuple[str, int]:
        rag_mod = sys.modules.get("services.rag")
        model = self._embedding_model or (getattr(rag_mod, "EMBEDDING_MODEL", None) if rag_mod else None)
        dim = self._embedding_dim or (getattr(rag_mod, "EMBEDDING_DIM", None) if rag_mod else None)
        if model is None or dim is None:
            import config
            model = model or getattr(config, "EMBEDDING_MODEL", "gemini-embedding-001")
            dim = dim or getattr(config, "EMBEDDING_DIM", 3072)
        return str(model), int(dim)

    async def _fetch_chunks(self) -> list[dict[str, Any]]:
        if self._loader is not None:
            return await self._loader()
        rag_mod = sys.modules.get("services.rag")
        getter = getattr(rag_mod, "get_all_chunks_with_metadata", None) if rag_mod else None
        if callable(getter):
            return await getter()
        from services.db import get_all_chunks_with_metadata
        return await get_all_chunks_with_metadata()

    async def ensure_loaded(self) -> None:
        if self._is_loaded:
            return
        await self.load()

    async def load(self) -> None:
        chunks = await self._fetch_chunks()
        expected_model, expected_dim = self._get_expected_model_and_dim()
        compatible_chunks: list[dict[str, Any]] = []

        for chunk in chunks:
            vector = chunk.get("embedding")
            if vector is None:
                continue
            if not isinstance(vector, np.ndarray):
                vector = np.asarray(vector, dtype=np.float32)
            if (
                chunk.get("embedding_model") != expected_model
                or chunk.get("embedding_dim") != expected_dim
                or vector.ndim != 1
                or vector.size != expected_dim
                or not np.isfinite(vector).all()
            ):
                logger.warning(
                    "Skipping incompatible chunk: file=%s, chunk=%s, model=%s, dim=%s, shape=%s; "
                    "expected model=%s, dim=%d. Reindex the document.",
                    chunk.get("filename"),
                    chunk.get("chunk_index"),
                    chunk.get("embedding_model"),
                    chunk.get("embedding_dim"),
                    vector.shape,
                    expected_model,
                    expected_dim,
                )
                continue
            compatible_chunks.append({
                "content": chunk.get("content"),
                "filename": chunk.get("filename"),
                "page_number": chunk.get("page_number"),
                "embedding": vector,
            })

        if not compatible_chunks:
            self._matrix = np.empty((0, expected_dim), dtype=np.float32)
            self._metadata = []
            self._is_loaded = True
            return

        raw_matrix = np.stack([c["embedding"] for c in compatible_chunks]).astype(np.float32, copy=False)
        # Cache unit-length rows so cosine similarity is a matrix-vector product.
        norms = np.linalg.norm(raw_matrix, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1e-9, norms)
        self._matrix = (raw_matrix / norms).astype(np.float32, copy=False)
        self._metadata = [
            {
                "content": c["content"],
                "filename": c["filename"],
                "page_number": c["page_number"],
            }
            for c in compatible_chunks
        ]
        self._is_loaded = True

    async def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 4,
        min_score: float = 0.52,
        score_window: float = 0.08,
    ) -> list[dict[str, Any]]:
        await self.ensure_loaded()
        if self._matrix is None or len(self._matrix) == 0:
            return []

        if not isinstance(query_vector, np.ndarray):
            query_vector = np.asarray(query_vector, dtype=np.float32)

        if query_vector.ndim != 1 or query_vector.size != self._matrix.shape[1]:
            return []

        q_norm = np.linalg.norm(query_vector)
        if q_norm == 0 or not np.isfinite(q_norm):
            return []

        query_norm = (query_vector / q_norm).astype(np.float32, copy=False)
        sims = self._matrix @ query_norm

        results: list[dict[str, Any]] = []
        for idx, sim in enumerate(sims):
            score = float(sim)
            if score >= min_score:
                item = dict(self._metadata[idx])
                item["score"] = score
                results.append(item)

        results.sort(key=lambda x: x["score"], reverse=True)
        if not results:
            return []

        top_score = results[0]["score"]
        cutoff = max(min_score, top_score - score_window)
        filtered = [r for r in results if r["score"] >= cutoff]
        return filtered[:top_k]


default_vector_store = SQLiteNumPyVectorStore()

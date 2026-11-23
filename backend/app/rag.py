"""Base de connaissances (RAG) sur ChromaDB, avec repli en mémoire si Chroma est absent."""
from __future__ import annotations

import os
import re
import uuid

from .config import settings


def chunk(text: str, size: int = 800, overlap: int = 100) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    return [text[i:i + size] for i in range(0, max(len(text), 1), size - overlap)] or [""]


class KnowledgeBase:
    def __init__(self, path: str | None = None, collection: str = "pme_kb") -> None:
        try:
            if os.getenv("KB_BACKEND") == "memory":  # tests : pas de téléchargement d'embeddings
                raise ImportError
            import chromadb
            self._client = chromadb.PersistentClient(path=path or settings.chroma_path)
            self._col = self._client.get_or_create_collection(collection)
            self._mem = None
        except ImportError:
            self._col = None
            self._mem: list[dict] = []

    def add(self, text: str, source: str, metadata: dict | None = None) -> int:
        parts = chunk(text)
        metas = [{"source": source, **(metadata or {})} for _ in parts]
        ids = [f"{source}-{uuid.uuid4().hex[:8]}" for _ in parts]
        if self._col is not None:
            self._col.add(ids=ids, documents=parts, metadatas=metas)
        else:
            self._mem.extend({"id": i, "text": t, "meta": m} for i, t, m in zip(ids, parts, metas))
        return len(parts)

    def search(self, query: str, k: int = 4) -> list[dict]:
        if self._col is not None:
            if self._col.count() == 0:
                return []
            r = self._col.query(query_texts=[query], n_results=min(k, self._col.count()))
            return [{"text": t, "source": m.get("source", "")}
                    for t, m in zip(r["documents"][0], r["metadatas"][0])]
        words = set(re.findall(r"\w{4,}", query.lower()))
        scored = sorted(self._mem, key=lambda d: -len(words & set(re.findall(r"\w{4,}", d["text"].lower()))))
        return [{"text": d["text"], "source": d["meta"]["source"]} for d in scored[:k]]


_kb: KnowledgeBase | None = None


def get_kb() -> KnowledgeBase:
    global _kb
    if _kb is None:
        _kb = KnowledgeBase()
    return _kb

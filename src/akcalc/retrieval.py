"""Hybrid retrieval: BM25 + ECNU embeddings + ECNU rerank, with graceful fallback."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import jieba
import numpy as np
from rank_bm25 import BM25Okapi

from .config import PROJECT_ROOT
from .llm import client
from .schemas import RetrievedDoc

CORPUS_PATH = PROJECT_ROOT / "data" / "kb" / "corpus.jsonl"
EMBED_CACHE = PROJECT_ROOT / "data" / "kb" / "embeddings.npz"

# Keep retrieval deterministic; jieba only tokenizes.
jieba.setLogLevel(20)


def tokenize(text: str) -> list[str]:
    text = text.lower()
    tokens = [t.strip() for t in jieba.lcut_for_search(text) if t.strip()]
    return tokens


def _cosine_matrix(query_vec: list[float], matrix: np.ndarray) -> np.ndarray:
    q = np.asarray(query_vec, dtype=np.float32)
    q_norm = np.linalg.norm(q)
    if q_norm == 0:
        return np.zeros(matrix.shape[0], dtype=np.float32)
    m_norm = np.linalg.norm(matrix, axis=1)
    m_norm[m_norm == 0] = 1.0
    return (matrix @ q) / (m_norm * q_norm)


class Retriever:
    def __init__(self, corpus_path: Path = CORPUS_PATH, use_vectors: bool = True):
        self.corpus_path = corpus_path
        self.docs: list[dict] = []
        if corpus_path.exists():
            for line in corpus_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.docs.append(json.loads(line))
        self._tokenized = [tokenize(d["title"] + "\n" + d["text"]) for d in self.docs]
        self._bm25 = BM25Okapi(self._tokenized) if self._tokenized else None
        self.use_vectors = use_vectors and client.enabled and bool(self.docs)
        self._matrix: np.ndarray | None = None
        self.vector_error = ""
        if self.use_vectors:
            try:
                self._load_or_build_vectors()
            except Exception as exc:  # noqa: BLE001
                self.use_vectors = False
                self._matrix = None
                self.vector_error = f"{type(exc).__name__}: {exc}"

    def _load_or_build_vectors(self) -> None:
        texts = [d["title"] + "\n" + d["text"] for d in self.docs]
        fingerprint = hashlib.sha256("\n".join(texts).encode("utf-8")).hexdigest()
        if EMBED_CACHE.exists():
            try:
                data = np.load(EMBED_CACHE)
                cached_fp = str(data["fingerprint"]) if "fingerprint" in data.files else ""
                if (
                    data["doc_ids"].tolist() == [d["doc_id"] for d in self.docs]
                    and cached_fp == fingerprint
                ):
                    self._matrix = data["vectors"].astype(np.float32)
                    return
            except Exception:  # noqa: BLE001
                pass  # stale/corrupt cache -> rebuild below
        vectors = client.embed(texts)
        self._matrix = np.asarray(vectors, dtype=np.float32)
        EMBED_CACHE.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            EMBED_CACHE,
            vectors=self._matrix,
            doc_ids=np.asarray([d["doc_id"] for d in self.docs]),
            fingerprint=np.asarray(fingerprint),
        )

    def _bm25_scores(self, query: str) -> np.ndarray:
        if self._bm25 is None:
            return np.zeros(len(self.docs), dtype=np.float32)
        return np.asarray(self._bm25.get_scores(tokenize(query)), dtype=np.float32)

    def search(
        self,
        query: str,
        top_k: int = 8,
        kinds: set[str] | None = None,
        use_rerank: bool = True,
    ) -> list[RetrievedDoc]:
        if not self.docs:
            return []
        n = len(self.docs)
        bm25 = self._bm25_scores(query)
        bm25_order = list(np.argsort(-bm25))
        vec_order: list[int] = []
        if self.use_vectors and self._matrix is not None:
            qv = client.embed([query])[0]
            cos = _cosine_matrix(qv, self._matrix)
            vec_order = list(np.argsort(-cos))
        else:
            cos = np.zeros(n, dtype=np.float32)

        # Reciprocal-rank fusion over BM25 and vector rankings.
        rrf = np.zeros(n, dtype=np.float32)
        for rank, idx in enumerate(bm25_order):
            rrf[idx] += 1.0 / (60 + rank + 1)
        for rank, idx in enumerate(vec_order):
            rrf[idx] += 1.0 / (60 + rank + 1)
        # Lightweight direct score contribution keeps exact keyword matches strong.
        if bm25.max() > 0:
            rrf += 0.05 * (bm25 / bm25.max())
        if cos.max() > 0:
            rrf += 0.05 * (cos / cos.max())

        candidates = list(np.argsort(-rrf))
        if kinds:
            candidates = [i for i in candidates if self.docs[i]["kind"] in kinds]
        candidates = candidates[: max(top_k * 3, 12)]

        if use_rerank and client.enabled and candidates:
            texts = [self.docs[i]["title"] + "\n" + self.docs[i]["text"] for i in candidates]
            reranked = client.rerank(query, texts, top_n=top_k)
            if reranked:
                out = []
                for pos, score in reranked:
                    if 0 <= pos < len(candidates):
                        idx = candidates[pos]
                        doc = self.docs[idx]
                        out.append(
                            RetrievedDoc(
                                doc_id=doc["doc_id"],
                                kind=doc["kind"],
                                title=doc["title"],
                                text=doc["text"],
                                score=float(score),
                                payload=doc.get("payload", {}),
                            )
                        )
                if out:
                    return out[:top_k]

        return [
            RetrievedDoc(
                doc_id=self.docs[i]["doc_id"],
                kind=self.docs[i]["kind"],
                title=self.docs[i]["title"],
                text=self.docs[i]["text"],
                score=float(rrf[i]),
                payload=self.docs[i].get("payload", {}),
            )
            for i in candidates[:top_k]
        ]


def norm_score(score: float) -> float:
    """Map an unbounded retrieval score into [0, 1] for display only."""
    return 1.0 - math.exp(-max(score, 0.0))

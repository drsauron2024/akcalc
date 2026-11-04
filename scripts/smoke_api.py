"""Verify ChatECNU connectivity: /models, chat, embeddings, rerank.

Usage: uv run python scripts/smoke_api.py
The API key is never printed; only a masked form is shown.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
from openai import OpenAI

from akcalc.config import settings


def main() -> int:
    if not settings.has_key:
        print("CHATECNU_API_KEY is not set. Put it in akcalc/.env and retry.")
        return 2

    print(f"base_url = {settings.base_url}")
    print(f"api_key  = {settings.masked_key}")
    client = OpenAI(api_key=settings.api_key, base_url=settings.base_url, timeout=settings.timeout)
    headers = {"Authorization": f"Bearer {settings.api_key}"}
    ok = True

    # 1. /models
    t0 = time.perf_counter()
    try:
        models = client.models.list()
        ids = [m.id for m in models.data]
        print(f"[ok] /models ({time.perf_counter() - t0:.2f}s): {', '.join(ids[:12])}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"[FAIL] /models: {type(exc).__name__}: {exc}")

    # 2. chat
    t0 = time.perf_counter()
    try:
        resp = client.chat.completions.create(
            model=settings.chat_model,
            messages=[{"role": "user", "content": "只回答数字：12 乘以 3 等于多少？"}],
            temperature=0,
            max_tokens=32,
        )
        text = (resp.choices[0].message.content or "").strip()
        usage = resp.usage
        print(
            f"[ok] chat/{settings.chat_model} ({time.perf_counter() - t0:.2f}s): "
            f"{text[:60]!r} tokens={getattr(usage, 'total_tokens', '?')}"
        )
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"[FAIL] chat/{settings.chat_model}: {type(exc).__name__}: {exc}")

    # 3. embeddings
    t0 = time.perf_counter()
    try:
        emb = client.embeddings.create(
            model=settings.embed_model, input=["物理伤害 = 攻击力 - 防御力", "法术伤害"]
        )
        dim = len(emb.data[0].embedding)
        print(f"[ok] embeddings/{settings.embed_model} ({time.perf_counter() - t0:.2f}s): dim={dim}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"[FAIL] embeddings/{settings.embed_model}: {type(exc).__name__}: {exc}")

    # 4. rerank (Cohere-compatible endpoint, not in OpenAI SDK)
    t0 = time.perf_counter()
    try:
        r = httpx.post(
            f"{settings.base_url.rstrip('/')}/rerank",
            headers=headers,
            json={
                "model": settings.rerank_model,
                "query": "物理伤害怎么计算",
                "documents": ["物理伤害 = 攻击力 - 防御力", "今天天气很好"],
                "top_n": 2,
                "return_documents": True,
            },
            timeout=settings.timeout,
        )
        r.raise_for_status()
        payload = r.json()
        scores = [(x.get("index"), round(x.get("relevance_score", 0), 4)) for x in payload.get("results", [])]
        print(f"[ok] rerank/{settings.rerank_model} ({time.perf_counter() - t0:.2f}s): {scores}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"[FAIL] rerank/{settings.rerank_model}: {type(exc).__name__}: {exc}")

    print("RESULT:", "ALL OK" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

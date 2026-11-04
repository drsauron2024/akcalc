"""Thin ChatECNU client with concurrency limits, retries and JSON mode."""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from openai import OpenAI

from .config import settings

# Documented per-model concurrency: ecnu-max 3, ecnu-plus 5.
_SEMAPHORES = {
    "ecnu-max": threading.Semaphore(3),
    "ecnu-plus": threading.Semaphore(5),
}


@dataclass
class LLMResult:
    text: str
    model: str
    latency_ms: float = 0.0
    usage: dict[str, int] = field(default_factory=dict)
    error: str = ""


def strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


class LLMClient:
    def __init__(self) -> None:
        self.enabled = settings.has_key
        self._client = (
            OpenAI(
                api_key=settings.api_key,
                base_url=settings.base_url,
                timeout=settings.timeout,
                max_retries=0,
            )
            if self.enabled
            else None
        )

    def _sem(self, model: str) -> threading.Semaphore:
        return _SEMAPHORES.get(model, threading.Semaphore(3))

    def chat(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        json_mode: bool = False,
        max_tokens: int = 1200,
    ) -> LLMResult:
        model = model or settings.chat_model
        if not self.enabled:
            return LLMResult(text="", model=model, error="LLM disabled: no API key")
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        t0 = time.perf_counter()
        last_exc: Exception | None = None
        with self._sem(model):
            for attempt in range(3):
                try:
                    kwargs: dict[str, Any] = {
                        "model": model,
                        "messages": messages,
                        "temperature": settings.temperature,
                        "max_tokens": max_tokens,
                    }
                    if json_mode:
                        kwargs["response_format"] = {"type": "json_object"}
                    resp = self._client.chat.completions.create(**kwargs)
                    text = (resp.choices[0].message.content or "").strip()
                    usage = {}
                    if resp.usage is not None:
                        usage = {
                            "prompt_tokens": resp.usage.prompt_tokens or 0,
                            "completion_tokens": resp.usage.completion_tokens or 0,
                            "total_tokens": resp.usage.total_tokens or 0,
                        }
                    return LLMResult(
                        text=text,
                        model=model,
                        latency_ms=(time.perf_counter() - t0) * 1000,
                        usage=usage,
                    )
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    status = getattr(exc, "status_code", None)
                    if status not in (429, 500, 502, 503, 504):
                        break
                    time.sleep(1.5 * (attempt + 1))
        return LLMResult(
            text="",
            model=model,
            latency_ms=(time.perf_counter() - t0) * 1000,
            error=f"{type(last_exc).__name__}: {last_exc}",
        )

    def chat_json(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        max_tokens: int = 1200,
    ) -> tuple[dict[str, Any] | None, LLMResult]:
        result = self.chat(
            prompt=prompt,
            system=system,
            model=model,
            json_mode=True,
            max_tokens=max_tokens,
        )
        if result.error or not result.text:
            return None, result
        raw = strip_code_fence(result.text)
        try:
            return json.loads(raw), result
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, re.S)
            if match:
                try:
                    return json.loads(match.group(0)), result
                except json.JSONDecodeError:
                    pass
        return None, result

    def embed(self, texts: list[str], batch_size: int = 16) -> list[list[float]]:
        if not self.enabled:
            raise RuntimeError("LLM disabled: no API key")
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            last_exc: Exception | None = None
            for attempt in range(3):
                try:
                    resp = self._client.embeddings.create(
                        model=settings.embed_model, input=batch
                    )
                    vectors.extend([item.embedding for item in resp.data])
                    break
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    time.sleep(1.5 * (attempt + 1))
            else:
                raise RuntimeError(f"embedding failed: {last_exc}")
        return vectors

    def rerank(
        self, query: str, documents: list[str], top_n: int = 5
    ) -> list[tuple[int, float]]:
        if not self.enabled:
            return []
        try:
            r = httpx.post(
                f"{settings.base_url.rstrip('/')}/rerank",
                headers={"Authorization": f"Bearer {settings.api_key}"},
                json={
                    "model": settings.rerank_model,
                    "query": query,
                    "documents": documents,
                    "top_n": top_n,
                    "return_documents": False,
                },
                timeout=settings.timeout,
            )
            r.raise_for_status()
            return [
                (int(item["index"]), float(item.get("relevance_score", 0.0)))
                for item in r.json().get("results", [])
            ]
        except Exception:  # noqa: BLE001
            return []


client = LLMClient()

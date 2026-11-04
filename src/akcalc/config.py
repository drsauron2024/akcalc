"""Runtime configuration loaded from .env / environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    api_key: str = field(default_factory=lambda: _env("CHATECNU_API_KEY"))
    base_url: str = field(
        default_factory=lambda: _env("CHATECNU_BASE_URL", "https://chat.ecnu.edu.cn/open/api/v1")
    )
    chat_model: str = field(default_factory=lambda: _env("CHATECNU_CHAT_MODEL", "ecnu-max"))
    fast_model: str = field(default_factory=lambda: _env("CHATECNU_FAST_MODEL", "ecnu-plus"))
    embed_model: str = field(
        default_factory=lambda: _env("CHATECNU_EMBED_MODEL", "ecnu-embedding-small")
    )
    rerank_model: str = field(default_factory=lambda: _env("CHATECNU_RERANK_MODEL", "ecnu-rerank"))
    temperature: float = field(
        default_factory=lambda: float(_env("AKCALC_LLM_TEMPERATURE", "0") or 0)
    )
    timeout: float = field(
        default_factory=lambda: float(_env("AKCALC_REQUEST_TIMEOUT", "120") or 120)
    )
    log_dir: Path = field(
        default_factory=lambda: PROJECT_ROOT / _env("AKCALC_LOG_DIR", "results/logs")
    )

    @property
    def has_key(self) -> bool:
        return bool(self.api_key) and not self.api_key.startswith("sk-your")

    @property
    def masked_key(self) -> str:
        if not self.api_key:
            return "<unset>"
        return self.api_key[:6] + "..." + self.api_key[-4:]

    def ensure_dirs(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()

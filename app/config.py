from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


@dataclass(frozen=True)
class Settings:
    gitlab_secret_token: str = os.getenv("GITLAB_SECRET_TOKEN", "")
    feishu_secret: str = os.getenv("FEISHU_SECRET", "")
    feishu_timeout: float = float(os.getenv("FEISHU_TIMEOUT", "10"))
    feishu_retry_count: int = _int("FEISHU_RETRY_COUNT", 3)
    feishu_retry_backoff: float = float(os.getenv("FEISHU_RETRY_BACKOFF", "1"))
    max_message_length: int = _int("MAX_MESSAGE_LENGTH", 12000)
    dedupe_ttl_seconds: int = _int("DEDUPE_TTL_SECONDS", 86400)
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()


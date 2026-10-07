from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _string_map(name: str) -> dict[str, str]:
    value = os.getenv(name, "{}")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be a JSON object mapping hook IDs to tokens") from exc
    if not isinstance(parsed, dict) or any(
        not isinstance(key, str) or not isinstance(token, str) or not token.strip()
        for key, token in parsed.items()
    ):
        raise ValueError(f"{name} must map hook IDs to non-empty token strings")
    return parsed


@dataclass(frozen=True)
class Settings:
    gitlab_signing_tokens: dict[str, str] = field(default_factory=lambda: _string_map("GITLAB_SIGNING_TOKENS"))
    gitlab_webhook_tolerance_seconds: int = _int("GITLAB_WEBHOOK_TOLERANCE_SECONDS", 300)
    feishu_secret: str = os.getenv("FEISHU_SECRET", "")
    feishu_timeout: float = float(os.getenv("FEISHU_TIMEOUT", "10"))
    feishu_retry_count: int = _int("FEISHU_RETRY_COUNT", 3)
    feishu_retry_backoff: float = float(os.getenv("FEISHU_RETRY_BACKOFF", "1"))
    max_message_length: int = _int("MAX_MESSAGE_LENGTH", 12000)
    dedupe_ttl_seconds: int = _int("DEDUPE_TTL_SECONDS", 86400)
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()


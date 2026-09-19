"""structlog をベースにした構造化ログ設定 (認証情報の redaction 付き)。

`configure_logging()` をアプリ起動時に 1 回呼ぶ。以後 `get_logger(__name__)` で
`structlog.stdlib.BoundLogger` を取り出す。ログ出力時に `knox_jwt`,
`authorization`, `secret_access_key`, `session_token` などのキーは自動で
`***REDACTED***` に置換される。
"""
from __future__ import annotations

import logging
import os
import re
import sys
from typing import Any

import structlog

# redact 対象のキー名 (大文字小文字を無視して部分一致)
_SENSITIVE_KEY_PATTERNS = (
    re.compile(r"(?i)knox.?jwt"),
    re.compile(r"(?i)authorization"),
    re.compile(r"(?i)access.?key"),
    re.compile(r"(?i)secret"),
    re.compile(r"(?i)session.?token"),
    re.compile(r"(?i)password"),
    re.compile(r"(?i)api.?key"),
    re.compile(r"(?i)cookie"),
)

_REDACTED = "***REDACTED***"


def _redact_processor(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """機微キーをマスクする structlog processor。"""
    for key in list(event_dict.keys()):
        if any(p.search(key) for p in _SENSITIVE_KEY_PATTERNS):
            event_dict[key] = _REDACTED
    # dict の値の中に混じっているケース (headers={"Authorization": "..."} 等) にも対応
    _scrub_nested(event_dict)
    return event_dict


def _scrub_nested(obj: Any) -> None:
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if isinstance(k, str) and any(p.search(k) for p in _SENSITIVE_KEY_PATTERNS):
                obj[k] = _REDACTED
            else:
                _scrub_nested(v)
    elif isinstance(obj, list):
        for item in obj:
            _scrub_nested(item)


def configure_logging(level: str | None = None) -> None:
    """アプリ起動時に 1 回呼び出す。"""
    log_level = (level or os.environ.get("THOR_LOG_LEVEL") or "INFO").upper()
    logging.basicConfig(
        stream=sys.stdout,
        level=getattr(logging, log_level, logging.INFO),
        format="%(message)s",
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact_processor,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level, logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name) if name else structlog.get_logger()

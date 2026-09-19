"""共通 HTTP / 認証 / ロギング層。

Knox JWT / IDBroker STS 資格情報の伝搬をここで受け持ち、
Tool は user_context を経由してエンドユーザー権限で外部システムを叩く。

エクスポート:

    UserContext, get_user_context, set_user_context
    build_user_context_from_headers, bearer_header
    configure_logging, get_logger
    ThorHttpClient
    ErrorCode, ToolErrorResult, ok, err
    BaseThorTool
"""
from __future__ import annotations

from thor.transport.auth import bearer_header, build_user_context_from_headers, extract_jwt
from thor.transport.errors import ErrorCode, ToolErrorResult, err, ok
from thor.transport.http import ThorHttpClient
from thor.transport.logging import configure_logging, get_logger
from thor.transport.tool_base import BaseThorTool
from thor.transport.user_context import (
    AwsCredentials,
    UserContext,
    get_user_context,
    get_user_context_optional,
    reset_user_context,
    set_user_context,
)

__all__ = [
    "AwsCredentials",
    "BaseThorTool",
    "ErrorCode",
    "ThorHttpClient",
    "ToolErrorResult",
    "UserContext",
    "bearer_header",
    "build_user_context_from_headers",
    "configure_logging",
    "err",
    "extract_jwt",
    "get_logger",
    "get_user_context",
    "get_user_context_optional",
    "ok",
    "reset_user_context",
    "set_user_context",
]

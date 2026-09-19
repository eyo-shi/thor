"""共通エラーコード。

すべての Tool は例外を投げずに :class:`ToolErrorResult` を返し、Crew.ai の
LLM 再試行ループを暴走させない。エラーコードは 7 プレフィックスに整理:

    S3_*      S3 / IDBroker 関連
    FORMAT_*  ファイルフォーマット判定・パース
    SCHEMA_*  スキーマ推定・命名衝突
    TRINO_*   Trino / Iceberg
    PERM_*    権限不足 (CREATE / SELECT など)
    CDV_*     Cloudera Data Visualization
    OSSIE_*   Apache Ossie YAML

`Dispatcher` はコード別にユーザー向け日本語メッセージに変換する。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ErrorCode:
    # S3 / IDBroker
    S3_NOT_FOUND = "S3_NOT_FOUND"
    S3_ACCESS_DENIED = "S3_ACCESS_DENIED"
    S3_RANGE_FAILED = "S3_RANGE_FAILED"
    S3_ASSUMEROLE_FAILED = "S3_ASSUMEROLE_FAILED"

    # Format
    FORMAT_UNSUPPORTED = "FORMAT_UNSUPPORTED"
    FORMAT_CORRUPT = "FORMAT_CORRUPT"
    FORMAT_EXCEL_HEADER_UNDETECTED = "FORMAT_EXCEL_HEADER_UNDETECTED"
    FORMAT_ENCODING_UNKNOWN = "FORMAT_ENCODING_UNKNOWN"

    # Schema
    SCHEMA_INFER_FAILED = "SCHEMA_INFER_FAILED"
    SCHEMA_NAME_CONFLICT = "SCHEMA_NAME_CONFLICT"

    # Trino / Iceberg
    TRINO_QUERY_FAILED = "TRINO_QUERY_FAILED"
    TRINO_DDL_FAILED = "TRINO_DDL_FAILED"
    TRINO_TABLE_NOT_FOUND = "TRINO_TABLE_NOT_FOUND"
    TRINO_EXPLAIN_FAILED = "TRINO_EXPLAIN_FAILED"

    # Permission
    PERM_CREATE_DENIED = "PERM_CREATE_DENIED"
    PERM_SELECT_DENIED = "PERM_SELECT_DENIED"
    PERM_INSERT_DENIED = "PERM_INSERT_DENIED"
    PERM_UNKNOWN = "PERM_UNKNOWN"

    # CDV
    CDV_NOT_RUNNING = "CDV_NOT_RUNNING"
    CDV_API_FAILED = "CDV_API_FAILED"
    CDV_DATASET_CONFLICT = "CDV_DATASET_CONFLICT"

    # Ossie
    OSSIE_YAML_INVALID = "OSSIE_YAML_INVALID"
    OSSIE_NOT_FOUND = "OSSIE_NOT_FOUND"
    OSSIE_GIT_CONFLICT = "OSSIE_GIT_CONFLICT"

    # Transport-level
    HTTP_TIMEOUT = "HTTP_TIMEOUT"
    HTTP_UNAVAILABLE = "HTTP_UNAVAILABLE"
    AUTH_MISSING = "AUTH_MISSING"


@dataclass
class ToolErrorResult:
    """Tool から返す標準エラー形。JSON にダンプすると LLM が読める。"""

    error_code: str
    message: str
    detail: dict[str, Any] | None = None
    status: str = "error"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "status": self.status,
            "error_code": self.error_code,
            "message": self.message,
        }
        if self.detail:
            d["detail"] = self.detail
        return d


def ok(payload: dict[str, Any]) -> dict[str, Any]:
    """Tool の正常返却をラップする。"""
    return {"status": "ok", **payload}


def err(code: str, message: str, **detail: Any) -> dict[str, Any]:
    """Tool のエラー返却をラップする。"""
    return ToolErrorResult(
        error_code=code, message=message, detail=detail or None
    ).to_dict()

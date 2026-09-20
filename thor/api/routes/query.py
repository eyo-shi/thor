"""Table Preview 用の SELECT (`/api/query`)。

ResultPane の Table タブが Iceberg テーブル先頭 N 行を取るために使う。
サーバ側で行数上限を強制し、副作用 SQL は拒否する。
"""
from __future__ import annotations

import re
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from thor.api.auth import require_user_context
from thor.transport.config import get_trino_config
from thor.transport.user_context import UserContext
from thor.tools._trino_client import map_trino_error, trino_connection_for_user

router = APIRouter(prefix="/api", tags=["query"])

_MUTATION_RE = re.compile(
    r"^\s*(CREATE|DROP|ALTER|INSERT|UPDATE|DELETE|TRUNCATE|MERGE|CALL|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


class QueryRequest(BaseModel):
    sql: str = Field(..., min_length=1, max_length=200_000)
    catalog: str = Field("iceberg", min_length=1, max_length=128)
    schema_: Optional[str] = Field(None, alias="schema", max_length=128)
    max_rows: int = Field(1000, ge=1, le=10000)

    model_config = {"populate_by_name": True}


class QueryResponse(BaseModel):
    columns: list[dict[str, Any]]
    rows: list[list[Any]]
    truncated: bool


@router.post("/query", response_model=QueryResponse)
def run_query(
    body: QueryRequest,
    user_ctx: Annotated[UserContext, Depends(require_user_context)],
) -> QueryResponse:
    # Trino 未設定なら 503 + guided error (UI が SetupGuide 表示)
    if get_trino_config() is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": "TRINO_NOT_CONFIGURED",
                "message": "Trino / CDW への接続情報が設定されていません。",
                "instruction": (
                    "Cloudera AI Workbench の Site Administration → Data "
                    "Connections で CDW / Trino connection を登録し、Project "
                    "→ Settings → Advanced → Environment Variables に "
                    "THOR_TRINO_CONNECTION_NAME を設定して Application を"
                    "再起動してください。"
                ),
            },
        )
    if _MUTATION_RE.match(body.sql):
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "TRINO_QUERY_FAILED",
                "message": (
                    "/api/query is read-only. Mutation statements are rejected."
                ),
            },
        )
    conn_or_err = trino_connection_for_user(
        user_ctx, catalog=body.catalog, schema=body.schema_
    )
    if isinstance(conn_or_err, dict):
        raise HTTPException(status_code=502, detail=conn_or_err)
    try:
        cur = conn_or_err.cursor()
        cur.execute(body.sql)
        # fetchmany で max_rows+1 まで取り、超過を truncated として扱う
        rows_raw = cur.fetchmany(body.max_rows + 1)
        truncated = len(rows_raw) > body.max_rows
        rows = rows_raw[: body.max_rows]
        columns = [
            {"name": d[0], "type": (d[1] if len(d) > 1 else None)}
            for d in (cur.description or [])
        ]
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=502, detail=map_trino_error(e, body.sql)
        ) from e

    return QueryResponse(
        columns=columns,
        rows=[list(_normalize_row(r)) for r in rows],
        truncated=truncated,
    )


def _normalize_row(row: Any) -> Any:
    """JSON 化できない Python オブジェクト (Decimal, datetime) を文字列化。"""
    out = []
    for v in row:
        if v is None or isinstance(v, (bool, int, float, str)):
            out.append(v)
        else:
            try:
                out.append(v.isoformat())  # datetime / date / time
            except AttributeError:
                out.append(str(v))
    return out

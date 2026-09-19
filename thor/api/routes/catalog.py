"""Trino カタログブラウザ API (`/api/catalog/*`)。

TreePane の左ペイン `iceberg` ルートに対応。Knox JWT でエンドユーザー
権限のクエリを行い、`information_schema` を叩く。

エンドポイント:

* ``GET /api/catalog/schemas``     — 全 schema
* ``GET /api/catalog/tables``      — schema 指定でテーブル一覧
* ``GET /api/catalog/columns``     — fully-qualified なテーブルのカラム定義
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from thor.api.auth import require_user_context
from thor.transport.user_context import UserContext
from thor.tools._trino_client import map_trino_error, trino_connection_for_user

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


def _run_query(
    user_ctx: UserContext,
    sql: str,
    catalog: str,
) -> list[list[Any]]:
    conn_or_err = trino_connection_for_user(user_ctx, catalog=catalog)
    if isinstance(conn_or_err, dict):
        detail = conn_or_err
        raise HTTPException(status_code=502, detail=detail)
    try:
        cur = conn_or_err.cursor()
        cur.execute(sql)
        return cur.fetchall()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=map_trino_error(e, sql)) from e


@router.get("/schemas")
def list_schemas(
    user_ctx: Annotated[UserContext, Depends(require_user_context)],
    catalog: Annotated[str, Query(min_length=1, max_length=128)] = "iceberg",
) -> dict[str, Any]:
    sql = (
        f"SELECT schema_name FROM {catalog}.information_schema.schemata "
        "ORDER BY schema_name"
    )
    rows = _run_query(user_ctx, sql, catalog)
    return {
        "catalog": catalog,
        "schemas": [r[0] for r in rows if r and r[0]],
    }


@router.get("/tables")
def list_tables(
    user_ctx: Annotated[UserContext, Depends(require_user_context)],
    schema: Annotated[str, Query(min_length=1, max_length=128)],
    catalog: Annotated[str, Query(min_length=1, max_length=128)] = "iceberg",
) -> dict[str, Any]:
    sql = (
        f"SELECT table_name, table_type "
        f"FROM {catalog}.information_schema.tables "
        f"WHERE table_schema = '{_esc(schema)}' "
        f"ORDER BY table_name"
    )
    rows = _run_query(user_ctx, sql, catalog)
    return {
        "catalog": catalog,
        "schema": schema,
        "tables": [
            {"name": r[0], "type": r[1], "fq": f"{catalog}.{schema}.{r[0]}"}
            for r in rows
            if r and r[0]
        ],
    }


@router.get("/columns")
def list_columns(
    user_ctx: Annotated[UserContext, Depends(require_user_context)],
    fq: Annotated[str, Query(min_length=3, max_length=256)],
) -> dict[str, Any]:
    """``fq`` は ``catalog.schema.table`` 形式。"""
    parts = fq.split(".")
    if len(parts) != 3:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "BAD_REQUEST",
                "message": "fq must be 'catalog.schema.table'",
            },
        )
    catalog, schema, table = parts
    sql = (
        f"SELECT column_name, data_type, is_nullable "
        f"FROM {catalog}.information_schema.columns "
        f"WHERE table_schema = '{_esc(schema)}' "
        f"  AND table_name = '{_esc(table)}' "
        f"ORDER BY ordinal_position"
    )
    rows = _run_query(user_ctx, sql, catalog)
    return {
        "fq": fq,
        "columns": [
            {"name": r[0], "type": r[1], "nullable": (r[2] == "YES")}
            for r in rows
            if r and r[0]
        ],
    }


def _esc(s: str) -> str:
    """Trino 文字列リテラルの単純エスケープ。"""
    return s.replace("'", "''")

"""Trino / Iceberg Tool。

Knox 経由の Trino Gateway に対し、エンドユーザーの Knox JWT を Bearer として
渡す。Trino 側 Ranger ACL が JWT の ``sub`` を主体に判定するため、これで
エンドユーザー権限のクエリが実現する。

* :class:`TrinoQueryTool` - SELECT (行数制限あり)
* :class:`TrinoDDLTool` - CREATE / DROP / ALTER (max_retries=0 前提)
* :class:`TrinoMetaTool` - information_schema + SHOW STATS
"""
from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, Field

from thor.transport.errors import ErrorCode, err, ok
from thor.transport.logging import get_logger
from thor.transport.tool_base import BaseThorTool
from thor.transport.user_context import UserContext
from thor.tools._trino_client import map_trino_error, trino_connection_for_user

_logger = get_logger(__name__)

_MUTATION_KEYWORDS = re.compile(
    r"^\s*(CREATE|DROP|ALTER|INSERT|UPDATE|DELETE|TRUNCATE|MERGE|CALL|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


def _is_readonly(sql: str) -> bool:
    return _MUTATION_KEYWORDS.match(sql) is None


# ------------------------------------------------------------------ #
# TrinoQueryTool
# ------------------------------------------------------------------ #
class TrinoQueryArgs(BaseModel):
    sql: str = Field(..., description="実行する SELECT / SHOW / DESCRIBE / EXPLAIN。")
    catalog: str = Field("iceberg", description="Trino カタログ名")
    schema_: Optional[str] = Field(
        None, alias="schema", description="Trino スキーマ (省略可)"
    )
    max_rows: int = Field(
        1000, ge=1, le=100000, description="返却する最大行数 (LIMIT 相当)"
    )

    model_config = {"populate_by_name": True}


class TrinoQueryTool(BaseThorTool):
    """SELECT / SHOW / DESCRIBE / EXPLAIN を実行して行を返す。

    ミューテーション系 SQL (CREATE 等) は拒否する。書き込みは
    :class:`TrinoDDLTool` を使う。
    """

    name: str = "trino_query"
    description: str = (
        "Execute a read-only Trino SQL statement (SELECT/SHOW/DESCRIBE/EXPLAIN) "
        "and return columns + rows. Mutations are rejected here — use trino_ddl."
    )
    args_schema: type[BaseModel] = TrinoQueryArgs

    def run(
        self,
        user_ctx: Optional[UserContext],
        sql: str,
        catalog: str = "iceberg",
        schema: Optional[str] = None,
        max_rows: int = 1000,
        **_: Any,
    ) -> dict[str, Any]:
        if not _is_readonly(sql):
            return err(
                ErrorCode.TRINO_QUERY_FAILED,
                "trino_query only accepts read-only SQL; use trino_ddl for mutations.",
            )
        conn_or_err = trino_connection_for_user(user_ctx, catalog=catalog, schema=schema)
        if isinstance(conn_or_err, dict):
            return conn_or_err
        try:
            cur = conn_or_err.cursor()
            cur.execute(sql)
            rows = cur.fetchmany(max_rows)
            cols = (
                [{"name": d[0], "type": str(d[1])} for d in cur.description]
                if cur.description
                else []
            )
        except Exception as e:  # noqa: BLE001
            return map_trino_error(e, sql)
        return ok(
            {
                "columns": cols,
                "rows": [list(r) for r in rows],
                "row_count": len(rows),
                "truncated": len(rows) >= max_rows,
            }
        )


# ------------------------------------------------------------------ #
# TrinoDDLTool
# ------------------------------------------------------------------ #
class TrinoDDLArgs(BaseModel):
    sql: str = Field(
        ...,
        description="実行する DDL / INSERT / DROP など (単文のみ)。",
    )
    catalog: str = Field("iceberg", description="Trino カタログ名")
    schema_: Optional[str] = Field(None, alias="schema")

    model_config = {"populate_by_name": True}


class TrinoDDLTool(BaseThorTool):
    """CREATE TABLE / DROP TABLE / INSERT などを実行する。

    副作用があるため、Task 側では ``max_retries=0`` を必須にする。
    """

    name: str = "trino_ddl"
    description: str = (
        "Execute a mutating Trino statement (CREATE / DROP / ALTER / INSERT). "
        "Returns row_count for INSERT, otherwise ok with no rows. "
        "Callers MUST set max_retries=0 to avoid double execution."
    )
    args_schema: type[BaseModel] = TrinoDDLArgs

    def run(
        self,
        user_ctx: Optional[UserContext],
        sql: str,
        catalog: str = "iceberg",
        schema: Optional[str] = None,
        **_: Any,
    ) -> dict[str, Any]:
        if _is_readonly(sql):
            return err(
                ErrorCode.TRINO_DDL_FAILED,
                "trino_ddl requires a mutating statement; use trino_query for SELECT.",
            )
        conn_or_err = trino_connection_for_user(user_ctx, catalog=catalog, schema=schema)
        if isinstance(conn_or_err, dict):
            return conn_or_err
        try:
            cur = conn_or_err.cursor()
            cur.execute(sql)
            # INSERT/UPDATE では 1 行 (count,) が返る
            affected: Optional[int] = None
            if cur.description:
                first = cur.fetchone()
                if first and len(first) == 1 and isinstance(first[0], int):
                    affected = first[0]
        except Exception as e:  # noqa: BLE001
            return map_trino_error(e, sql)
        return ok({"affected_rows": affected, "sql": sql})


# ------------------------------------------------------------------ #
# TrinoMetaTool
# ------------------------------------------------------------------ #
class TrinoMetaArgs(BaseModel):
    catalog: str = Field("iceberg")
    schema_: str = Field(..., alias="schema")
    table: str = Field(...)
    include_stats: bool = Field(True, description="SHOW STATS の結果を含める")

    model_config = {"populate_by_name": True}


class TrinoMetaTool(BaseThorTool):
    """テーブルのカラム定義 + オプションで統計情報を返す。

    * カラム: ``information_schema.columns``
    * 統計: ``SHOW STATS FOR ...`` (NULL 率、distinct 数、min/max)
    """

    name: str = "trino_meta"
    description: str = (
        "Return column definitions and (optionally) column statistics for an "
        "Iceberg/Trino table. Use before Text-to-SQL to ground the model."
    )
    args_schema: type[BaseModel] = TrinoMetaArgs

    def run(
        self,
        user_ctx: Optional[UserContext],
        catalog: str,
        schema: str,
        table: str,
        include_stats: bool = True,
        **_: Any,
    ) -> dict[str, Any]:
        fq = f'"{catalog}"."{schema}"."{table}"'
        conn_or_err = trino_connection_for_user(user_ctx, catalog=catalog, schema=schema)
        if isinstance(conn_or_err, dict):
            return conn_or_err
        columns: list[dict[str, Any]] = []
        stats: list[dict[str, Any]] = []
        try:
            cur = conn_or_err.cursor()
            cur.execute(
                "SELECT column_name, data_type, is_nullable "
                f"FROM {catalog}.information_schema.columns "
                f"WHERE table_schema = ? AND table_name = ? "
                "ORDER BY ordinal_position",
                params=(schema, table),
            )
            rows = cur.fetchall()
            if not rows:
                return err(
                    ErrorCode.TRINO_TABLE_NOT_FOUND,
                    f"Table {fq} not found or you have no SELECT privilege.",
                )
            columns = [
                {"name": r[0], "type": r[1], "nullable": r[2] == "YES"} for r in rows
            ]
            if include_stats:
                cur.execute(f"SHOW STATS FOR {fq}")
                stat_rows = cur.fetchall()
                stat_cols = [d[0] for d in cur.description] if cur.description else []
                for r in stat_rows:
                    stats.append(dict(zip(stat_cols, r)))
        except Exception as e:  # noqa: BLE001
            return map_trino_error(e, f"trino_meta({fq})")
        return ok(
            {
                "fq_table_name": f"{catalog}.{schema}.{table}",
                "columns": columns,
                "stats": stats,
            }
        )

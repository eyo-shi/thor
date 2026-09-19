"""Iceberg テーブル作成・存在確認 Tool。

Trino DDL の書式ゆらぎを吸収する薄いラッパ。DDL の実行自体は
:class:`TrinoDDLTool` と同じ経路 (JWT Bearer) を通り、Ranger の判定を受ける。

* :class:`TableExistsTool` — 衝突チェック
* :class:`IcebergCreateTableTool` — 構造化されたカラム定義 → CREATE TABLE DDL
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

# CREATE TABLE の識別子は英数 + _ に限定 (Trino/Iceberg 制約に合わせる)
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TRINO_TYPE_RE = re.compile(
    r"^(BOOLEAN|TINYINT|SMALLINT|INTEGER|BIGINT|REAL|DOUBLE|DATE|"
    r"TIMESTAMP(\(\d+\))?|DECIMAL\(\d+,\d+\)|VARCHAR(\(\d+\))?|CHAR(\(\d+\))?|"
    r"VARBINARY|JSON)$",
    re.IGNORECASE,
)


class ColumnDef(BaseModel):
    name: str
    trino_type: str
    nullable: bool = True
    comment: Optional[str] = None


# ------------------------------------------------------------------ #
# TableExistsTool
# ------------------------------------------------------------------ #
class TableExistsArgs(BaseModel):
    catalog: str = Field("iceberg")
    schema_: str = Field(..., alias="schema")
    table: str = Field(...)

    model_config = {"populate_by_name": True}


class TableExistsTool(BaseThorTool):
    """``information_schema.tables`` を叩いて存在確認する。

    存在すれば columns も返し、衝突チェックの材料にする。
    """

    name: str = "table_exists"
    description: str = (
        "Check whether an Iceberg/Trino table already exists. If it does, "
        "return its column definitions so the caller can decide whether to "
        "overwrite or pick another name."
    )
    args_schema: type[BaseModel] = TableExistsArgs

    def run(
        self,
        user_ctx: Optional[UserContext],
        catalog: str,
        schema: str,
        table: str,
        **_: Any,
    ) -> dict[str, Any]:
        conn_or_err = trino_connection_for_user(user_ctx, catalog=catalog, schema=schema)
        if isinstance(conn_or_err, dict):
            return conn_or_err
        try:
            cur = conn_or_err.cursor()
            cur.execute(
                f"SELECT table_name FROM {catalog}.information_schema.tables "
                "WHERE table_schema = ? AND table_name = ?",
                params=(schema, table),
            )
            row = cur.fetchone()
            exists = row is not None
            columns: list[dict[str, Any]] = []
            if exists:
                cur.execute(
                    "SELECT column_name, data_type, is_nullable "
                    f"FROM {catalog}.information_schema.columns "
                    "WHERE table_schema = ? AND table_name = ? "
                    "ORDER BY ordinal_position",
                    params=(schema, table),
                )
                columns = [
                    {"name": r[0], "type": r[1], "nullable": r[2] == "YES"}
                    for r in cur.fetchall()
                ]
        except Exception as e:  # noqa: BLE001
            return map_trino_error(e, f"table_exists({catalog}.{schema}.{table})")
        return ok(
            {
                "catalog": catalog,
                "schema": schema,
                "table": table,
                "exists": exists,
                "columns": columns,
            }
        )


# ------------------------------------------------------------------ #
# IcebergCreateTableTool
# ------------------------------------------------------------------ #
class IcebergCreateArgs(BaseModel):
    catalog: str = Field("iceberg")
    schema_: str = Field(..., alias="schema")
    table: str = Field(...)
    columns: list[ColumnDef] = Field(..., min_length=1)
    partitioning: list[str] = Field(
        default_factory=list,
        description='例: ["month(order_date)", "region"]',
    )
    location: Optional[str] = Field(
        None, description="Iceberg テーブルの物理配置 (S3 URI)"
    )
    if_not_exists: bool = Field(
        True, description="IF NOT EXISTS を付ける (衝突チェック済み前提なら False にする)"
    )

    model_config = {"populate_by_name": True}


class IcebergCreateTableTool(BaseThorTool):
    """構造化されたカラム定義から Iceberg CREATE TABLE を生成・実行する。

    * DDL は Agent が手で書かず、この Tool が組み立てる (SQL injection 対策)
    * 副作用があるため呼び出し側は ``max_retries=0`` を守ること
    * ``TrinoDDLTool`` と経路 (JWT) は同じで、権限判定は Ranger 側に委ねる
    """

    name: str = "iceberg_create_table"
    description: str = (
        "Create an empty Iceberg table from a structured column list. "
        "Returns the executed DDL so the caller can log it. "
        "Callers MUST set max_retries=0."
    )
    args_schema: type[BaseModel] = IcebergCreateArgs

    def run(
        self,
        user_ctx: Optional[UserContext],
        catalog: str,
        schema: str,
        table: str,
        columns: list[dict[str, Any]] | list[ColumnDef],
        partitioning: Optional[list[str]] = None,
        location: Optional[str] = None,
        if_not_exists: bool = True,
        **_: Any,
    ) -> dict[str, Any]:
        # 名前バリデーション
        for ident in (catalog, schema, table):
            if not _IDENT_RE.match(ident):
                return err(
                    ErrorCode.SCHEMA_INFER_FAILED,
                    f"Invalid identifier: {ident!r}",
                )
        # dict で来ることが多いので正規化
        cols: list[ColumnDef] = [
            c if isinstance(c, ColumnDef) else ColumnDef.model_validate(c)
            for c in columns
        ]
        # 型 / 名前バリデーション
        for c in cols:
            if not _IDENT_RE.match(c.name):
                return err(
                    ErrorCode.SCHEMA_INFER_FAILED,
                    f"Invalid column name: {c.name!r}",
                )
            if not _TRINO_TYPE_RE.match(c.trino_type):
                return err(
                    ErrorCode.SCHEMA_INFER_FAILED,
                    f"Invalid Trino type for {c.name}: {c.trino_type!r}",
                )
        ddl = _build_create_ddl(
            catalog, schema, table, cols, partitioning or [], location, if_not_exists
        )
        conn_or_err = trino_connection_for_user(user_ctx, catalog=catalog, schema=schema)
        if isinstance(conn_or_err, dict):
            return conn_or_err
        try:
            cur = conn_or_err.cursor()
            cur.execute(ddl)
            # CREATE TABLE は cur.description が None なことが多いが念のため
            if cur.description:
                cur.fetchall()
        except Exception as e:  # noqa: BLE001
            return map_trino_error(e, ddl)
        return ok(
            {
                "fq_table_name": f"{catalog}.{schema}.{table}",
                "ddl": ddl,
                "column_count": len(cols),
            }
        )


def _build_create_ddl(
    catalog: str,
    schema: str,
    table: str,
    cols: list[ColumnDef],
    partitioning: list[str],
    location: Optional[str],
    if_not_exists: bool,
) -> str:
    col_lines = []
    for c in cols:
        line = f'  "{c.name}" {c.trino_type}'
        if not c.nullable:
            line += " NOT NULL"
        if c.comment:
            # Trino の COMMENT は文字列リテラル。シングルクォートをエスケープ
            escaped = c.comment.replace("'", "''")
            line += f" COMMENT '{escaped}'"
        col_lines.append(line)
    with_parts: list[str] = ["format = 'PARQUET'"]
    if partitioning:
        arr = ", ".join(f"'{p}'" for p in partitioning)
        with_parts.append(f"partitioning = ARRAY[{arr}]")
    if location:
        escaped_loc = location.replace("'", "''")
        with_parts.append(f"location = '{escaped_loc}'")
    ine = "IF NOT EXISTS " if if_not_exists else ""
    ddl = (
        f'CREATE TABLE {ine}"{catalog}"."{schema}"."{table}" (\n'
        + ",\n".join(col_lines)
        + "\n) WITH (\n  "
        + ",\n  ".join(with_parts)
        + "\n)"
    )
    return ddl

"""Trino Tool のテスト (trino Python client の cursor をモック化)。

DBAPI connection.cursor() が返すオブジェクトを差し替えて、
* 読み書き判定
* 権限エラーの写像 (PERM_SELECT_DENIED / PERM_INSERT_DENIED / PERM_CREATE_DENIED)
* 存在しないテーブル → TRINO_TABLE_NOT_FOUND
* SELECT で fetchmany / description が正しく変換される
を検証する。
"""
from __future__ import annotations

from unittest import mock

import pytest

from thor.tools import _trino_client
from thor.tools._trino_client import map_trino_error
from thor.tools.trino import TrinoDDLTool, TrinoMetaTool, TrinoQueryTool
from thor.transport.user_context import (
    UserContext,
    reset_user_context,
    set_user_context,
)


@pytest.fixture
def auth_ctx():
    ctx = UserContext(user_name="alice", knox_jwt="fake-jwt")
    token = set_user_context(ctx)
    yield ctx
    reset_user_context(token)


def _make_fake_conn(
    rows: list[tuple] | None = None,
    description: list[tuple] | None = None,
    exc: Exception | None = None,
) -> mock.MagicMock:
    """cursor.execute → fetchmany/fetchall を再現する fake conn を返す。"""
    conn = mock.MagicMock()
    cur = conn.cursor.return_value
    if exc is not None:
        cur.execute.side_effect = exc
    cur.description = description or []
    cur.fetchmany.return_value = rows or []
    cur.fetchall.return_value = rows or []
    cur.fetchone.return_value = rows[0] if rows else None
    return conn


class TestTrinoQueryTool:
    def test_readonly_success(self, auth_ctx: UserContext) -> None:
        fake_conn = _make_fake_conn(
            rows=[("Alice", 30), ("Bob", 25)],
            description=[("name", "varchar", None, None, None, None, None),
                         ("age", "bigint", None, None, None, None, None)],
        )
        with mock.patch.object(
            _trino_client, "trino_connection_for_user", return_value=fake_conn
        ):
            result = TrinoQueryTool()._run(sql="SELECT name, age FROM users", max_rows=10)
        assert result["status"] == "ok"
        assert result["row_count"] == 2
        assert result["rows"][0] == ["Alice", 30]
        assert [c["name"] for c in result["columns"]] == ["name", "age"]

    def test_mutation_rejected(self, auth_ctx: UserContext) -> None:
        # DDL は trino_query では拒否
        result = TrinoQueryTool()._run(sql="CREATE TABLE t (x int)")
        assert result["status"] == "error"
        assert result["error_code"] == "TRINO_QUERY_FAILED"
        assert "trino_ddl" in result["message"].lower()

    def test_access_denied_maps_to_perm(self, auth_ctx: UserContext) -> None:
        fake_conn = _make_fake_conn(
            exc=Exception("Access Denied: Cannot select from table foo")
        )
        with mock.patch.object(
            _trino_client, "trino_connection_for_user", return_value=fake_conn
        ):
            result = TrinoQueryTool()._run(sql="SELECT * FROM foo")
        assert result["status"] == "error"
        assert result["error_code"] == "PERM_SELECT_DENIED"

    def test_table_not_found(self, auth_ctx: UserContext) -> None:
        fake_conn = _make_fake_conn(
            exc=Exception("Table 'iceberg.demo.missing' does not exist")
        )
        with mock.patch.object(
            _trino_client, "trino_connection_for_user", return_value=fake_conn
        ):
            result = TrinoQueryTool()._run(sql="SELECT * FROM missing")
        assert result["status"] == "error"
        assert result["error_code"] == "TRINO_TABLE_NOT_FOUND"


class TestTrinoDDLTool:
    def test_ddl_success(self, auth_ctx: UserContext) -> None:
        fake_conn = _make_fake_conn(description=[], rows=[])
        with mock.patch.object(
            _trino_client, "trino_connection_for_user", return_value=fake_conn
        ):
            result = TrinoDDLTool()._run(sql="CREATE TABLE demo.new_t (a int)")
        assert result["status"] == "ok"
        assert result["sql"].startswith("CREATE TABLE")

    def test_readonly_rejected(self, auth_ctx: UserContext) -> None:
        result = TrinoDDLTool()._run(sql="SELECT 1")
        assert result["status"] == "error"
        assert result["error_code"] == "TRINO_DDL_FAILED"

    def test_insert_returns_affected_rows(self, auth_ctx: UserContext) -> None:
        # INSERT は 1 行 (count,) を返す仕様
        fake_conn = _make_fake_conn(
            rows=[(42,)],
            description=[("rows", "bigint", None, None, None, None, None)],
        )
        with mock.patch.object(
            _trino_client, "trino_connection_for_user", return_value=fake_conn
        ):
            result = TrinoDDLTool()._run(sql="INSERT INTO t VALUES (1)")
        assert result["status"] == "ok"
        assert result["affected_rows"] == 42

    def test_create_denied(self, auth_ctx: UserContext) -> None:
        fake_conn = _make_fake_conn(
            exc=Exception("Access Denied: Cannot create table demo.foo")
        )
        with mock.patch.object(
            _trino_client, "trino_connection_for_user", return_value=fake_conn
        ):
            result = TrinoDDLTool()._run(sql="CREATE TABLE demo.foo (x int)")
        assert result["status"] == "error"
        assert result["error_code"] == "PERM_CREATE_DENIED"


class TestTrinoMetaTool:
    def test_returns_columns_and_stats(self, auth_ctx: UserContext) -> None:
        conn = mock.MagicMock()
        cur = conn.cursor.return_value
        # 1回目の execute: information_schema.columns
        # 2回目の execute: SHOW STATS
        call_rows = [
            [("id", "bigint", "NO"), ("name", "varchar", "YES")],
            [("id", 0.0, 1000.0, 0.0, None, None, None)],
        ]
        call_desc = [
            [("column_name", "varchar", None, None, None, None, None),
             ("data_type", "varchar", None, None, None, None, None),
             ("is_nullable", "varchar", None, None, None, None, None)],
            [("column_name", "varchar", None, None, None, None, None),
             ("data_size", "double", None, None, None, None, None),
             ("distinct_values_count", "double", None, None, None, None, None),
             ("nulls_fraction", "double", None, None, None, None, None),
             ("row_count", "double", None, None, None, None, None),
             ("low_value", "varchar", None, None, None, None, None),
             ("high_value", "varchar", None, None, None, None, None)],
        ]

        # 1 回目は fetchall (columns)、2 回目も fetchall (stats)
        fetchall_side = list(call_rows)
        cur.fetchall.side_effect = fetchall_side

        # description は毎回 execute のあとに参照される
        # execute 側で description を切替
        desc_iter = iter(call_desc)

        def _execute(*_a, **_kw):
            cur.description = next(desc_iter)

        cur.execute.side_effect = _execute

        with mock.patch.object(
            _trino_client, "trino_connection_for_user", return_value=conn
        ):
            result = TrinoMetaTool()._run(
                catalog="iceberg", schema="demo", table="sales"
            )
        assert result["status"] == "ok"
        assert result["fq_table_name"] == "iceberg.demo.sales"
        assert len(result["columns"]) == 2
        assert result["columns"][0] == {"name": "id", "type": "bigint", "nullable": False}
        assert result["columns"][1]["nullable"] is True
        assert len(result["stats"]) == 1

    def test_table_not_found_empty_columns(self, auth_ctx: UserContext) -> None:
        conn = mock.MagicMock()
        cur = conn.cursor.return_value
        cur.description = []
        cur.fetchall.return_value = []
        with mock.patch.object(
            _trino_client, "trino_connection_for_user", return_value=conn
        ):
            result = TrinoMetaTool()._run(
                catalog="iceberg", schema="demo", table="missing"
            )
        assert result["status"] == "error"
        assert result["error_code"] == "TRINO_TABLE_NOT_FOUND"


class TestMapTrinoError:
    def test_ddl_upper_prefix(self) -> None:
        r = map_trino_error(Exception("boom"), "DROP TABLE foo")
        assert r["error_code"] == "TRINO_DDL_FAILED"

    def test_select_query_fallback(self) -> None:
        r = map_trino_error(Exception("syntax boom"), "SELECT * FROM t")
        assert r["error_code"] == "TRINO_QUERY_FAILED"

    def test_perm_unknown_when_no_verb(self) -> None:
        r = map_trino_error(Exception("Access Denied"), "SHOW TABLES")
        assert r["error_code"] == "PERM_UNKNOWN"

    def test_sql_is_snipped(self) -> None:
        long_sql = "SELECT " + ", ".join(f"c{i}" for i in range(200)) + " FROM t"
        r = map_trino_error(Exception("boom"), long_sql)
        assert len(r["detail"]["sql_snippet"]) <= 303  # 300 + "..."

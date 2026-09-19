"""trino Python client を UserContext から組み立てるヘルパ。

Knox 経由の Trino Gateway に、エンドユーザーの Knox JWT を Bearer として渡す。
Trino 側は Knox が提示した JWT の ``sub`` クレームでユーザーを識別し、
Ranger の ACL 判定に使う (= エンドユーザー権限)。
"""
from __future__ import annotations

import os
from typing import Any, Union

from thor.transport.errors import ErrorCode, err
from thor.transport.user_context import UserContext

try:  # trino は本番依存
    import trino  # type: ignore
    from trino.auth import JWTAuthentication  # type: ignore
    from trino.exceptions import TrinoQueryError, TrinoUserError  # type: ignore
except ImportError:  # pragma: no cover
    trino = None  # type: ignore[assignment]

    class TrinoQueryError(Exception):  # type: ignore[no-redef]
        pass

    class TrinoUserError(TrinoQueryError):  # type: ignore[no-redef]
        pass


def trino_connection_for_user(
    user_ctx: UserContext,
    catalog: str = "iceberg",
    schema: Union[str, None] = None,
) -> Union[Any, dict[str, Any]]:
    """ユーザー権限で trino connection を返す。失敗時は err() dict。"""
    if trino is None:
        return err(ErrorCode.TRINO_QUERY_FAILED, "trino client is not installed")
    if not user_ctx.knox_jwt:
        return err(ErrorCode.AUTH_MISSING, "Knox JWT is required for Trino access")
    host = os.environ.get("THOR_TRINO_HOST")
    if not host:
        return err(
            ErrorCode.TRINO_QUERY_FAILED,
            "THOR_TRINO_HOST is not configured",
        )
    port = int(os.environ.get("THOR_TRINO_PORT", "443"))
    scheme = os.environ.get("THOR_TRINO_SCHEME", "https")
    verify_env = os.environ.get("THOR_TRINO_VERIFY_SSL", "true").lower()
    verify: Union[bool, str] = True
    if verify_env in ("false", "0", "no"):
        verify = False
    elif verify_env not in ("true", "1", "yes"):
        # ファイルパス指定 (CA バンドル)
        verify = verify_env
    conn = trino.dbapi.connect(
        host=host,
        port=port,
        http_scheme=scheme,
        user=user_ctx.user_name,
        auth=JWTAuthentication(user_ctx.knox_jwt),
        catalog=catalog,
        schema=schema,
        verify=verify,
        http_headers={
            "X-Thor-Request-Id": user_ctx.request_id,
        },
    )
    return conn


def map_trino_error(e: Exception, sql: str) -> dict[str, Any]:
    """trino の例外を ThorErrorResult に写像する。"""
    msg = str(e)
    lower = msg.lower()
    # 権限系
    if "access denied" in lower or "not authorized" in lower:
        if "select" in lower:
            return err(ErrorCode.PERM_SELECT_DENIED, msg, sql_snippet=_snip(sql))
        if "insert" in lower:
            return err(ErrorCode.PERM_INSERT_DENIED, msg, sql_snippet=_snip(sql))
        if "create" in lower:
            return err(ErrorCode.PERM_CREATE_DENIED, msg, sql_snippet=_snip(sql))
        return err(ErrorCode.PERM_UNKNOWN, msg, sql_snippet=_snip(sql))
    # 存在しないテーブル
    if "does not exist" in lower or "table not found" in lower:
        return err(ErrorCode.TRINO_TABLE_NOT_FOUND, msg, sql_snippet=_snip(sql))
    # DDL 系
    upper = sql.strip().upper()
    if upper.startswith(("CREATE", "DROP", "ALTER")):
        return err(ErrorCode.TRINO_DDL_FAILED, msg, sql_snippet=_snip(sql))
    return err(ErrorCode.TRINO_QUERY_FAILED, msg, sql_snippet=_snip(sql))


def _snip(sql: str, limit: int = 300) -> str:
    s = " ".join(sql.split())
    return s if len(s) <= limit else s[:limit] + "..."

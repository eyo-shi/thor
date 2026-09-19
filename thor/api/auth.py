"""FastAPI 依存性: リクエストから :class:`UserContext` を組み立てる。

Workbench Application は Knox 越しに配信されるため、リクエストヘッダから
Knox JWT + ``X-Forwarded-User`` を取り出し、Tool / Crew が使う ContextVar に
セットする。実際の Bearer / STS 発行はここではやらず、下流の
:mod:`thor.transport` 層が扱う。

**注意**: ここでは JWT の署名検証は行わない (Knox が済ませている前提)。
プロダクションで検証を強化したい場合は、この依存性の前段に FastAPI
middleware を差し込む。
"""
from __future__ import annotations

from typing import Annotated, Iterator, Optional

from fastapi import Depends, Header, HTTPException, Request

from thor.transport.auth import build_user_context_from_headers
from thor.transport.user_context import (
    UserContext,
    reset_user_context,
    set_user_context,
)


def _extract_headers(request: Request) -> dict[str, str]:
    """FastAPI Request からヘッダ dict を作る (Knox 転送ヘッダを網羅)。"""
    return {k: v for k, v in request.headers.items()}


def get_user_context(
    request: Request,
    x_thor_session_id: Annotated[Optional[str], Header()] = None,
) -> UserContext:
    """FastAPI 依存性: :class:`UserContext` を作って返す。

    * Knox JWT / X-Forwarded-User / X-Forwarded-Groups から抽出
    * ``X-Thor-Session-Id`` があればセッション ID として保持
    * ContextVar への set は :func:`user_context_scope` の役割 (SSE などの
      非依存経路で呼びやすいように分離)
    """
    headers = _extract_headers(request)
    return build_user_context_from_headers(
        headers, session_id=x_thor_session_id
    )


def require_user_context(
    ctx: Annotated[UserContext, Depends(get_user_context)],
) -> UserContext:
    """Knox JWT が取れなかった場合に 401 を返す厳格版。

    デモ環境で anonymous を許すなら :func:`get_user_context` を使う。
    Trino / S3 を叩くエンドポイントは基本これに置く。
    """
    if not ctx.knox_jwt:
        raise HTTPException(
            status_code=401,
            detail={
                "error_code": "AUTH_MISSING",
                "message": (
                    "Knox JWT is required. Access this app via the Workbench "
                    "Application URL so that Knox forwards a bearer token."
                ),
            },
        )
    return ctx


def user_context_scope(ctx: UserContext) -> Iterator[None]:
    """`with user_context_scope(ctx):` で ContextVar に一時セットする。

    FastAPI の Depends 経由で set/reset を自動化しにくい SSE や、明示的に
    Crew を kickoff する経路で使う。
    """
    token = set_user_context(ctx)
    try:
        yield
    finally:
        reset_user_context(token)

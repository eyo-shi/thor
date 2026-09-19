"""Knox JWT の抽出とデコード。

Cloudera Workbench Application は Knox の背後で動くため、既に Knox が
JWT の署名検証を済ませている。この層では **検証はしない**（Knox を信頼する）
が、`sub` / `preferred_username` などのクレームは取り出す。
"""
from __future__ import annotations

import base64
import json
from typing import Optional

import jwt

from thor.transport.user_context import UserContext

# Knox が転送してくるヘッダの候補 (デプロイ形態により異なる)
_JWT_HEADER_CANDIDATES = (
    "authorization",  # "Bearer <jwt>"
    "x-knox-jwt",
    "x-forwarded-access-token",
)
_USER_HEADER_CANDIDATES = (
    "x-forwarded-user",
    "x-remote-user",
    "cdsw-authenticated-user",
)
_GROUPS_HEADER_CANDIDATES = (
    "x-forwarded-groups",
    "x-remote-groups",
)


def _lowercase_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items()}


def _decode_jwt_payload_unverified(token: str) -> dict[str, object]:
    """署名検証なしで JWT のペイロードだけ取り出す。

    Knox が既に検証済みという前提。改ざん検知はしないので
    ヘッダ値をトラストする境界は FastAPI 側で明示する。
    """
    try:
        return jwt.decode(token, options={"verify_signature": False})
    except jwt.exceptions.PyJWTError:
        # フォールバック: 3 パート化して base64 decode を試みる
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        try:
            return json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}


def extract_jwt(headers: dict[str, str]) -> Optional[str]:
    """リクエストヘッダから Knox JWT を取り出す。"""
    lower = _lowercase_headers(headers)
    for h in _JWT_HEADER_CANDIDATES:
        raw = lower.get(h)
        if not raw:
            continue
        if h == "authorization":
            if raw.lower().startswith("bearer "):
                return raw[7:].strip() or None
            continue
        return raw.strip() or None
    return None


def build_user_context_from_headers(
    headers: dict[str, str],
    session_id: Optional[str] = None,
) -> UserContext:
    """FastAPI 依存性から呼び出す想定のファクトリ。

    Knox JWT が無い場合 (開発モード等) は "anonymous" で構築する。
    プロダクション設定では JWT 必須にする middleware を上に置く。
    """
    lower = _lowercase_headers(headers)
    jwt_token = extract_jwt(headers)

    # ユーザー名: 明示ヘッダ → JWT claim → "anonymous"
    user_name: Optional[str] = None
    for h in _USER_HEADER_CANDIDATES:
        if lower.get(h):
            user_name = lower[h].strip()
            break
    groups: tuple[str, ...] = ()
    for h in _GROUPS_HEADER_CANDIDATES:
        if lower.get(h):
            groups = tuple(g.strip() for g in lower[h].split(",") if g.strip())
            break

    if jwt_token and (not user_name or not groups):
        claims = _decode_jwt_payload_unverified(jwt_token)
        if not user_name:
            for claim_key in ("preferred_username", "sub", "user"):
                v = claims.get(claim_key)
                if isinstance(v, str) and v:
                    user_name = v
                    break
        if not groups:
            raw_groups = claims.get("groups") or claims.get("roles") or []
            if isinstance(raw_groups, list):
                groups = tuple(str(g) for g in raw_groups if g)

    return UserContext(
        user_name=user_name or "anonymous",
        groups=groups,
        knox_jwt=jwt_token,
        session_id=session_id,
    )


def bearer_header(ctx: UserContext) -> dict[str, str]:
    """`Authorization: Bearer <jwt>` を組み立てるユーティリティ。JWT 未設定なら空。"""
    if not ctx.knox_jwt:
        return {}
    return {"Authorization": f"Bearer {ctx.knox_jwt}"}

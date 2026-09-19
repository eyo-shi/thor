"""エンドユーザーの実行コンテキストを contextvars で伝搬する。

Cloudera AI Workbench Application は Knox 越しに配信されるため、
FastAPI ハンドラでリクエストごとに :class:`UserContext` を生成し、
:func:`set_user_context` で ContextVar にセットする。以降 Crew / Tool は
:func:`get_user_context` を呼ぶだけで現在のユーザー情報 (Knox JWT を含む)
にアクセスできる。

**厳守**: Knox JWT (:attr:`UserContext.knox_jwt`) や STS 短期資格情報を
LLM プロンプト、Crew.ai の Task context、標準出力ログに含めてはならない。
ログ出力は :mod:`thor.transport.logging` の redact プロセッサが `knox_jwt`,
`aws_secret_access_key`, `authorization` フィールドを自動でマスクする。
"""
from __future__ import annotations

import contextvars
import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class AwsCredentials:
    """IDBroker が発行する STS 短期資格情報。"""

    access_key_id: str
    secret_access_key: str
    session_token: str
    expiration_epoch: int  # UNIX epoch seconds


@dataclass(frozen=True)
class UserContext:
    """1 リクエスト / 1 Crew 実行で有効なユーザー情報。

    :attr:`request_id` はログ相関用の一意 ID。Crew.ai の Task.context には
    :attr:`user_name` と :attr:`groups` のみを渡し、資格情報は決して渡さない。
    """

    user_name: str
    groups: tuple[str, ...] = ()
    knox_jwt: Optional[str] = None
    aws_credentials: Optional[AwsCredentials] = None
    session_id: Optional[str] = None
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_llm_safe_dict(self) -> dict[str, object]:
        """LLM に見せてもよい辞書表現。認証情報は含めない。"""
        return {
            "user_name": self.user_name,
            "groups": list(self.groups),
            "session_id": self.session_id,
            "request_id": self.request_id,
        }


_user_ctx_var: contextvars.ContextVar[Optional[UserContext]] = contextvars.ContextVar(
    "thor_user_context", default=None
)


def set_user_context(ctx: UserContext) -> contextvars.Token[Optional[UserContext]]:
    """現在の実行スコープに :class:`UserContext` をセットする。

    戻り値の Token を :func:`reset_user_context` に渡すことで元に戻せる。
    FastAPI の依存性注入で使う場合は `try/finally` の finally で reset する。
    """
    return _user_ctx_var.set(ctx)


def reset_user_context(token: contextvars.Token[Optional[UserContext]]) -> None:
    _user_ctx_var.reset(token)


def get_user_context() -> UserContext:
    """現在のスコープの :class:`UserContext` を取得。未設定なら例外。"""
    ctx = _user_ctx_var.get()
    if ctx is None:
        raise RuntimeError(
            "UserContext is not set. FastAPI dependency or Crew kickoff wrapper "
            "must call set_user_context() before invoking any Tool."
        )
    return ctx


def get_user_context_optional() -> Optional[UserContext]:
    """設定されていなければ None。テストやウォームアップ用途。"""
    return _user_ctx_var.get()

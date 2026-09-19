"""Thor 共通 Tool 基底クラス。

crewai の :class:`BaseTool` を薄くラップし、以下を強制する:

* 呼び出し時に必ず :class:`UserContext` を参照する (未設定なら AUTH_MISSING エラー)
* 例外を送出せず、常に `{"status": "ok"|"error", ...}` の dict を返す
* Tool 名 / user_name / 引数 / 所要時間を構造化ログに記録

Tool 実装者は :meth:`run` (小文字) を override するだけでよい。crewai の
`_run` は自動でラップされる。
"""
from __future__ import annotations

import time
import traceback
from typing import Any

from thor.transport.errors import ErrorCode, err
from thor.transport.logging import get_logger
from thor.transport.user_context import UserContext, get_user_context_optional

try:  # crewai は本番依存だが、単体テスト用にフォールバックを用意
    from crewai.tools import BaseTool as _CrewBaseTool  # type: ignore
except Exception:  # pragma: no cover - crewai 未インストール時のフォールバック
    class _CrewBaseTool:  # type: ignore[no-redef]
        """crewai.tools.BaseTool のスタブ。名前と説明のみ持つ。"""

        name: str = ""
        description: str = ""

        def _run(self, *args: Any, **kwargs: Any) -> Any:
            raise NotImplementedError


_logger = get_logger(__name__)


class BaseThorTool(_CrewBaseTool):
    """Thor 内で使う CrewAI Tool の基底。

    サブクラスは以下を定義する:

    * :attr:`name` - CrewAI が Agent に見せる Tool 名
    * :attr:`description` - Agent 用のツール説明
    * :meth:`run` - 実処理。dict を返す (`ok(...)` / `err(...)` を使う)。
    """

    #: 認証必須か。False の場合、UserContext 未設定でも走らせる (テスト用)。
    requires_auth: bool = True

    def _run(self, **kwargs: Any) -> dict[str, Any]:
        # 認証チェック
        ctx = get_user_context_optional()
        if self.requires_auth and ctx is None:
            return err(
                ErrorCode.AUTH_MISSING,
                f"Tool {self.name!r} requires an authenticated user context.",
            )

        start = time.perf_counter()
        try:
            result = self.run(user_ctx=ctx, **kwargs)
        except Exception as e:  # noqa: BLE001 -- Tool は例外を上に投げない契約
            tb = traceback.format_exc(limit=5)
            _logger.error(
                "tool.exception",
                tool=self.name,
                error=str(e),
                traceback=tb,
                user=ctx.user_name if ctx else None,
            )
            return err(
                ErrorCode.HTTP_UNAVAILABLE,
                f"Tool {self.name!r} raised: {type(e).__name__}: {e}",
            )
        latency_ms = round((time.perf_counter() - start) * 1000, 1)

        # 結果の形をチェック (dict + status キー)
        if not isinstance(result, dict) or "status" not in result:
            _logger.error(
                "tool.bad_result_shape",
                tool=self.name,
                result_type=type(result).__name__,
            )
            return err(
                ErrorCode.HTTP_UNAVAILABLE,
                f"Tool {self.name!r} returned unexpected shape",
            )
        _logger.info(
            "tool.done",
            tool=self.name,
            status=result.get("status"),
            error_code=result.get("error_code"),
            latency_ms=latency_ms,
            user=ctx.user_name if ctx else None,
            args_keys=sorted(kwargs.keys()),
        )
        return result

    # サブクラスが実装する
    def run(self, user_ctx: UserContext | None, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError

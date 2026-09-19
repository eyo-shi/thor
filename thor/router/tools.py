"""Router Crew 用 Tool 定義。

* :class:`EntityMemoryReadTool` — `thor.api.state.InMemoryStore` から現在の
  セッションの ``entity_memory`` (last_table / last_dashboard_id / ...) を返す。
  IntentClassifierAgent が「そのテーブル」等の代名詞を解決するために使う。

* :class:`IngestionKickoffTool` — DispatcherAgent が子 Crew (Ingestion) を
  起動する際に使う。実際には :func:`kickoff_ingestion` を Python 側で呼ぶ
  ラッパで、LLM のツール呼び出しから起動できるようにしてある。

* :class:`AnalyticsKickoffTool` — 同上 (AnalyticsCrew 実装後に有効化)。
  現時点では ``NOT_IMPLEMENTED`` を返すスタブ。

**セキュリティ**: これらの Tool は Knox JWT / STS を LLM に露出させない。
UserContext は ContextVar から取り出し、Tool 引数には決して含めない。
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from thor.transport.errors import ErrorCode, err, ok
from thor.transport.logging import get_logger
from thor.transport.tool_base import BaseThorTool
from thor.transport.user_context import UserContext

_logger = get_logger(__name__)


# ------------------------------------------------------------------ #
# EntityMemoryReadTool
# ------------------------------------------------------------------ #
class EntityMemoryReadArgs(BaseModel):
    session_id: str = Field(..., description="対象セッション ID (X-Thor-Session-Id)")


class EntityMemoryReadTool(BaseThorTool):
    """会話セッションに保持された ``entity_memory`` スナップショットを返す。

    LLM は前ターンの結果 (最後に触れたテーブル・S3 パス・ダッシュボード ID)
    を参照して代名詞を解決するのに使う。値は文字列/数値のみで、
    Knox JWT や STS のような秘匿情報は絶対に含めない (`InMemoryStore` の
    :meth:`update_entity_memory` を通す限り原則安全)。
    """

    name: str = "entity_memory_read"
    description: str = (
        "Read the current chat session's entity memory (last_table, "
        "last_dashboard_id, last_s3_path, last_ossie_path, ...) so that "
        "you can resolve pronouns like 'そのテーブル' / 'that dashboard'."
    )
    args_schema: type[BaseModel] = EntityMemoryReadArgs
    requires_auth: bool = False  # session_id ベース。認可は API 層で済み

    def run(
        self,
        user_ctx: Optional[UserContext],
        session_id: str,
        **_: Any,
    ) -> dict[str, Any]:
        # 遅延 import で thor.api への循環参照を避ける (Tool は crewai から
        # 呼ばれる可能性があり、API 層より先に import されうる)
        from thor.api.state import get_store

        store = get_store()
        sess = store.get_session(session_id)
        if sess is None:
            return ok(
                {
                    "session_id": session_id,
                    "found": False,
                    "entity_memory": {},
                    "turn_count": 0,
                }
            )
        return ok(
            {
                "session_id": session_id,
                "found": True,
                "entity_memory": dict(sess.entity_memory),
                "turn_count": len(sess.turns),
            }
        )


# ------------------------------------------------------------------ #
# IngestionKickoffTool
# ------------------------------------------------------------------ #
class IngestionKickoffArgs(BaseModel):
    bucket: str = Field(..., min_length=1, description="S3 バケット名")
    key: str = Field(..., min_length=1, description="S3 オブジェクトキー")
    target_schema: str = Field(
        "demo", min_length=1, description="Trino/Iceberg スキーマ (default: demo)"
    )


class IngestionKickoffTool(BaseThorTool):
    """Ingestion Crew を 1 回起動する (DispatcherAgent 用)。

    LLM が使う想定だが、実質的には :func:`thor.ingestion.kickoff_ingestion`
    を呼ぶ薄いラッパ。長時間ブロックする点に注意 (SSE 経路では Python 側で
    直接 :func:`kickoff_ingestion` を呼ぶのが推奨)。
    """

    name: str = "ingestion_kickoff"
    description: str = (
        "Kick off the Ingestion Crew for a given s3://<bucket>/<key> and "
        "target Iceberg schema. Returns the final IngestionReport."
    )
    args_schema: type[BaseModel] = IngestionKickoffArgs

    def run(
        self,
        user_ctx: Optional[UserContext],
        bucket: str,
        key: str,
        target_schema: str = "demo",
        **_: Any,
    ) -> dict[str, Any]:
        if user_ctx is None:
            return err(ErrorCode.AUTH_MISSING, "IngestionKickoffTool requires user_ctx")
        # 遅延 import (thor.ingestion → thor.tools → BaseThorTool の循環回避)
        from thor.ingestion.crew import kickoff_ingestion

        _logger.info(
            "router.ingestion_kickoff",
            user=user_ctx.user_name,
            bucket=bucket,
            key=key,
            target_schema=target_schema,
        )
        return kickoff_ingestion(
            user_ctx=user_ctx,
            bucket=bucket,
            key=key,
            target_schema=target_schema,
        )


# ------------------------------------------------------------------ #
# AnalyticsKickoffTool  (Analytics Crew 未実装のためスタブ)
# ------------------------------------------------------------------ #
class AnalyticsKickoffArgs(BaseModel):
    mode: Literal["summary", "dashboard"] = Field(
        ..., description="summary は例3、dashboard は例2"
    )
    fq_table_name: str = Field(
        ..., min_length=1, description="対象 Iceberg テーブルの fq 名"
    )
    question: Optional[str] = Field(
        None, description="ユーザーの元の日本語質問 (Text2SQL のヒント)"
    )


class AnalyticsKickoffTool(BaseThorTool):
    """Analytics Crew を起動する (未実装のためスタブ)。

    Analytics Crew (thor.analytics) が実装され次第、Python 側から
    ``kickoff_analytics`` を呼ぶよう差し替える。それまでは
    ``NOT_IMPLEMENTED`` を返し、Dispatcher に上流でその旨を伝えさせる。
    """

    name: str = "analytics_kickoff"
    description: str = (
        "Kick off the Analytics Crew for summary or dashboard on the given "
        "fully-qualified Iceberg table. NOT YET IMPLEMENTED."
    )
    args_schema: type[BaseModel] = AnalyticsKickoffArgs

    def run(
        self,
        user_ctx: Optional[UserContext],
        mode: str,
        fq_table_name: str,
        question: Optional[str] = None,
        **_: Any,
    ) -> dict[str, Any]:
        return err(
            "NOT_IMPLEMENTED",
            f"AnalyticsCrew ({mode}) は未実装です。Ingestion のみ対応。",
            fq_table_name=fq_table_name,
        )


__all__ = [
    "EntityMemoryReadTool",
    "IngestionKickoffTool",
    "AnalyticsKickoffTool",
]

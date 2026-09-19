"""Router Crew の Task 出力スキーマ (Pydantic v2)。

Router は 2 タスク固定:

  1. classify_intent_task   -> IntentClassification
  2. dispatch_task          -> DispatchPlan

Intent は 5 分類:

  * ``INGEST``            — s3://... の取り込み依頼
  * ``ANALYZE_SUMMARY``   — 既存テーブルへの Markdown サマリー要求
  * ``ANALYZE_DASHBOARD`` — 既存テーブルへのダッシュボード生成要求
  * ``CHITCHAT``          — 挨拶・雑談・ヘルプ (子 Crew は起動しない)
  * ``UNKNOWN``           — 判定不能。needs_clarification=True で聞き返す

``extracted_args`` には子 Crew に渡すべき引数を LLM が抜いてくる:

  * INGEST:            ``bucket``, ``key``, ``target_schema`` (任意)
  * ANALYZE_*:         ``fq_table_name`` (代名詞なら entity_memory 参照後の解決値)

代名詞 ("そのテーブル" 等) は :class:`EntityMemoryReadTool` の結果を LLM が
自分で埋めることを期待。埋められなかった場合は ``needs_clarification=True``。
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


IntentLiteral = Literal[
    "INGEST",
    "ANALYZE_SUMMARY",
    "ANALYZE_DASHBOARD",
    "CHITCHAT",
    "UNKNOWN",
]


# ------------------------------------------------------------------ #
# 1. classify_intent
# ------------------------------------------------------------------ #
class IntentClassification(BaseModel):
    """ユーザー発話の意図分類結果。"""

    model_config = ConfigDict(extra="ignore")

    intent: IntentLiteral = Field(
        ..., description="INGEST / ANALYZE_SUMMARY / ANALYZE_DASHBOARD / CHITCHAT / UNKNOWN"
    )
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    extracted_args: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "子 Crew に渡す引数。INGEST は bucket/key/target_schema、"
            "ANALYZE_* は fq_table_name を含む。"
        ),
    )
    needs_clarification: bool = Field(
        False,
        description="True なら子 Crew を起動せず、clarification_prompt をユーザーに投げる",
    )
    clarification_prompt: Optional[str] = Field(
        None, description="needs_clarification=True の場合のみ、ユーザーへの再質問"
    )
    reasoning: Optional[str] = Field(
        None, description="LLM の判定根拠 (デバッグ / ログ用)"
    )


# ------------------------------------------------------------------ #
# 2. dispatch  (Router 内部で決定される ルーティングプラン)
# ------------------------------------------------------------------ #
class DispatchPlan(BaseModel):
    """`classify_intent` の結果を実行可能な子 Crew 呼び出しに変換した計画。

    実際の :func:`Crew.kickoff` は :func:`kickoff_router` の Python 側で
    行う (LLM に子 Crew の巨大な出力を読ませないため)。
    """

    model_config = ConfigDict(extra="ignore")

    intent: IntentLiteral
    child_crew: Literal["ingestion", "analytics_summary", "analytics_dashboard", "none"]
    inputs: dict[str, Any] = Field(default_factory=dict)
    response_markdown: str = Field(
        "",
        description="CHITCHAT / needs_clarification 時に ChatPane に直接返すテキスト",
    )
    skip_child: bool = Field(
        False,
        description="True の場合、Python 側は子 Crew を起動しない (雑談 or 再質問)",
    )


# ------------------------------------------------------------------ #
# トップレベル: kickoff_router() の戻り値
# ------------------------------------------------------------------ #
class RouterResult(BaseModel):
    """`kickoff_router` の呼び出し側 (SSE ハンドラ等) が受け取る結果。

    * ``classification`` — LLM (or heuristic) が付けた分類ラベル
    * ``plan``           — 子 Crew の起動計画
    * ``response_markdown`` — CHITCHAT / clarify 時にそのまま ChatPane に流す文字列
    """

    model_config = ConfigDict(extra="ignore")

    classification: IntentClassification
    plan: DispatchPlan
    response_markdown: str = ""

"""Router Crew の Agent 定義 (プラン準拠)。

  * ``IntentClassifierAgent`` — ユーザー発話を 5 分類のいずれかに割り振る。
    代名詞は :class:`EntityMemoryReadTool` で解決する。
  * ``DispatcherAgent``       — 分類結果に応じて子 Crew を起動する。
    (現時点で Analytics 子 Crew は未実装のためスタブ)

LLM は Router では軽量モデル (8B 相当) を割り当てる想定。Cloudera AI
Inference のエンドポイントを LiteLLM 経由で :class:`crewai.LLM` に載せる。
"""
from __future__ import annotations

from typing import Any, Optional

from thor.router.tools import (
    AnalyticsKickoffTool,
    EntityMemoryReadTool,
    IngestionKickoffTool,
)

try:  # crewai は本番依存。無い環境でも import は通す
    from crewai import Agent  # type: ignore
except Exception:  # pragma: no cover
    class Agent:  # type: ignore[no-redef]
        """crewai.Agent のスタブ。属性を保持するだけ。"""

        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)


# ------------------------------------------------------------------ #
# IntentClassifierAgent
# ------------------------------------------------------------------ #
def make_intent_classifier_agent(llm: Optional[Any] = None) -> Agent:
    """ユーザー発話の意図分類を担当する Agent。"""
    return Agent(
        role="Intent Classifier",
        goal=(
            "ユーザーの発話を INGEST / ANALYZE_SUMMARY / ANALYZE_DASHBOARD / "
            "CHITCHAT / UNKNOWN のいずれかに分類する。"
            "代名詞 (『そのテーブル』『さっきのダッシュボード』等) が現れたら "
            "entity_memory_read で last_table / last_dashboard_id を引き当てて "
            "extracted_args に埋める。判定不能な場合は needs_clarification=true "
            "とし、clarification_prompt に日本語の追加質問を書く。"
        ),
        backstory=(
            "Cloudera Data Warehouse と Data Visualization に精通した AI アシスタント。"
            "ユーザーの短い日本語プロンプトからも意図を的確に読み取り、"
            "曖昧なときは正直に聞き返すことができる。Knox JWT や STS 資格情報を"
            "決してプロンプトに含めない。"
        ),
        tools=[EntityMemoryReadTool()],
        llm=llm,
        allow_delegation=False,
        verbose=False,
        memory=False,
    )


# ------------------------------------------------------------------ #
# DispatcherAgent
# ------------------------------------------------------------------ #
def make_dispatcher_agent(llm: Optional[Any] = None) -> Agent:
    """分類結果を受けて子 Crew を起動する Agent。"""
    return Agent(
        role="Dispatcher",
        goal=(
            "IntentClassification の結果を DispatchPlan に変換する。"
            "INGEST なら child_crew='ingestion' + inputs={bucket, key, target_schema}、"
            "ANALYZE_SUMMARY なら 'analytics_summary'、"
            "ANALYZE_DASHBOARD なら 'analytics_dashboard'、"
            "CHITCHAT / needs_clarification なら skip_child=true にして "
            "response_markdown に日本語の返信文を書く。実際の子 Crew 起動は "
            "Python 側 (kickoff_router) が担当するので、この Agent は"
            "kickoff Tool を『使わずに』計画を出力するだけでよい。"
        ),
        backstory=(
            "分類 → 実行計画の変換を専門とするコーディネータ。過去の Task 出力を"
            "厳密な JSON 契約 (DispatchPlan) にはめ込むのが仕事。副作用は起こさず、"
            "Python 側の呼び出し口に安全にバトンを渡す。"
        ),
        # 子 Crew の kickoff は Python 側で行うので tools は補助用途 (フォールバック)。
        tools=[IngestionKickoffTool(), AnalyticsKickoffTool()],
        llm=llm,
        allow_delegation=False,
        verbose=False,
        memory=False,
    )


__all__ = [
    "make_intent_classifier_agent",
    "make_dispatcher_agent",
]

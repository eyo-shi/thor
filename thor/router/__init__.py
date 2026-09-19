"""Router Crew: ユーザー発話の intent 分類と子 Crew ディスパッチ。

プラン (:doc:`/design plan`) では 2 タスク Sequential:

  1. classify_intent_task — INGEST / ANALYZE_SUMMARY / ANALYZE_DASHBOARD /
     CHITCHAT / UNKNOWN の 5 分類
  2. dispatch_task        — 分類結果を DispatchPlan にはめ込む

実際の子 Crew (Ingestion / Analytics) 起動は Python 側 (:func:`kickoff_router`)
が行う。LLM 未接続環境では :func:`heuristic_classify` で決定論的に動く。

公開 API:
  - :func:`build_router_crew`  — crewai.Crew を組み立てる
  - :func:`kickoff_router`     — 1 発話を分類 + 計画化して返す
  - :func:`heuristic_classify` — LLM 不要の決定論的分類
  - :func:`build_dispatch_plan` — 分類結果から DispatchPlan を導出
  - :class:`IntentClassification`, :class:`DispatchPlan`, :class:`RouterResult`
"""
from thor.router.crew import (
    build_dispatch_plan,
    build_router_crew,
    heuristic_classify,
    kickoff_router,
)
from thor.router.models import (
    DispatchPlan,
    IntentClassification,
    IntentLiteral,
    RouterResult,
)

__all__ = [
    "build_router_crew",
    "kickoff_router",
    "heuristic_classify",
    "build_dispatch_plan",
    "IntentClassification",
    "DispatchPlan",
    "RouterResult",
    "IntentLiteral",
]

"""Analytics Crew — 既存 Iceberg テーブルに対する Summary / Dashboard 生成。

* :func:`kickoff_analytics_summary` — Sequential 2 タスク (Summary パス)
* :func:`kickoff_analytics_dashboard` — Sequential 4 タスク (Dashboard パス、
  Cloudera Data Visualization に Dataset / Visual / Dashboard を作成する)

Router は intent (ANALYZE_SUMMARY / ANALYZE_DASHBOARD) を分けて Python 側で
該当 kickoff を呼び分ける。両パスとも前処理の ``inspect_table_task`` を
共有し、権限不足や存在しないテーブルは guardrail で早期停止する。
"""
from thor.analytics.crew import (
    build_analytics_dashboard_crew,
    build_analytics_summary_crew,
    kickoff_analytics_dashboard,
    kickoff_analytics_summary,
)
from thor.analytics.models import (
    BuildDashboardResult,
    CDVStartupResult,
    InspectedColumn,
    SummaryObservation,
    SummaryReport,
    TableInspectionResult,
    VizPlan,
    VizProposal,
)

__all__ = [
    # crew
    "build_analytics_summary_crew",
    "kickoff_analytics_summary",
    "build_analytics_dashboard_crew",
    "kickoff_analytics_dashboard",
    # shared models
    "TableInspectionResult",
    "InspectedColumn",
    # summary models
    "SummaryReport",
    "SummaryObservation",
    # dashboard models
    "VizProposal",
    "VizPlan",
    "CDVStartupResult",
    "BuildDashboardResult",
]

"""Analytics Crew — 既存 Iceberg テーブルに対する Summary / Dashboard 生成。

現時点で実装済みは Summary パスのみ (プラン Task #4)。Dashboard パスは
CDV Adapter と VizPlanner の実装後に追加する (プラン Task #5)。

呼び出し側は :func:`kickoff_analytics_summary` を使う想定。
"""
from thor.analytics.crew import (
    build_analytics_summary_crew,
    kickoff_analytics_summary,
)
from thor.analytics.models import (
    InspectedColumn,
    SummaryObservation,
    SummaryReport,
    TableInspectionResult,
)

__all__ = [
    # crew
    "build_analytics_summary_crew",
    "kickoff_analytics_summary",
    # models
    "TableInspectionResult",
    "InspectedColumn",
    "SummaryReport",
    "SummaryObservation",
]

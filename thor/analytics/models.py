"""Analytics Crew の Task 出力スキーマ (Pydantic v2)。

現時点で実装される Summary パス:

  inspect_table_task     -> TableInspectionResult
  write_summary_task     -> SummaryReport

Dashboard パス (未実装, プレースホルダ):

  plan_viz_task          -> VizPlan
  ensure_cdv_running_task -> CDVStartupResult
  build_dashboard_task   -> BuildDashboardResult

すべての Task 出力は Pydantic v2 モデル。Task 間の受け渡しは ``context=[prev]``
経由で行い、LLM が壊れた JSON を出したときは Crew.ai が JSON 化を再要求する。

``TableInspectionResult`` は Summary / Dashboard の両パスで共有する前処理結果。
権限不足や存在しないテーブルは ``has_select_priv=False`` / ``exists=False``
+ ``error_code`` で表現し、Sequential Crew の guardrail が Crew を停止する。
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# ------------------------------------------------------------------ #
# 共通: 1. inspect_table
# ------------------------------------------------------------------ #
class InspectedColumn(BaseModel):
    """1 カラム分のメタ + 統計情報。"""

    name: str
    trino_type: str
    nullable: bool = True
    null_ratio: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="SHOW STATS の nulls_fraction"
    )
    distinct_count: Optional[int] = Field(
        None, ge=0, description="SHOW STATS の distinct_values_count"
    )
    low_value: Optional[Any] = Field(None, description="SHOW STATS の low_value")
    high_value: Optional[Any] = Field(None, description="SHOW STATS の high_value")
    role: str = Field(
        "dimension",
        description="dimension / measure / time — Ossie にあればそれを、無ければ型から推定",
    )


class TableInspectionResult(BaseModel):
    """テーブル存在確認・権限・カラム統計・Ossie 有無をまとめた前処理結果。

    guardrail はこの結果を見て以降の Task を止める:

      * ``exists=False`` → 中断 (TRINO_TABLE_NOT_FOUND)
      * ``has_select_priv=False`` → 中断 (PERM_SELECT_DENIED)
    """

    # ``schema_`` は Pydantic の予約語 (BaseModel.schema) を避けるため。
    # LLM / API 側では ``schema`` として読み書きする。
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    fq_table_name: str = Field(..., description="catalog.schema.table")
    catalog: str
    schema_: str = Field(..., alias="schema")
    table: str

    exists: bool = True
    has_select_priv: bool = True
    error_code: Optional[str] = None
    message: Optional[str] = None

    columns: list[InspectedColumn] = Field(default_factory=list)
    row_count_estimate: Optional[int] = Field(
        None, ge=0, description="SHOW STATS の row_count (全カラム共通行の統計)"
    )

    # Ossie 情報 (無ければ None のまま — Summary は LLM に「無い」と教える)
    ossie_yaml_path: Optional[str] = None
    ossie_description: Optional[str] = None
    ossie_dimensions: list[str] = Field(default_factory=list)
    ossie_measures: list[str] = Field(default_factory=list)

    # LLM が具体例を出せるように 5-20 行の生サンプルを付ける
    sample_rows: list[dict[str, Any]] = Field(default_factory=list)


# ------------------------------------------------------------------ #
# Summary パス: 2. write_summary
# ------------------------------------------------------------------ #
class SummaryObservation(BaseModel):
    """LLM が抽出した 1 個の観察点 (簡潔なタグ + 詳細)。"""

    category: str = Field(
        ..., description="distribution / trend / outlier / quality / relationship"
    )
    headline: str = Field(..., description="1 行で言い切る観察 (例: '東京の売上が全体の 42%')")
    detail: Optional[str] = None


class SummaryReport(BaseModel):
    """ユーザー向けの最終 Markdown サマリー。

    ``summary_markdown`` はチャット & Result Pane にそのまま出す本文。
    ``observations`` / ``warnings`` / ``sample_queries`` はストラクチャード
    情報で、後段 (Dashboard 生成の Text2SQL 例) が再利用できる形にしておく。
    """

    model_config = ConfigDict(extra="ignore")

    fq_table_name: str
    summary_markdown: str = Field(
        ..., description="ChatPane に流す日本語 Markdown 本文 (5-15 行)"
    )
    observations: list[SummaryObservation] = Field(default_factory=list)
    warnings: list[str] = Field(
        default_factory=list,
        description="欠損の多いカラム、サンプル 0 行、統計未収集などの注意点",
    )
    sample_queries: list[dict[str, str]] = Field(
        default_factory=list,
        description="[{'question': '...', 'sql': '...'}] の Few-shot 候補 (2-3 件)",
    )


# ------------------------------------------------------------------ #
# Dashboard パス: 3 タスクの出力スキーマ
# ------------------------------------------------------------------ #
class VizProposal(BaseModel):
    """VizPlan 内の 1 個の Visual 定義。

    VizHeuristicTool が機械的に組んだ後、VizPlannerAgent (LLM) が名前を
    日本語化したり不要な Visual を落としたりしてから最終確定させる。
    """

    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., description="ユーザーに見せる Visual 名 (日本語 OK)")
    viz_type: str = Field(..., description="line | bar | pie | kpi | table")
    x: Optional[str] = Field(None, description="X 軸カラム (line/bar/pie/table で使用)")
    y: Optional[str] = Field(None, description="Y 軸カラム / measure (line/bar/kpi で必須)")
    aggregation: str = Field(
        "sum", description="sum | avg | count | min | max"
    )
    group_by: Optional[str] = Field(
        None, description="第 2 分岐カラム (色分けなどに使う)"
    )
    description: Optional[str] = Field(
        None, description="この Visual の意図を 1 行で"
    )


class VizPlan(BaseModel):
    """VizPlanner の出力。Dashboard で並べる Visual の設計図。"""

    model_config = ConfigDict(extra="ignore")

    fq_table_name: str
    title: str = Field(..., description="ダッシュボード全体のタイトル")
    visuals: list[VizProposal] = Field(default_factory=list, min_length=1)


class CDVStartupResult(BaseModel):
    """CDV 疎通確認の結果。

    ``running=False`` は Dashboard Crew の guardrail が Crew を停止させる材料。
    ユーザーへの案内 (Workbench Data メニューから CDV を初回起動) は
    ``message`` に載せる。
    """

    model_config = ConfigDict(extra="ignore")

    running: bool
    endpoint: Optional[str] = None
    version: Optional[str] = None
    message: Optional[str] = None
    error_code: Optional[str] = None


class BuildDashboardResult(BaseModel):
    """最終ダッシュボード。UI (ResultPane) が dashboard_url を iframe で開く。"""

    model_config = ConfigDict(extra="ignore")

    fq_table_name: str
    title: str
    dashboard_id: str
    dashboard_url: str = Field(
        ..., description="iframe 埋め込み可能な CDV URL (Knox セッション継承)"
    )
    dataset_id: str
    dataset_reused: bool = Field(
        False, description="既存 Dataset を再利用したか (True) 新規作成か (False)"
    )
    visual_ids: list[str] = Field(default_factory=list, min_length=1)
    notes: Optional[str] = Field(
        None, description="ユーザーへの補足 (置換対象カラムが無かった等)"
    )


__all__ = [
    # 共通
    "InspectedColumn",
    "TableInspectionResult",
    # Summary
    "SummaryObservation",
    "SummaryReport",
    # Dashboard
    "VizProposal",
    "VizPlan",
    "CDVStartupResult",
    "BuildDashboardResult",
]

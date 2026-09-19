"""Analytics Crew の Agent 定義。

プラン準拠の役割分担:

  Summary パス:
    TableInspectorAgent  -> TrinoQueryTool, TrinoMetaTool, OssieReadTool
    SummaryWriterAgent   -> OssieReadTool  (LLM 主体)

  Dashboard パス:
    TableInspectorAgent  (Summary と共通)
    VizPlannerAgent      -> VizHeuristicTool, OssieReadTool
    DashboardBuilderAgent -> CDVStartupCheckTool, CDVDatasetTool,
                             CDVVisualTool, CDVDashboardTool

Text2SQL は現状 SummaryWriter / VizPlanner のプロンプト内で完結するので
独立 Agent としては置かない (プランでは分離していたが、実運用の 4 タスク
Sequential では過剰なホップになるため 3 タスクに集約)。
"""
from __future__ import annotations

from typing import Any, Optional

from thor.tools import (
    CDVDashboardTool,
    CDVDatasetTool,
    CDVStartupCheckTool,
    CDVVisualTool,
    OssieReadTool,
    TrinoMetaTool,
    TrinoQueryTool,
    VizHeuristicTool,
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
# TableInspectorAgent
# ------------------------------------------------------------------ #
def make_table_inspector_agent(llm: Optional[Any] = None) -> Agent:
    """既存テーブルの存在確認・権限確認・統計取得・Ossie 参照を行う Agent。

    Summary / Dashboard の両パスで前処理として使う。副作用なし。
    """
    return Agent(
        role="Table Inspector",
        goal=(
            "指示された fq_table_name (catalog.schema.table) について、"
            "TrinoMetaTool で カラム定義と SHOW STATS を取得し、"
            "OssieReadTool で該当データセットの Ossie YAML があるか確認し、"
            "TrinoQueryTool で先頭 20 行のサンプルを取得する。"
            "SELECT 権限が無い場合は has_select_priv=false と "
            "error_code=PERM_SELECT_DENIED を返し、後段を止める。"
            "テーブルが存在しないなら exists=false と "
            "error_code=TRINO_TABLE_NOT_FOUND を返す。"
        ),
        backstory=(
            "Cloudera Data Warehouse (Trino/Iceberg) の運用に習熟したデータ"
            "エンジニアで、Ranger が返す権限エラーを正しく分類する。"
            "統計が未収集のテーブルは SHOW STATS が NULL を返すため、"
            "row_count_estimate は None のまま返して SummaryWriter に判断"
            "を委ねる。"
        ),
        tools=[TrinoMetaTool(), TrinoQueryTool(), OssieReadTool()],
        llm=llm,
        allow_delegation=False,
        verbose=False,
        memory=False,
    )


# ------------------------------------------------------------------ #
# SummaryWriterAgent
# ------------------------------------------------------------------ #
def make_summary_writer_agent(llm: Optional[Any] = None) -> Agent:
    """カラム統計とサンプル行から、日本語の Markdown サマリーを書く Agent。

    副作用なし。LLM 主体。TrinoQueryTool は使わない
    (追加クエリを避け、TableInspector が集めた情報のみで書き切る)。
    """
    return Agent(
        role="Summary Writer",
        goal=(
            "TableInspectionResult のカラム統計 (null_ratio, distinct_count, "
            "low_value, high_value) とサンプル行から、テーブルの内容を "
            "5-15 行の日本語 Markdown で要約する。"
            "見出しレベルは ## から。"
            "分布・トレンド・外れ値・データ品質の 4 観点をカバーし、"
            "根拠のない断定は避ける (統計が None のカラムは 'ヒント不足' と明示)。"
            "Ossie が存在するテーブルは、Ossie の description / dimensions / "
            "measures をそのまま踏まえた上で追加情報を書く。"
            "最後に「次に見ると良さそうな SQL」を 2-3 件、"
            "question と sql の組で提案する (Text2SQL の Few-shot に使う想定)。"
        ),
        backstory=(
            "ビジネスサイドとエンジニアの両方から尊敬されるデータアナリスト。"
            "統計が乏しくても分かることだけを丁寧に書き、事実と推測を分けて示す。"
            "個人情報らしきカラム名 (email, phone, address 等) は具体値を"
            "サマリーに書かず、'PII の可能性あり' と警告に回す。"
        ),
        tools=[OssieReadTool()],
        llm=llm,
        allow_delegation=False,
        verbose=False,
        memory=False,
    )


# ------------------------------------------------------------------ #
# VizPlannerAgent (Dashboard パス)
# ------------------------------------------------------------------ #
def make_viz_planner_agent(llm: Optional[Any] = None) -> Agent:
    """テーブルからダッシュボード構成 (VizPlan) を提案する Agent。

    * まず :class:`VizHeuristicTool` を呼んで機械的な候補を得る
      (時系列 line / カテゴリ bar / KPI など)。
    * その後 LLM が候補の名前を日本語化 (「revenue の推移」→「月次売上の推移」)
      し、意味の薄い Visual を落として最大 4-5 個に絞る。
    * Ossie に ``sample_queries`` があれば、その意図に沿うように優先順位を調整。
    """
    return Agent(
        role="Viz Planner",
        goal=(
            "TableInspectionResult のカラム情報から、ダッシュボードで見せる "
            "Visual の集合 (VizPlan) を提案する。まず viz_heuristic を呼んで "
            "候補を得て、その後 Ossie の description と sample_queries を "
            "踏まえて Visual 名を日本語化し、意味の薄いものを落として 3-5 個に絞る。"
            "破壊系のカラム操作は行わない (SELECT / 集計のみ)。"
        ),
        backstory=(
            "BI 経験の長いアナリスト。まず 'これがあれば一目で分かる' Visual を"
            "選び、無闇に増やさない。KPI・時系列・カテゴリ分解の 3 パターンで "
            "たいていのニーズは満たせると信じている。"
        ),
        tools=[VizHeuristicTool(), OssieReadTool()],
        llm=llm,
        allow_delegation=False,
        verbose=False,
        memory=False,
    )


# ------------------------------------------------------------------ #
# DashboardBuilderAgent (Dashboard パス)
# ------------------------------------------------------------------ #
def make_dashboard_builder_agent(llm: Optional[Any] = None) -> Agent:
    """CDV に対して Dataset / Visual / Dashboard を実際に作る Agent。

    副作用のある API を叩くため、Task 側では ``max_retries=0`` を必須にする
    (Crew.ai の再試行で同じ Dashboard が 2 個作られるのを防ぐ)。
    """
    return Agent(
        role="Dashboard Builder",
        goal=(
            "VizPlan と TableInspectionResult を受け取り、Cloudera Data "
            "Visualization に対して以下を順に実行する: "
            "  (1) cdv_startup_check で CDV が起動しているか確認、"
            "      running=false なら CDV_NOT_RUNNING エラーで停止。"
            "  (2) cdv_dataset_create_or_get で fq_table_name の Dataset を"
            "      作成 or 既存を再利用。"
            "  (3) VizPlan の各 visual について cdv_visual_create を呼び、"
            "      visual_id を集める。失敗した Visual はスキップし、成功分だけで"
            "      Dashboard を組む。"
            "  (4) cdv_dashboard_create で全 visual_id を 1 つのダッシュボードに"
            "      集約。title は VizPlan.title を使う。"
            "  (5) 最終出力 BuildDashboardResult に dashboard_id / dashboard_url / "
            "      dataset_id / visual_ids を詰める。"
        ),
        backstory=(
            "Cloudera Data Visualization の管理経験があるエンジニア。"
            "CDV は Dataset 名 + Connection ID で一意なので、既存があれば必ず"
            "再利用する (毎回作ると管理画面が Dataset で溢れる)。"
            "Visual 作成で 1 個失敗しても Dashboard は残りで組み、"
            "notes フィールドに 'Visual X はスキップ' と書き残す。"
        ),
        tools=[
            CDVStartupCheckTool(),
            CDVDatasetTool(),
            CDVVisualTool(),
            CDVDashboardTool(),
        ],
        llm=llm,
        allow_delegation=False,
        verbose=False,
        memory=False,
    )


__all__ = [
    "make_table_inspector_agent",
    "make_summary_writer_agent",
    "make_viz_planner_agent",
    "make_dashboard_builder_agent",
]

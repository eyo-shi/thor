"""Analytics Crew の Task 定義。

**Summary パス** (Sequential 2 タスク):
  1. inspect_table_task    (TableInspector, guardrail 付き)
  2. write_summary_task    (SummaryWriter, LLM 主体)

**Dashboard パス** (Sequential 4 タスク):
  1. inspect_table_task    (TableInspector, guardrail 付き) — Summary と共通
  2. plan_viz_task         (VizPlanner, LLM が VizHeuristic の結果を絞る)
  3. ensure_cdv_running_task (DashboardBuilder, guardrail 付き — running=False で停止)
  4. build_dashboard_task  (DashboardBuilder, max_retries=0 で副作用重複を防ぐ)

副作用なしのタスクは max_retries=1、CDV に書き込むタスクは max_retries=0。
"""
from __future__ import annotations

from typing import Any, Optional

from thor.analytics.models import (
    BuildDashboardResult,
    CDVStartupResult,
    SummaryReport,
    TableInspectionResult,
    VizPlan,
)

try:
    from crewai import Task  # type: ignore
except Exception:  # pragma: no cover
    class Task:  # type: ignore[no-redef]
        """crewai.Task のスタブ。属性を保持するだけ。"""

        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)


# ------------------------------------------------------------------ #
# 1. inspect_table_task
# ------------------------------------------------------------------ #
def make_inspect_table_task(agent: Any) -> Task:
    """存在確認・権限・統計・Ossie 参照を 1 タスクにまとめる。

    LLM が実行順を間違えないよう、description に手順を明示。
    """
    return Task(
        description=(
            "対象テーブル fq_table_name={fq_table_name} を調査せよ。"
            "以下の順で必ず実行する: "
            "(a) fq_table_name を catalog / schema / table にパースする "
            "(3 セグメント想定、'iceberg.demo.sales_2024' → catalog='iceberg', "
            "schema='demo', table='sales_2024')。"
            "(b) trino_meta で columns と SHOW STATS を取る。"
            "  権限不足なら error_code=PERM_SELECT_DENIED、"
            "  テーブル未存在なら error_code=TRINO_TABLE_NOT_FOUND を返し、"
            "  それ以外のフィールドは空/None で構わない。"
            "(c) ossie_read で fq_name={fq_table_name} を読み、"
            "  存在すれば description / dimensions / measures を取り出す "
            "  (無くても error にせず、ossie_yaml_path=None で継続する)。"
            "(d) trino_query で 'SELECT * FROM {fq_table_name} LIMIT 20' を実行し、"
            "  sample_rows に詰める。0 行でも error にはしない。"
            "(e) 各カラムの role は Ossie に一致名があればそれを、"
            "  無ければ trino_type から 'measure' (数値系) / 'time' (date/timestamp) / "
            "  'dimension' (それ以外) を選ぶ。"
        ),
        expected_output=(
            "TableInspectionResult の JSON。fq_table_name, catalog, schema, "
            "table, exists, has_select_priv, error_code, message, columns "
            "(name/trino_type/nullable/null_ratio/distinct_count/low_value/"
            "high_value/role), row_count_estimate, ossie_yaml_path, "
            "ossie_description, ossie_dimensions, ossie_measures, sample_rows "
            "を含む。"
        ),
        agent=agent,
        output_json=TableInspectionResult,
        max_retries=1,
    )


# ------------------------------------------------------------------ #
# 2. write_summary_task
# ------------------------------------------------------------------ #
def make_write_summary_task(agent: Any, context: list[Task]) -> Task:
    """LLM が Markdown サマリーを書き切るタスク。

    guardrail (inspect_table) を通過している前提。ここでは追加の Trino
    アクセスはせず、context の統計とサンプル行だけで書く。
    """
    return Task(
        description=(
            "前タスクの TableInspectionResult を踏まえ、日本語 Markdown で "
            "テーブル内容を 5-15 行程度に要約せよ。"
            "含めるべき観点 (それぞれ 1-3 行): "
            "  1. 全体像 (行数目安・カラム数・主な dimension と measure)。"
            "     row_count_estimate が None のときは「統計未収集」と明示。"
            "  2. 分布 (distinct_count が小さい dimension は代表値を列挙、"
            "     数値カラムは low_value / high_value を提示)。"
            "  3. データ品質 (null_ratio が 0.2 以上のカラムは列挙、"
            "     sample_rows が空なら「サンプル取得 0 行」と警告)。"
            "  4. Ossie に description があればそれを冒頭に置き、"
            "     無ければ「セマンティックレイヤ未整備」と 1 行で触れる。"
            "  5. 個人情報が疑われるカラム名 (email/phone/address/name 等) は "
            "     具体値を書かず warnings に回す。"
            "最後に sample_queries として "
            "'question (日本語) と sql (Trino SELECT)' の組を 2-3 件提案。"
            "SQL は fq_table_name={fq_table_name} を使い、"
            "破壊系 (INSERT/UPDATE/DELETE/CREATE/DROP) は絶対に含めない。"
        ),
        expected_output=(
            "SummaryReport の JSON。fq_table_name, summary_markdown, "
            "observations (category/headline/detail), warnings, sample_queries "
            "([{question, sql}]) を含む。"
        ),
        agent=agent,
        context=context,
        output_json=SummaryReport,
        max_retries=1,
    )


# ------------------------------------------------------------------ #
# guardrail helper
# ------------------------------------------------------------------ #
def inspect_table_guardrail(
    output: Any,
) -> tuple[bool, Optional[str]]:
    """``inspect_table_task`` の結果を検査するガードレール。

    Crew.ai の Task には ``guardrail`` パラメータがあり、``(ok, feedback)``
    を返す関数を受け取る。ok=False で Crew は次タスクに進まず停止する。

    テーブル未存在 / SELECT 権限なしの場合、SummaryWriter を呼ばずに終了する。
    (LLM トークンの無駄と、ユーザーへの誤解を招くサマリーの生成を防ぐ)。
    """
    if isinstance(output, TableInspectionResult):
        result = output
    elif isinstance(output, dict):
        try:
            result = TableInspectionResult.model_validate(output)
        except Exception:  # noqa: BLE001
            return False, f"guardrail: could not parse output: {output!r}"
    else:
        return False, f"guardrail: unexpected output type: {type(output).__name__}"

    if not result.exists:
        return False, (
            f"Table {result.fq_table_name!r} not found: "
            f"{result.message or 'no such table'}. Ask the user to verify the name."
        )
    if not result.has_select_priv:
        return False, (
            f"SELECT permission denied on {result.fq_table_name!r}: "
            f"{result.message or 'contact the workspace admin'}."
        )
    return True, None


# ------------------------------------------------------------------ #
# Dashboard パス: 3. plan_viz_task
# ------------------------------------------------------------------ #
def make_plan_viz_task(agent: Any, context: list[Task]) -> Task:
    """VizHeuristicTool の候補を LLM が絞り込んで VizPlan にするタスク。

    副作用なし。VizHeuristic は決定論的なので、LLM の仕事は「候補の名前を
    日本語化する」「意味の薄い Visual を落とす」「タイトルを付ける」の 3 つ。
    """
    return Task(
        description=(
            "前タスクの TableInspectionResult のカラム情報から、"
            "以下の手順でダッシュボード構成 (VizPlan) を作れ: "
            "(a) viz_heuristic に fq_table_name と columns "
            "  (name / trino_type / role / distinct_count / null_ratio) を渡し、"
            "  最大 6 個の Visual 候補を得る。"
            "(b) ossie_read で fq_name={fq_table_name} を読み、"
            "  存在すれば description / sample_queries を参照する。"
            "(c) 候補のうち意味の薄いもの (低カーディナリティ dim × 別 dim など) "
            "  を落として 3-5 個に絞り、各 Visual の name を日本語に整える "
            "  (例: 'revenue の推移' → '月次売上の推移')。"
            "(d) title は 'このテーブルは何のダッシュボードか' が伝わる短い日本語に。"
            "破壊系のカラム操作は絶対に含めず、SELECT / 集計に限定する。"
        ),
        expected_output=(
            "VizPlan の JSON。fq_table_name, title, visuals ("
            "[{name, viz_type, x, y, aggregation, group_by, description}]) を含む。"
            "visuals は最低 1 個。"
        ),
        agent=agent,
        context=context,
        output_json=VizPlan,
        max_retries=1,
    )


# ------------------------------------------------------------------ #
# Dashboard パス: 4. ensure_cdv_running_task
# ------------------------------------------------------------------ #
def make_ensure_cdv_running_task(agent: Any, context: list[Task]) -> Task:
    """CDV 疎通確認。running=False なら guardrail で Crew を停止する。

    副作用なし (GET のみ)。max_retries=1。
    """
    return Task(
        description=(
            "cdv_startup_check を 1 回だけ呼び、Cloudera Data Visualization が"
            "起動しているか確認せよ。"
            "返り値をそのまま CDVStartupResult として出力する:"
            "  running=true なら次のタスク (build_dashboard) へ進める。"
            "  running=false なら error_code=CDV_NOT_RUNNING と、ユーザーへの"
            "  案内 (Workbench の Data メニューから CDV を初回起動して欲しい旨) を"
            "  message に載せる。"
            "起動していれば endpoint と version をそのまま埋める。"
        ),
        expected_output=(
            "CDVStartupResult の JSON。running, endpoint, version, message, "
            "error_code を含む。"
        ),
        agent=agent,
        context=context,
        output_json=CDVStartupResult,
        max_retries=1,
    )


# ------------------------------------------------------------------ #
# Dashboard パス: 5. build_dashboard_task
# ------------------------------------------------------------------ #
def make_build_dashboard_task(agent: Any, context: list[Task]) -> Task:
    """CDV に Dataset / Visual / Dashboard を実際に作るタスク。

    副作用があるため max_retries=0。個別 Visual が 1 個失敗しても
    残った Visual で Dashboard を組んで notes に記録する。
    """
    return Task(
        description=(
            "VizPlan と TableInspectionResult を使い、Cloudera Data Visualization"
            "に対して以下を順に実行せよ: "
            "(a) cdv_dataset_create_or_get に fq_table_name を渡して"
            "  Dataset を確保する (既存なら再利用)。dataset_id と "
            "  dataset_reused (created の否定) を控えておく。"
            "(b) VizPlan.visuals の各 visual について cdv_visual_create を呼び、"
            "  visual_id を集める。"
            "  - viz_type=line/bar: x と y は必須。"
            "  - viz_type=kpi: y (measure) 必須、x は None。"
            "  - viz_type=pie: x 必須、y は不要 (aggregation=count)。"
            "  - viz_type=table: x/y ともに不要。"
            "  1 個失敗しても他は続行、失敗内容は notes に列挙する。"
            "(c) cdv_dashboard_create に title=VizPlan.title と "
            "  visual_ids (成功分) を渡し、dashboard_id と dashboard_url を得る。"
            "(d) 最終出力 BuildDashboardResult に "
            "  fq_table_name / title / dashboard_id / dashboard_url / "
            "  dataset_id / dataset_reused / visual_ids / notes を詰める。"
            "同じダッシュボードを二重に作らないこと (このタスクは max_retries=0)。"
        ),
        expected_output=(
            "BuildDashboardResult の JSON。fq_table_name, title, dashboard_id, "
            "dashboard_url, dataset_id, dataset_reused, visual_ids, notes を含む。"
        ),
        agent=agent,
        context=context,
        output_json=BuildDashboardResult,
        max_retries=0,
    )


# ------------------------------------------------------------------ #
# Dashboard パス: guardrail
# ------------------------------------------------------------------ #
def ensure_cdv_running_guardrail(
    output: Any,
) -> tuple[bool, Optional[str]]:
    """CDVStartupResult を検査し、running=False で Crew を停止する。

    Crew.ai の guardrail 契約は ``(ok, feedback)``。ok=False で後段が走らない。
    """
    if isinstance(output, CDVStartupResult):
        result = output
    elif isinstance(output, dict):
        try:
            result = CDVStartupResult.model_validate(output)
        except Exception:  # noqa: BLE001
            return False, f"guardrail: could not parse CDVStartupResult: {output!r}"
    else:
        return False, (
            f"guardrail: unexpected CDVStartupResult type: {type(output).__name__}"
        )

    if not result.running:
        return False, (
            f"CDV is not running at {result.endpoint or '(unknown)'}: "
            f"{result.message or 'startup check failed'}. "
            "Ask the user to start Cloudera Data Visualization from the "
            "Workbench Data menu once."
        )
    return True, None


__all__ = [
    "make_inspect_table_task",
    "make_write_summary_task",
    "make_plan_viz_task",
    "make_ensure_cdv_running_task",
    "make_build_dashboard_task",
    "inspect_table_guardrail",
    "ensure_cdv_running_guardrail",
]

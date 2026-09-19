"""AnalyticsCrew (Summary パス) の配線テスト。

LLM を呼ばずに以下だけを確認する:

  * 2 Agent が正しい Tool set を持って生成される
  * 2 Task が Sequential で組まれ、output_json が Pydantic モデル
  * guardrail 関数が期待通り (exists=False / has_select_priv=False で blocked、
    正常系で passed) を返す
  * kickoff_analytics_summary は crewai 未インストール環境でも構造化エラーで返る
  * fq_table_name が 3 セグメントでないときは LLM を回さず即座にエラーで返る

crewai が入っていない環境では :class:`Agent` / :class:`Task` / :class:`Crew`
はスタブになる。スタブでも属性チェックは全て通るように設計してある。
"""
from __future__ import annotations

from thor.analytics.agents import (
    make_dashboard_builder_agent,
    make_summary_writer_agent,
    make_table_inspector_agent,
    make_viz_planner_agent,
)
from thor.analytics.crew import (
    build_analytics_dashboard_crew,
    build_analytics_summary_crew,
    kickoff_analytics_dashboard,
    kickoff_analytics_summary,
)
from thor.analytics.models import (
    BuildDashboardResult,
    CDVStartupResult,
    SummaryObservation,
    SummaryReport,
    TableInspectionResult,
    VizPlan,
    VizProposal,
)
from thor.analytics.tasks import (
    ensure_cdv_running_guardrail,
    inspect_table_guardrail,
)
from thor.tools.viz import VizHeuristicTool
from thor.transport.user_context import UserContext


# ------------------------------------------------------------------ #
# Agents
# ------------------------------------------------------------------ #
def _tool_names(agent: object) -> set[str]:
    return {getattr(t, "name", "") for t in getattr(agent, "tools", [])}


def test_table_inspector_has_expected_tools() -> None:
    agent = make_table_inspector_agent()
    assert _tool_names(agent) == {"trino_meta", "trino_query", "ossie_read"}
    assert getattr(agent, "role", "") == "Table Inspector"
    assert getattr(agent, "allow_delegation", True) is False


def test_summary_writer_has_ossie_read_only() -> None:
    agent = make_summary_writer_agent()
    # SummaryWriter は LLM 主体で、追加 Trino アクセスは行わない
    assert _tool_names(agent) == {"ossie_read"}
    assert getattr(agent, "role", "") == "Summary Writer"


# ------------------------------------------------------------------ #
# Crew wiring
# ------------------------------------------------------------------ #
def test_build_analytics_summary_crew_has_two_tasks_sequential() -> None:
    crew = build_analytics_summary_crew(memory=False)
    agents = getattr(crew, "agents", [])
    tasks = getattr(crew, "tasks", [])
    assert len(agents) == 2
    assert len(tasks) == 2

    t_inspect, t_summary = tasks
    # 2 番目のタスクは 1 番目を context に持つ (Sequential)
    context = getattr(t_summary, "context", None) or []
    assert t_inspect in context

    # output_json が Pydantic モデル
    assert getattr(t_inspect, "output_json", None) is TableInspectionResult
    assert getattr(t_summary, "output_json", None) is SummaryReport

    # guardrail が inspect_table_task に付いている
    assert getattr(t_inspect, "guardrail", None) is inspect_table_guardrail
    # writer 側は副作用なしなので guardrail 無し
    assert getattr(t_summary, "guardrail", None) is None


def test_build_analytics_summary_crew_process_is_sequential() -> None:
    crew = build_analytics_summary_crew(memory=False)
    proc = getattr(crew, "process", "")
    # Process.sequential もスタブの "sequential" 文字列もどちらも許容
    assert "sequential" in str(proc).lower()


# ------------------------------------------------------------------ #
# guardrail
# ------------------------------------------------------------------ #
def _make_inspection(
    *,
    exists: bool = True,
    has_select_priv: bool = True,
    fq: str = "iceberg.demo.sales_2024",
) -> TableInspectionResult:
    return TableInspectionResult(
        fq_table_name=fq,
        catalog="iceberg",
        schema="demo",
        table=fq.rsplit(".", 1)[-1],
        exists=exists,
        has_select_priv=has_select_priv,
    )


def test_guardrail_passes_when_table_ok() -> None:
    ok, feedback = inspect_table_guardrail(_make_inspection())
    assert ok is True
    assert feedback is None


def test_guardrail_blocks_when_table_not_found() -> None:
    inspection = _make_inspection(exists=False)
    inspection.message = "no such table"
    ok, feedback = inspect_table_guardrail(inspection)
    assert ok is False
    assert feedback is not None
    assert "not found" in feedback


def test_guardrail_blocks_when_no_select_priv() -> None:
    inspection = _make_inspection(has_select_priv=False)
    inspection.message = "Ranger denied"
    ok, feedback = inspect_table_guardrail(inspection)
    assert ok is False
    assert feedback is not None
    assert "permission denied" in feedback.lower()


def test_guardrail_accepts_dict_output() -> None:
    # LLM が dict で返してきた場合も検証を通す
    ok, feedback = inspect_table_guardrail(
        {
            "fq_table_name": "iceberg.demo.t",
            "catalog": "iceberg",
            "schema": "demo",
            "table": "t",
            "exists": True,
            "has_select_priv": True,
        }
    )
    assert ok is True
    assert feedback is None


def test_guardrail_rejects_broken_output() -> None:
    ok, feedback = inspect_table_guardrail("not a dict")
    assert ok is False
    assert feedback is not None


# ------------------------------------------------------------------ #
# kickoff_analytics_summary
# ------------------------------------------------------------------ #
def test_kickoff_rejects_bad_fq_table_name() -> None:
    ctx = UserContext(user_name="alice", groups=[], knox_jwt="tok")
    result = kickoff_analytics_summary(
        user_ctx=ctx,
        fq_table_name="not_fully_qualified",
    )
    assert result["status"] == "error"
    assert result["error_code"] == "BAD_REQUEST"


def test_kickoff_rejects_empty_fq_table_name() -> None:
    ctx = UserContext(user_name="alice", groups=[], knox_jwt="tok")
    result = kickoff_analytics_summary(
        user_ctx=ctx,
        fq_table_name="",
    )
    assert result["status"] == "error"
    assert result["error_code"] == "BAD_REQUEST"


def test_kickoff_returns_error_without_crewai() -> None:
    """crewai 未インストール環境では kickoff 時にスタブ Crew が RuntimeError を
    投げ、それが構造化エラー (HTTP_UNAVAILABLE) として返る。
    """
    ctx = UserContext(user_name="alice", groups=[], knox_jwt="tok")
    result = kickoff_analytics_summary(
        user_ctx=ctx,
        fq_table_name="iceberg.demo.sales_2024",
    )
    # crewai が入っていれば LLM で失敗するかもしれないが、いずれにせよ
    # 「structured エラー」または「payload あり」のどちらか。
    # crewai 無し環境なら error_code=HTTP_UNAVAILABLE。
    assert result["status"] in ("ok", "error")
    if result["status"] == "error":
        assert result["error_code"] in ("HTTP_UNAVAILABLE", "BAD_REQUEST")


# ------------------------------------------------------------------ #
# Model contract
# ------------------------------------------------------------------ #
def test_table_inspection_result_accepts_schema_alias() -> None:
    """LLM は ``schema`` を返し、内部フィールドは ``schema_``。両方通す。"""
    # alias 経由
    a = TableInspectionResult(
        fq_table_name="iceberg.demo.t",
        catalog="iceberg",
        schema="demo",
        table="t",
    )
    assert a.schema_ == "demo"
    # populate_by_name で内部名でも通る
    b = TableInspectionResult(
        fq_table_name="iceberg.demo.t",
        catalog="iceberg",
        schema_="demo",
        table="t",
    )
    assert b.schema_ == "demo"


def test_summary_report_serialization_round_trip() -> None:
    report = SummaryReport(
        fq_table_name="iceberg.demo.sales_2024",
        summary_markdown="## 概要\n- 東京が最大",
        observations=[
            SummaryObservation(
                category="distribution",
                headline="東京が全体の 42%",
                detail="region カラムの上位 1 位",
            )
        ],
        warnings=["email カラムに PII の可能性"],
        sample_queries=[
            {
                "question": "月次売上を見たい",
                "sql": "SELECT date_trunc('month', order_date) m, sum(revenue) FROM iceberg.demo.sales_2024 GROUP BY 1",
            }
        ],
    )
    dumped = report.model_dump()
    parsed = SummaryReport.model_validate(dumped)
    assert parsed.fq_table_name == report.fq_table_name
    assert len(parsed.observations) == 1
    assert parsed.observations[0].category == "distribution"
    assert parsed.warnings == report.warnings
    assert parsed.sample_queries == report.sample_queries


# ================================================================== #
# Dashboard パス
# ================================================================== #
# ------------------------------------------------------------------ #
# Agents
# ------------------------------------------------------------------ #
def test_viz_planner_has_expected_tools() -> None:
    agent = make_viz_planner_agent()
    assert _tool_names(agent) == {"viz_heuristic", "ossie_read"}
    assert getattr(agent, "role", "") == "Viz Planner"
    assert getattr(agent, "allow_delegation", True) is False


def test_dashboard_builder_has_all_cdv_tools() -> None:
    agent = make_dashboard_builder_agent()
    assert _tool_names(agent) == {
        "cdv_startup_check",
        "cdv_dataset_create_or_get",
        "cdv_visual_create",
        "cdv_dashboard_create",
    }
    assert getattr(agent, "role", "") == "Dashboard Builder"
    assert getattr(agent, "allow_delegation", True) is False


# ------------------------------------------------------------------ #
# Crew wiring
# ------------------------------------------------------------------ #
def test_build_dashboard_crew_has_four_tasks_sequential() -> None:
    crew = build_analytics_dashboard_crew(memory=False)
    agents = getattr(crew, "agents", [])
    tasks = getattr(crew, "tasks", [])
    assert len(agents) == 3
    assert len(tasks) == 4

    t_inspect, t_plan, t_ensure, t_build = tasks

    # output_json が正しい Pydantic モデル
    assert getattr(t_inspect, "output_json", None) is TableInspectionResult
    assert getattr(t_plan, "output_json", None) is VizPlan
    assert getattr(t_ensure, "output_json", None) is CDVStartupResult
    assert getattr(t_build, "output_json", None) is BuildDashboardResult


def test_build_dashboard_crew_context_chain() -> None:
    """context 依存が Sequential パイプラインに沿って組まれている。

    - plan_viz は inspect を参照
    - ensure_cdv_running は inspect を参照 (t_plan は不要)
    - build_dashboard は inspect + plan + ensure すべてを参照
    """
    crew = build_analytics_dashboard_crew(memory=False)
    t_inspect, t_plan, t_ensure, t_build = crew.tasks

    plan_ctx = getattr(t_plan, "context", None) or []
    ensure_ctx = getattr(t_ensure, "context", None) or []
    build_ctx = getattr(t_build, "context", None) or []

    assert t_inspect in plan_ctx
    assert t_inspect in ensure_ctx
    assert t_inspect in build_ctx
    assert t_plan in build_ctx
    assert t_ensure in build_ctx


def test_build_dashboard_crew_guardrails_attached() -> None:
    """副作用直前の 2 タスクに guardrail が付いている。"""
    crew = build_analytics_dashboard_crew(memory=False)
    t_inspect, t_plan, t_ensure, t_build = crew.tasks
    assert getattr(t_inspect, "guardrail", None) is inspect_table_guardrail
    assert getattr(t_ensure, "guardrail", None) is ensure_cdv_running_guardrail
    # 副作用なしタスクは guardrail 不要
    assert getattr(t_plan, "guardrail", None) is None
    assert getattr(t_build, "guardrail", None) is None


def test_build_dashboard_task_max_retries_zero() -> None:
    """CDV に書き込む build_dashboard は Crew.ai の再試行で二重生成しないよう
    max_retries=0 固定。他のタスクは 1 が既定 (LLM 依存)。
    """
    crew = build_analytics_dashboard_crew(memory=False)
    t_inspect, t_plan, t_ensure, t_build = crew.tasks
    assert getattr(t_build, "max_retries", None) == 0
    # 副作用なしは 1 (再試行 1 回まで)
    assert getattr(t_inspect, "max_retries", None) == 1
    assert getattr(t_plan, "max_retries", None) == 1
    assert getattr(t_ensure, "max_retries", None) == 1


def test_build_dashboard_crew_process_is_sequential() -> None:
    crew = build_analytics_dashboard_crew(memory=False)
    proc = getattr(crew, "process", "")
    assert "sequential" in str(proc).lower()


# ------------------------------------------------------------------ #
# ensure_cdv_running_guardrail
# ------------------------------------------------------------------ #
def test_ensure_cdv_running_guardrail_passes_when_running() -> None:
    ok, feedback = ensure_cdv_running_guardrail(
        CDVStartupResult(running=True, endpoint="https://cdv.example/arc")
    )
    assert ok is True
    assert feedback is None


def test_ensure_cdv_running_guardrail_blocks_when_not_running() -> None:
    ok, feedback = ensure_cdv_running_guardrail(
        CDVStartupResult(
            running=False,
            endpoint=None,
            message="CDV base URL is not configured",
            error_code="CDV_NOT_RUNNING",
        )
    )
    assert ok is False
    assert feedback is not None
    # ユーザーへの案内が feedback に含まれる (Workbench 誘導)
    assert "CDV" in feedback


def test_ensure_cdv_running_guardrail_accepts_dict_output() -> None:
    ok, feedback = ensure_cdv_running_guardrail(
        {"running": True, "endpoint": "https://cdv.example/arc"}
    )
    assert ok is True
    assert feedback is None


def test_ensure_cdv_running_guardrail_rejects_broken_output() -> None:
    ok, feedback = ensure_cdv_running_guardrail("not a dict")
    assert ok is False
    assert feedback is not None


# ------------------------------------------------------------------ #
# kickoff_analytics_dashboard
# ------------------------------------------------------------------ #
def test_dashboard_kickoff_rejects_bad_fq_table_name() -> None:
    ctx = UserContext(user_name="alice", groups=[], knox_jwt="tok")
    result = kickoff_analytics_dashboard(
        user_ctx=ctx,
        fq_table_name="not_fully_qualified",
    )
    assert result["status"] == "error"
    assert result["error_code"] == "BAD_REQUEST"


def test_dashboard_kickoff_rejects_empty_fq_table_name() -> None:
    ctx = UserContext(user_name="alice", groups=[], knox_jwt="tok")
    result = kickoff_analytics_dashboard(user_ctx=ctx, fq_table_name="")
    assert result["status"] == "error"
    assert result["error_code"] == "BAD_REQUEST"


def test_dashboard_kickoff_returns_error_without_crewai() -> None:
    """crewai 未インストール環境では kickoff 時にスタブ Crew が RuntimeError を
    投げ、それが構造化エラー (HTTP_UNAVAILABLE) として返る。
    """
    ctx = UserContext(user_name="alice", groups=[], knox_jwt="tok")
    result = kickoff_analytics_dashboard(
        user_ctx=ctx,
        fq_table_name="iceberg.demo.sales_2024",
    )
    assert result["status"] in ("ok", "error")
    if result["status"] == "error":
        assert result["error_code"] in ("HTTP_UNAVAILABLE", "BAD_REQUEST")


# ------------------------------------------------------------------ #
# Model contract
# ------------------------------------------------------------------ #
def test_build_dashboard_result_round_trip() -> None:
    r = BuildDashboardResult(
        fq_table_name="iceberg.demo.sales_2024",
        title="売上ダッシュボード",
        dashboard_id="dash_1",
        dashboard_url="https://cdv.example/arc/apps/dashboard/dash_1",
        dataset_id="ds_1",
        dataset_reused=True,
        visual_ids=["v1", "v2", "v3"],
        notes=None,
    )
    parsed = BuildDashboardResult.model_validate(r.model_dump())
    assert parsed.dashboard_id == "dash_1"
    assert parsed.visual_ids == ["v1", "v2", "v3"]
    assert parsed.dataset_reused is True


def test_viz_plan_requires_at_least_one_visual() -> None:
    """VizPlan.visuals は min_length=1: 空配列は ValidationError。"""
    import pytest as _pytest
    from pydantic import ValidationError

    with _pytest.raises(ValidationError):
        VizPlan(
            fq_table_name="iceberg.demo.sales_2024",
            title="empty",
            visuals=[],
        )


# ------------------------------------------------------------------ #
# VizHeuristicTool: 決定論的なルールマトリクス
# ------------------------------------------------------------------ #
def _run_viz(columns: list[dict]) -> dict:
    """VizHeuristicTool を直接呼び出して payload を返す。"""
    tool = VizHeuristicTool()
    resp = tool.run(
        user_ctx=None,
        fq_table_name="iceberg.demo.t",
        columns=columns,
    )
    assert resp["status"] == "ok"
    return resp["payload"]


def test_viz_heuristic_time_x_measure_yields_line() -> None:
    """time (date/timestamp) と measure が両方揃うと line が最優先で提案される。"""
    payload = _run_viz(
        [
            {"name": "order_date", "trino_type": "date", "role": "time"},
            {"name": "revenue", "trino_type": "decimal(18,2)", "role": "measure"},
        ]
    )
    visuals = payload["visuals"]
    assert len(visuals) >= 1
    assert visuals[0]["viz_type"] == "line"
    assert visuals[0]["x"] == "order_date"
    assert visuals[0]["y"] == "revenue"


def test_viz_heuristic_dim_x_measure_yields_bar() -> None:
    """低カーディナリティ dimension と measure なら bar が入る。"""
    payload = _run_viz(
        [
            {
                "name": "region",
                "trino_type": "varchar",
                "role": "dimension",
                "distinct_count": 5,
            },
            {"name": "revenue", "trino_type": "decimal(18,2)", "role": "measure"},
        ]
    )
    types = {v["viz_type"] for v in payload["visuals"]}
    assert "bar" in types
    bar = next(v for v in payload["visuals"] if v["viz_type"] == "bar")
    assert bar["x"] == "region"
    assert bar["y"] == "revenue"


def test_viz_heuristic_measure_only_yields_kpi() -> None:
    """measure だけなら KPI (代表値) を提案。"""
    payload = _run_viz(
        [{"name": "revenue", "trino_type": "bigint", "role": "measure"}]
    )
    types = {v["viz_type"] for v in payload["visuals"]}
    assert "kpi" in types


def test_viz_heuristic_falls_back_to_table() -> None:
    """time も measure も無く、低カーディナリティ dim だけでも measure が
    無いので bar は作れない。very-low-card (<=8) なら pie、それすら無ければ
    table にフォールバックする。
    """
    # 高カーディナリティ dimension のみ → pie も bar も出せない → table
    payload = _run_viz(
        [
            {
                "name": "customer_id",
                "trino_type": "varchar",
                "role": "dimension",
                "distinct_count": 1000,
            }
        ]
    )
    assert len(payload["visuals"]) == 1
    assert payload["visuals"][0]["viz_type"] == "table"


def test_viz_heuristic_very_low_card_dim_alone_yields_pie() -> None:
    """distinct_count <= 8 の dimension 単独なら pie。"""
    payload = _run_viz(
        [
            {
                "name": "channel",
                "trino_type": "varchar",
                "role": "dimension",
                "distinct_count": 3,
            }
        ]
    )
    types = {v["viz_type"] for v in payload["visuals"]}
    assert "pie" in types


def test_viz_heuristic_respects_max_visuals() -> None:
    """max_visuals で候補数を絞れる。"""
    tool = VizHeuristicTool()
    resp = tool.run(
        user_ctx=None,
        fq_table_name="iceberg.demo.t",
        columns=[
            {"name": "order_date", "trino_type": "date", "role": "time"},
            {
                "name": "region",
                "trino_type": "varchar",
                "role": "dimension",
                "distinct_count": 5,
            },
            {
                "name": "channel",
                "trino_type": "varchar",
                "role": "dimension",
                "distinct_count": 4,
            },
            {"name": "revenue", "trino_type": "bigint", "role": "measure"},
        ],
        max_visuals=2,
    )
    assert resp["status"] == "ok"
    assert len(resp["payload"]["visuals"]) == 2

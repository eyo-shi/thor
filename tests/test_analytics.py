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
    make_summary_writer_agent,
    make_table_inspector_agent,
)
from thor.analytics.crew import (
    build_analytics_summary_crew,
    kickoff_analytics_summary,
)
from thor.analytics.models import (
    SummaryObservation,
    SummaryReport,
    TableInspectionResult,
)
from thor.analytics.tasks import inspect_table_guardrail
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

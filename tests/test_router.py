"""Router Crew の配線 + heuristic テスト。

LLM を呼ばずに以下だけを確認する:

  * 2 Agent が正しい Tool set を持って生成される
  * 2 Task が Sequential で組まれ、output_json が Pydantic モデル
  * heuristic_classify が 5 分類を意図通りに割り振る
  * build_dispatch_plan が clarify / skip_child を正しく設定する
  * kickoff_router(mode='heuristic') は LLM 不要で動く
  * entity_memory が Knox JWT / STS を LLM に露出させないよう安全にフィルタされる
"""
from __future__ import annotations

from thor.router import (
    DispatchPlan,
    IntentClassification,
    RouterResult,
    build_dispatch_plan,
    build_router_crew,
    heuristic_classify,
    kickoff_router,
)
from thor.router.agents import (
    make_dispatcher_agent,
    make_intent_classifier_agent,
)
from thor.router.crew import _summarize_entity_memory
from thor.router.tools import (
    AnalyticsKickoffTool,
    EntityMemoryReadTool,
    IngestionKickoffTool,
)
from thor.transport.user_context import UserContext


# ------------------------------------------------------------------ #
# Agents
# ------------------------------------------------------------------ #
def _tool_names(agent: object) -> set[str]:
    return {getattr(t, "name", "") for t in getattr(agent, "tools", [])}


def test_intent_classifier_has_entity_memory_tool() -> None:
    agent = make_intent_classifier_agent()
    assert _tool_names(agent) == {"entity_memory_read"}
    assert getattr(agent, "role", "") == "Intent Classifier"


def test_dispatcher_has_kickoff_tools() -> None:
    agent = make_dispatcher_agent()
    assert _tool_names(agent) == {"ingestion_kickoff", "analytics_kickoff"}
    assert getattr(agent, "role", "") == "Dispatcher"


# ------------------------------------------------------------------ #
# Crew wiring
# ------------------------------------------------------------------ #
def test_build_router_crew_has_two_tasks_sequential() -> None:
    crew = build_router_crew(memory=False)
    tasks = getattr(crew, "tasks", [])
    agents = getattr(crew, "agents", [])
    assert len(tasks) == 2
    assert len(agents) == 2
    # 2 番目のタスクは 1 番目を context に持つ (Sequential)
    t_classify, t_dispatch = tasks
    context = getattr(t_dispatch, "context", None) or []
    assert t_classify in context
    # output_json が Pydantic モデル
    assert getattr(t_classify, "output_json", None) is IntentClassification
    assert getattr(t_dispatch, "output_json", None) is DispatchPlan


# ------------------------------------------------------------------ #
# heuristic_classify
# ------------------------------------------------------------------ #
def test_heuristic_ingest_with_s3_uri() -> None:
    c = heuristic_classify(
        "s3://demo-bucket/2024/sales.xlsx を取り込んでテーブルにして"
    )
    assert c.intent == "INGEST"
    assert c.extracted_args["bucket"] == "demo-bucket"
    assert c.extracted_args["key"] == "2024/sales.xlsx"
    assert c.extracted_args["target_schema"] == "demo"
    assert c.needs_clarification is False


def test_heuristic_ingest_keyword_only_asks_for_path() -> None:
    c = heuristic_classify("何かを取り込みたい")
    assert c.intent == "INGEST"
    assert c.needs_clarification is True
    assert c.clarification_prompt and "s3://" in c.clarification_prompt


def test_heuristic_summary_uses_entity_memory() -> None:
    c = heuristic_classify(
        "そのテーブルをサマリーして",
        entity_memory={"last_table": "iceberg.demo.sales_2024"},
    )
    assert c.intent == "ANALYZE_SUMMARY"
    assert c.needs_clarification is False
    assert c.extracted_args["fq_table_name"] == "iceberg.demo.sales_2024"


def test_heuristic_summary_without_entity_asks_for_table() -> None:
    c = heuristic_classify("サマリーを見たい", entity_memory={})
    assert c.intent == "ANALYZE_SUMMARY"
    assert c.needs_clarification is True
    assert c.clarification_prompt is not None


def test_heuristic_dashboard_uses_entity_memory() -> None:
    c = heuristic_classify(
        "そのテーブルからダッシュボードを作って",
        entity_memory={"last_table": "iceberg.demo.sales_2024"},
    )
    assert c.intent == "ANALYZE_DASHBOARD"
    assert c.extracted_args["fq_table_name"] == "iceberg.demo.sales_2024"


def test_heuristic_chitchat() -> None:
    c = heuristic_classify("こんにちは、何ができるの?")
    assert c.intent == "CHITCHAT"
    assert c.needs_clarification is False


def test_heuristic_unknown() -> None:
    c = heuristic_classify("今日の天気は?")
    assert c.intent == "UNKNOWN"
    assert c.needs_clarification is True


# ------------------------------------------------------------------ #
# build_dispatch_plan
# ------------------------------------------------------------------ #
def test_dispatch_plan_ingest() -> None:
    classification = IntentClassification(
        intent="INGEST",
        confidence=0.9,
        extracted_args={"bucket": "b", "key": "k", "target_schema": "demo"},
    )
    plan = build_dispatch_plan(classification)
    assert plan.child_crew == "ingestion"
    assert plan.skip_child is False
    assert plan.inputs["bucket"] == "b"
    assert plan.inputs["key"] == "k"


def test_dispatch_plan_ingest_missing_args_asks_clarify() -> None:
    classification = IntentClassification(
        intent="INGEST", confidence=0.5, extracted_args={}
    )
    plan = build_dispatch_plan(classification)
    assert plan.child_crew == "none"
    assert plan.skip_child is True
    assert "s3://" in plan.response_markdown


def test_dispatch_plan_summary_no_table_asks_clarify() -> None:
    classification = IntentClassification(
        intent="ANALYZE_SUMMARY", confidence=0.6, extracted_args={}
    )
    plan = build_dispatch_plan(classification)
    assert plan.child_crew == "none"
    assert plan.skip_child is True


def test_dispatch_plan_dashboard_with_table() -> None:
    classification = IntentClassification(
        intent="ANALYZE_DASHBOARD",
        confidence=0.9,
        extracted_args={"fq_table_name": "iceberg.demo.t"},
    )
    plan = build_dispatch_plan(classification)
    assert plan.child_crew == "analytics_dashboard"
    assert plan.skip_child is False
    assert plan.inputs["fq_table_name"] == "iceberg.demo.t"


def test_dispatch_plan_chitchat_skips_child() -> None:
    classification = IntentClassification(
        intent="CHITCHAT", confidence=0.9, extracted_args={}
    )
    plan = build_dispatch_plan(classification)
    assert plan.child_crew == "none"
    assert plan.skip_child is True
    assert plan.response_markdown  # 何か返答が入っている


def test_dispatch_plan_needs_clarification() -> None:
    classification = IntentClassification(
        intent="UNKNOWN",
        confidence=0.2,
        extracted_args={},
        needs_clarification=True,
        clarification_prompt="もう少し詳しく教えて",
    )
    plan = build_dispatch_plan(classification)
    assert plan.skip_child is True
    assert "もう少し" in plan.response_markdown


# ------------------------------------------------------------------ #
# kickoff_router (heuristic mode)
# ------------------------------------------------------------------ #
def test_kickoff_router_heuristic_ingest() -> None:
    ctx = UserContext(user_name="alice", groups=[], knox_jwt=None)
    result = kickoff_router(
        user_ctx=ctx,
        prompt="s3://b/k.csv を取り込んで",
        session_id="s1",
        entity_memory={},
        mode="heuristic",
    )
    assert isinstance(result, RouterResult)
    assert result.classification.intent == "INGEST"
    assert result.plan.child_crew == "ingestion"
    assert result.plan.inputs["bucket"] == "b"


def test_kickoff_router_heuristic_chitchat_populates_response_markdown() -> None:
    ctx = UserContext(user_name="alice", groups=[], knox_jwt=None)
    result = kickoff_router(
        user_ctx=ctx,
        prompt="こんにちは",
        session_id="s1",
        entity_memory={},
        mode="heuristic",
    )
    assert result.classification.intent == "CHITCHAT"
    assert result.plan.skip_child is True
    assert result.response_markdown == result.plan.response_markdown
    assert result.response_markdown  # 空でない


def test_kickoff_router_auto_falls_back_to_heuristic_without_llm() -> None:
    ctx = UserContext(user_name="alice", groups=[], knox_jwt=None)
    result = kickoff_router(
        user_ctx=ctx,
        prompt="s3://b/k.csv を取り込んで",
        session_id="s1",
        entity_memory={},
        llm_light=None,
        mode="auto",
    )
    # llm_light=None なら auto でも heuristic で完走する
    assert result.classification.intent == "INGEST"


# ------------------------------------------------------------------ #
# entity_memory safety
# ------------------------------------------------------------------ #
def test_summarize_entity_memory_filters_unsafe_keys() -> None:
    """LLM プロンプトには allowlist の key しか含めない。

    Knox JWT / STS が entity_memory にうっかり入っても、LLM 経路に流れる
    要約文字列には出てこないことを担保する。
    """
    memory = {
        "last_table": "iceberg.demo.sales_2024",
        "last_s3_path": "s3://bucket/key",
        "knox_jwt": "eyJ.SECRET.SIGNATURE",  # うっかり入った秘密
        "aws_access_key_id": "AKIA...",  # 同上
        "arbitrary_debug_dump": {"password": "hunter2"},
    }
    summary = _summarize_entity_memory(memory)
    assert "iceberg.demo.sales_2024" in summary
    assert "s3://bucket/key" in summary
    assert "SECRET" not in summary
    assert "AKIA" not in summary
    assert "hunter2" not in summary


def test_summarize_entity_memory_empty() -> None:
    assert "no entity memory" in _summarize_entity_memory({})


# ------------------------------------------------------------------ #
# EntityMemoryReadTool
# ------------------------------------------------------------------ #
def test_entity_memory_read_tool_returns_stored_snapshot() -> None:
    from thor.api.state import get_store

    tool = EntityMemoryReadTool()
    store = get_store()
    sess = store.get_or_create_session("test_sess_router", "alice")
    store.update_entity_memory(
        sess.session_id, {"last_table": "iceberg.demo.sales_2024"}
    )
    # BaseThorTool._run 経由で呼ぶ (requires_auth=False なので user_ctx 不要)
    result = tool._run(session_id=sess.session_id)
    assert result["status"] == "ok"
    assert result["found"] is True
    assert result["entity_memory"]["last_table"] == "iceberg.demo.sales_2024"


def test_entity_memory_read_tool_unknown_session_ok() -> None:
    tool = EntityMemoryReadTool()
    result = tool._run(session_id="does_not_exist_xxx")
    assert result["status"] == "ok"
    assert result["found"] is False
    assert result["entity_memory"] == {}


# ------------------------------------------------------------------ #
# AnalyticsKickoffTool (currently stub)
# ------------------------------------------------------------------ #
def test_analytics_kickoff_tool_returns_not_implemented() -> None:
    tool = AnalyticsKickoffTool()
    # BaseThorTool._run は requires_auth=True (デフォルト) を強制する
    # そのため UserContext を ContextVar にセットして走らせる
    from thor.transport.user_context import reset_user_context, set_user_context

    ctx = UserContext(user_name="alice", groups=[], knox_jwt="tok")
    token = set_user_context(ctx)
    try:
        result = tool._run(mode="summary", fq_table_name="iceberg.demo.t")
    finally:
        reset_user_context(token)
    assert result["status"] == "error"
    assert result["error_code"] == "NOT_IMPLEMENTED"


def test_ingestion_kickoff_tool_requires_user_ctx() -> None:
    """BaseThorTool の requires_auth=True で AUTH_MISSING を返す。"""
    tool = IngestionKickoffTool()
    # user_ctx を立てないまま呼ぶ
    result = tool._run(bucket="b", key="k", target_schema="demo")
    assert result["status"] == "error"
    assert result["error_code"] == "AUTH_MISSING"

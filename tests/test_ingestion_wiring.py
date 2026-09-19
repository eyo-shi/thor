"""Ingestion Crew の配線テスト。

LLM を呼ばずに以下だけを確認する:

  * 5 Agent が正しい Tool set を持って生成される
  * 8 Task が正しい順序 + context 依存で組み立てられる
  * guardrail 関数が期待通り (blocked / passed) の判定を返す
  * kickoff_ingestion は crewai 未インストール環境でも構造化エラーで返る

crewai が入っていない環境では :class:`Agent` / :class:`Task` / :class:`Crew`
はスタブになる。スタブでも属性チェックは全て通るように設計してある。
"""
from __future__ import annotations

from thor.ingestion.agents import (
    make_format_sniffer_agent,
    make_ossie_drafter_agent,
    make_s3_scout_agent,
    make_schema_drafter_agent,
    make_table_creator_agent,
)
from thor.ingestion.crew import build_ingestion_crew, kickoff_ingestion
from thor.ingestion.models import ConflictAndPermissionsResult
from thor.ingestion.tasks import conflict_permissions_guardrail
from thor.transport.user_context import UserContext


# ------------------------------------------------------------------ #
# Agents
# ------------------------------------------------------------------ #
def _tool_names(agent: object) -> set[str]:
    return {getattr(t, "name", "") for t in getattr(agent, "tools", [])}


def test_s3_scout_has_s3_tools() -> None:
    agent = make_s3_scout_agent()
    assert _tool_names(agent) == {"s3_list", "s3_head", "s3_get_range"}


def test_format_sniffer_has_format_tools() -> None:
    agent = make_format_sniffer_agent()
    assert _tool_names(agent) == {
        "magic_byte",
        "csv_sniff",
        "excel_header_detect",
        "parquet_meta",
        "dataframe_preview",
    }


def test_schema_drafter_has_schema_tools() -> None:
    agent = make_schema_drafter_agent()
    assert _tool_names(agent) == {
        "type_infer",
        "name_proposer",
        "similar_table_search",
    }


def test_table_creator_has_iceberg_tools() -> None:
    agent = make_table_creator_agent()
    assert _tool_names(agent) == {
        "table_exists",
        "trino_meta",
        "iceberg_create_table",
    }


def test_ossie_drafter_has_ossie_tool() -> None:
    agent = make_ossie_drafter_agent()
    assert _tool_names(agent) == {"ossie_write"}


# ------------------------------------------------------------------ #
# Crew build
# ------------------------------------------------------------------ #
def test_build_ingestion_crew_shape() -> None:
    crew = build_ingestion_crew(memory=False)
    agents = list(getattr(crew, "agents", []))
    tasks = list(getattr(crew, "tasks", []))
    # 5 agents / 8 tasks
    assert len(agents) == 5
    assert len(tasks) == 8
    # sequential
    assert str(getattr(crew, "process", "")).endswith("sequential")


def test_task_context_chain() -> None:
    """後段の Task が前段を context で受け取っていることを確認。"""
    crew = build_ingestion_crew(memory=False)
    tasks = list(getattr(crew, "tasks", []))
    (
        t_locate,
        t_sniff,
        t_extract,
        t_propose,
        t_check,
        t_create,
        t_ossie,
        t_wrap,
    ) = tasks

    # locate は context を持たない (最初)
    assert not getattr(t_locate, "context", []) or getattr(t_locate, "context") is None
    # sniff は locate を受ける
    assert t_locate in getattr(t_sniff, "context", [])
    # extract は sniff を受ける
    assert t_sniff in getattr(t_extract, "context", [])
    # propose は locate / sniff / extract を受ける
    prop_ctx = getattr(t_propose, "context", [])
    assert t_locate in prop_ctx and t_sniff in prop_ctx and t_extract in prop_ctx
    # check は propose を受ける + guardrail が付いている
    assert t_propose in getattr(t_check, "context", [])
    assert callable(getattr(t_check, "guardrail", None))
    # create は propose + check を受ける
    create_ctx = getattr(t_create, "context", [])
    assert t_propose in create_ctx and t_check in create_ctx
    # ossie は create + propose + extract + locate を受ける
    ossie_ctx = getattr(t_ossie, "context", [])
    assert t_create in ossie_ctx and t_propose in ossie_ctx
    # wrap は create + ossie を受ける
    wrap_ctx = getattr(t_wrap, "context", [])
    assert t_create in wrap_ctx and t_ossie in wrap_ctx


def test_side_effect_tasks_have_zero_retries() -> None:
    """CREATE / Git commit のタスクは max_retries=0 を守っていること。"""
    crew = build_ingestion_crew(memory=False)
    tasks = list(getattr(crew, "tasks", []))
    _, _, _, _, t_check, t_create, t_ossie, _ = tasks
    assert getattr(t_check, "max_retries", None) == 0
    assert getattr(t_create, "max_retries", None) == 0
    assert getattr(t_ossie, "max_retries", None) == 0


# ------------------------------------------------------------------ #
# guardrail
# ------------------------------------------------------------------ #
def test_guardrail_blocks_conflict() -> None:
    out = ConflictAndPermissionsResult(
        has_conflict=True,
        has_create_priv=True,
        resolved_table="sales_2024",
        error_code="SCHEMA_NAME_CONFLICT",
        message="already exists",
    )
    ok_, msg = conflict_permissions_guardrail(out)
    assert ok_ is False
    assert msg is not None
    assert "conflict" in msg.lower()


def test_guardrail_blocks_missing_privilege() -> None:
    out = ConflictAndPermissionsResult(
        has_conflict=False,
        has_create_priv=False,
        resolved_table="sales_2024",
        error_code="PERM_CREATE_DENIED",
        message="not a data owner",
    )
    ok_, msg = conflict_permissions_guardrail(out)
    assert ok_ is False
    assert msg is not None
    assert "permission" in msg.lower() or "denied" in msg.lower()


def test_guardrail_passes_clean_output() -> None:
    out = ConflictAndPermissionsResult(
        has_conflict=False,
        has_create_priv=True,
        resolved_table="sales_2024",
    )
    ok_, msg = conflict_permissions_guardrail(out)
    assert ok_ is True
    assert msg is None


def test_guardrail_accepts_dict_input() -> None:
    ok_, _ = conflict_permissions_guardrail(
        {
            "has_conflict": False,
            "has_create_priv": True,
            "resolved_table": "customers",
        }
    )
    assert ok_ is True


def test_guardrail_rejects_bad_output() -> None:
    ok_, msg = conflict_permissions_guardrail("not a dict")
    assert ok_ is False
    assert msg is not None


# ------------------------------------------------------------------ #
# kickoff_ingestion - crewai 未インストール環境での安全ネット
# ------------------------------------------------------------------ #
def test_kickoff_ingestion_without_crewai_returns_error() -> None:
    """crewai 未インストールなら Crew.kickoff が例外を投げ、
    kickoff_ingestion は構造化エラーで包んで返す。"""
    ctx = UserContext(user_name="alice", session_id="s1")
    res = kickoff_ingestion(
        user_ctx=ctx,
        bucket="demo",
        key="sales.xlsx",
        target_schema="demo",
    )
    # crewai が入っていれば実際に LLM を呼ぼうとして別のエラーになるが、
    # 少なくとも Python 例外は上に投げず dict で返ることを確認
    assert isinstance(res, dict)
    assert "status" in res

"""Agent Studio manifest 生成のテスト。

LLM も crewai 本体も呼ばない。以下だけを確認する:

  * ``thor.tools.__all__`` の全 Tool が tools.yaml に現れる
  * 各 Tool の args_schema が JSON Schema 形式になっている
  * Ingestion Crew の 5 Agent / 8 Task が agents.yaml / crews.yaml に載る
  * すべての Task ``context`` 参照が定義済み Task 名にリゾルブする
  * ``--check`` モードが drift を検出する (tmp_path で改ざん → exit 1)
"""
from __future__ import annotations

from pathlib import Path

import pytest

import thor.tools as tools_pkg
from thor.manifest import (
    build_agents_manifest,
    build_crews_manifest,
    build_tools_manifest,
    diff_manifest,
    dump_manifest_yaml,
    write_manifests,
)
from thor.manifest.cli import main as cli_main
from thor.manifest.writer import AGENTS_FILE, CREWS_FILE, TOOLS_FILE


# ------------------------------------------------------------------ #
# tools.yaml
# ------------------------------------------------------------------ #
def test_all_public_tools_appear_in_tools_manifest() -> None:
    m = build_tools_manifest()
    names = {t["name"] for t in m["tools"]}
    # Tool クラス毎に BaseThorTool を継承しつつ name を持っているはず
    expected: set[str] = set()
    for cls_name in tools_pkg.__all__:
        cls = getattr(tools_pkg, cls_name)
        expected.add(cls().name)
    assert names == expected
    # 予約されたトップレベルキー
    assert m["version"] == 1
    assert m["generated_from"] == "thor.tools"


def test_tools_have_json_schema_args() -> None:
    m = build_tools_manifest()
    for t in m["tools"]:
        schema = t["args_schema"]
        assert isinstance(schema, dict), t["name"]
        # Pydantic v2 は type='object' と properties を必ず出す
        assert schema.get("type") == "object", t["name"]
        assert "properties" in schema, t["name"]


def test_tools_class_path_is_importable_looking() -> None:
    m = build_tools_manifest()
    for t in m["tools"]:
        assert t["class_path"].startswith("thor.tools."), t["name"]


# ------------------------------------------------------------------ #
# agents.yaml
# ------------------------------------------------------------------ #
def test_ingestion_agents_present() -> None:
    m = build_agents_manifest()
    names = [a["name"] for a in m["agents"] if a["crew"] == "ingestion"]
    # 5 Ingestion Agent
    assert set(names) == {
        "s3_scout",
        "format_sniffer",
        "schema_drafter",
        "iceberg_table_creator",
        "ossie_drafter",
    }


def test_ingestion_agent_tool_bindings() -> None:
    m = build_agents_manifest()
    by_name = {a["name"]: a for a in m["agents"]}
    assert set(by_name["s3_scout"]["tools"]) == {"s3_list", "s3_head", "s3_get_range"}
    assert set(by_name["format_sniffer"]["tools"]) == {
        "magic_byte",
        "csv_sniff",
        "excel_header_detect",
        "parquet_meta",
        "dataframe_preview",
    }
    assert set(by_name["schema_drafter"]["tools"]) == {
        "type_infer",
        "name_proposer",
        "similar_table_search",
    }
    assert set(by_name["iceberg_table_creator"]["tools"]) == {
        "table_exists",
        "trino_meta",
        "iceberg_create_table",
    }
    assert by_name["ossie_drafter"]["tools"] == ["ossie_write"]


# ------------------------------------------------------------------ #
# crews.yaml
# ------------------------------------------------------------------ #
def test_ingestion_crew_has_eight_tasks() -> None:
    m = build_crews_manifest()
    ingestion = next(c for c in m["crews"] if c["name"] == "ingestion")
    assert ingestion["implemented"] is True
    assert ingestion["process"] == "sequential"
    task_names = [t["name"] for t in ingestion["tasks"]]
    # 順序も含めて完全一致 (Sequential の意味)
    assert task_names == [
        "locate_s3_object",
        "sniff_format",
        "extract_data_frame",
        "propose_schema_and_name",
        "conflict_and_permissions",
        "create_iceberg_table",
        "draft_ossie",
        "wrap_up",
    ]


def test_task_context_refs_resolve() -> None:
    m = build_crews_manifest()
    ingestion = next(c for c in m["crews"] if c["name"] == "ingestion")
    all_names = {t["name"] for t in ingestion["tasks"]}
    for t in ingestion["tasks"]:
        for c in t["context"]:
            assert c in all_names, f"dangling ctx {c!r} in {t['name']!r}"


def test_side_effect_tasks_max_retries_zero() -> None:
    m = build_crews_manifest()
    ingestion = next(c for c in m["crews"] if c["name"] == "ingestion")
    by_name = {t["name"]: t for t in ingestion["tasks"]}
    for n in ("conflict_and_permissions", "create_iceberg_table", "draft_ossie"):
        assert by_name[n]["max_retries"] == 0, n


def test_check_task_has_guardrail_annotation() -> None:
    m = build_crews_manifest()
    ingestion = next(c for c in m["crews"] if c["name"] == "ingestion")
    by_name = {t["name"]: t for t in ingestion["tasks"]}
    gr = by_name["conflict_and_permissions"]["guardrail"]
    assert isinstance(gr, str) and "conflict_permissions_guardrail" in gr


def test_task_inputs_scanned_from_template_vars() -> None:
    m = build_crews_manifest()
    ingestion = next(c for c in m["crews"] if c["name"] == "ingestion")
    by_name = {t["name"]: t for t in ingestion["tasks"]}
    # locate_s3_object の description は {bucket} と {key} を使っている
    assert set(by_name["locate_s3_object"]["inputs"]) >= {"bucket", "key"}
    # propose_schema_and_name は {target_schema} を使っている
    assert "target_schema" in by_name["propose_schema_and_name"]["inputs"]


def test_stub_crews_are_placeholders() -> None:
    m = build_crews_manifest()
    for name in ("router", "analytics"):
        entry = next(c for c in m["crews"] if c["name"] == name)
        assert entry["implemented"] is False
        assert entry["agents"] == []
        assert entry["tasks"] == []
        assert entry.get("todo")


# ------------------------------------------------------------------ #
# writer / CLI
# ------------------------------------------------------------------ #
def test_write_manifests_produces_three_files(tmp_path: Path) -> None:
    tools = build_tools_manifest()
    agents = build_agents_manifest()
    crews = build_crews_manifest()
    written = write_manifests(tmp_path, tools, agents, crews)
    assert [p.name for p in written] == [TOOLS_FILE, AGENTS_FILE, CREWS_FILE]
    for p in written:
        assert p.exists()
        text = p.read_text(encoding="utf-8")
        assert text.startswith("# AUTO-GENERATED")


def test_yaml_dump_is_deterministic() -> None:
    m = build_tools_manifest()
    a = dump_manifest_yaml(m)
    b = dump_manifest_yaml(m)
    assert a == b


def test_diff_manifest_detects_no_drift_after_write(tmp_path: Path) -> None:
    tools = build_tools_manifest()
    path = tmp_path / TOOLS_FILE
    path.write_text(dump_manifest_yaml(tools), encoding="utf-8")
    assert diff_manifest(path, tools) == ""


def test_diff_manifest_detects_drift(tmp_path: Path) -> None:
    tools = build_tools_manifest()
    path = tmp_path / TOOLS_FILE
    path.write_text("version: 1\ntools: []\n", encoding="utf-8")
    diff = diff_manifest(path, tools)
    assert diff  # 非空
    assert "tools" in diff


def test_cli_check_mode_flags_drift(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    # 先に正しいマニフェストを書き出す
    rc = cli_main(["--out", str(tmp_path)])
    assert rc == 0
    # check モードは 0
    rc = cli_main(["--check", "--out", str(tmp_path)])
    assert rc == 0
    # tools.yaml を壊す
    (tmp_path / TOOLS_FILE).write_text("version: 1\ntools: []\n", encoding="utf-8")
    rc = cli_main(["--check", "--out", str(tmp_path)])
    assert rc == 1
    captured = capsys.readouterr()
    assert "drift" in captured.err.lower()


def test_cli_stdout_mode(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    rc = cli_main(["--stdout", "--out", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    # 3 マニフェストのセパレータが全部出ている
    assert TOOLS_FILE in out
    assert AGENTS_FILE in out
    assert CREWS_FILE in out
    # ファイルは書かれていない
    assert not (tmp_path / TOOLS_FILE).exists()

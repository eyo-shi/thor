"""Tool / Agent / Crew を pure dict にする introspection 関数群。

このモジュールは **LLM を呼ばない**。Python の Tool クラスと Crew インスタンスを
リフレクションで読むだけ。crewai が未インストールでも、:mod:`thor.ingestion.crew`
のスタブ経由で Crew 構造は組み立たるので、生成は問題なく動く。

規約:
  * Tool 名: クラス属性 ``name`` (crewai の Tool 契約と一致)
  * Agent 名: ``role.lower().replace(" ", "_")`` (例: "S3 Scout" -> "s3_scout")
  * Task 名: ``output_json.__name__`` を snake_case 化
             (例: "LocateS3ObjectResult" -> "locate_s3_object")
  * Task inputs: ``description`` を ``{var}`` パターンでスキャン
  * Task context: 前段の Task インスタンスから Task 名を逆引き
  * Guardrail: 関数の ``__qualname__`` を文字列で残す (Studio は関数を実行できないが、
    "この Task にはガードレールがある" というアノテーションとして意味がある)
"""
from __future__ import annotations

import re
from typing import Any, Optional

# ------------------------------------------------------------------ #
# 内部ヘルパ
# ------------------------------------------------------------------ #
_INPUT_VAR_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_CAMEL_TO_SNAKE_1 = re.compile(r"(.)([A-Z][a-z]+)")
_CAMEL_TO_SNAKE_2 = re.compile(r"([a-z0-9])([A-Z])")


def _camel_to_snake(name: str) -> str:
    """CamelCase / PascalCase を snake_case へ変換する。"""
    s1 = _CAMEL_TO_SNAKE_1.sub(r"\1_\2", name)
    return _CAMEL_TO_SNAKE_2.sub(r"\1_\2", s1).lower()


def _strip_result_suffix(name: str) -> str:
    """"LocateS3ObjectResult" -> "LocateS3Object" 相当の接尾語除去。"""
    for suffix in ("Result", "Output", "Report"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _task_name_from_output(task: Any) -> Optional[str]:
    """Task の ``output_json`` (Pydantic モデル) からタスク名を導出する。

    IngestionReport のように接尾語が Report のものは wrap_up と読み替える。
    """
    model = getattr(task, "output_json", None)
    if model is None:
        return None
    cls_name = getattr(model, "__name__", None)
    if not cls_name:
        return None
    # IngestionReport は最終集約タスクなので特別扱い
    if cls_name == "IngestionReport":
        return "wrap_up"
    return _camel_to_snake(_strip_result_suffix(cls_name))


def _agent_name_from_role(agent: Any) -> str:
    """Agent の role からスラグ化した名前を作る。"""
    role = getattr(agent, "role", "") or ""
    slug = role.strip().lower().replace(" ", "_")
    return slug or "unnamed_agent"


def _tool_names_from_agent(agent: Any) -> list[str]:
    """Agent が持つ tools リストから Tool 名 (str) を取り出す。"""
    tools = getattr(agent, "tools", None) or []
    names: list[str] = []
    for t in tools:
        n = getattr(t, "name", None)
        if isinstance(n, str) and n:
            names.append(n)
    return names


def _extract_input_vars(description: str) -> list[str]:
    """Task description から ``{var}`` テンプレート変数を抽出する。"""
    if not isinstance(description, str):
        return []
    seen: dict[str, None] = {}
    for m in _INPUT_VAR_RE.finditer(description):
        seen.setdefault(m.group(1), None)
    return list(seen.keys())


def _resolve_context_names(task: Any, task_id_to_name: dict[int, str]) -> list[str]:
    """Task の ``context`` (Task インスタンスのリスト) を Task 名の str リストにする。"""
    ctx = getattr(task, "context", None) or []
    out: list[str] = []
    for c in ctx:
        name = task_id_to_name.get(id(c))
        if name:
            out.append(name)
    return out


# ------------------------------------------------------------------ #
# tools.yaml
# ------------------------------------------------------------------ #
def _tool_to_dict(tool_cls: type) -> dict[str, Any]:
    """Tool クラス 1 つ分のマニフェストエントリを組み立てる。

    ``args_schema`` は Pydantic v2 の ``model_json_schema()`` で生の JSON Schema
    (title / type / properties / required) をそのまま埋め込む。Studio 側で
    引数フォームの生成に使う。
    """
    # 1 度インスタンス化して属性を取得 (BaseThorTool は引数なし __init__)
    inst = tool_cls()
    name = getattr(inst, "name", "") or ""
    desc = getattr(inst, "description", "") or ""
    requires_auth = bool(getattr(inst, "requires_auth", True))

    args_schema: dict[str, Any] = {}
    schema_cls = getattr(inst, "args_schema", None)
    if schema_cls is not None and hasattr(schema_cls, "model_json_schema"):
        try:
            args_schema = schema_cls.model_json_schema()
        except Exception:  # noqa: BLE001 - 生成失敗は空にして CI に検出させる
            args_schema = {}

    return {
        "name": name,
        "class_path": f"{tool_cls.__module__}.{tool_cls.__qualname__}",
        "description": desc.strip(),
        "requires_auth": requires_auth,
        "args_schema": args_schema,
    }


def build_tools_manifest() -> dict[str, Any]:
    """``thor.tools.__all__`` を走査して tools.yaml 相当の dict を返す。"""
    import thor.tools as tools_pkg

    entries: list[dict[str, Any]] = []
    for cls_name in tools_pkg.__all__:
        cls = getattr(tools_pkg, cls_name, None)
        if cls is None:
            continue
        entries.append(_tool_to_dict(cls))
    # name でソート (yaml diff を読みやすく、かつ __all__ の並び順に依存しない)
    entries.sort(key=lambda e: e["name"])
    return {
        "version": 1,
        "generated_from": "thor.tools",
        "tools": entries,
    }


# ------------------------------------------------------------------ #
# Crew registry
# ------------------------------------------------------------------ #
def _crew_registry() -> list[dict[str, Any]]:
    """(crew_name, factory, implemented) のリストを返す。

    Router / Analytics は現時点で未実装なので、implemented=False で
    空エントリを生成する (Studio 側にプレースホルダを残す運用)。
    """
    from thor.ingestion.crew import build_ingestion_crew
    from thor.router.crew import build_router_crew

    return [
        {
            "name": "router",
            "factory": lambda: build_router_crew(memory=False),
            "implemented": True,
            "process": "sequential",
        },
        {
            "name": "ingestion",
            # LLM を渡さずに Crew を組み立てられる (Agent には llm=None)
            "factory": lambda: build_ingestion_crew(memory=False),
            "implemented": True,
            "process": "sequential",
        },
        {
            "name": "analytics",
            "factory": None,
            "implemented": False,
            "process": "hierarchical",
            "todo": "TableInspector / Text2SQL / VizPlanner / DashboardBuilder / SummaryWriter を実装後接続する。",
        },
    ]


# ------------------------------------------------------------------ #
# agents.yaml
# ------------------------------------------------------------------ #
def _agent_to_dict(agent: Any, crew_name: str) -> dict[str, Any]:
    return {
        "name": _agent_name_from_role(agent),
        "crew": crew_name,
        "role": getattr(agent, "role", ""),
        "goal": (getattr(agent, "goal", "") or "").strip(),
        "backstory": (getattr(agent, "backstory", "") or "").strip(),
        "tools": _tool_names_from_agent(agent),
        "allow_delegation": bool(getattr(agent, "allow_delegation", False)),
        "memory": bool(getattr(agent, "memory", False)),
    }


def build_agents_manifest() -> dict[str, Any]:
    """全 Crew の Agent を集めた agents.yaml 相当の dict を返す。"""
    agents_out: list[dict[str, Any]] = []
    for entry in _crew_registry():
        crew_name = entry["name"]
        if not entry["implemented"] or entry["factory"] is None:
            continue
        crew = entry["factory"]()
        for agent in getattr(crew, "agents", []) or []:
            agents_out.append(_agent_to_dict(agent, crew_name))
    return {
        "version": 1,
        "generated_from": "thor.{router,ingestion,analytics}.crew",
        "agents": agents_out,
    }


# ------------------------------------------------------------------ #
# crews.yaml
# ------------------------------------------------------------------ #
def _task_to_dict(task: Any, task_id_to_name: dict[int, str]) -> dict[str, Any]:
    output_json = getattr(task, "output_json", None)
    output_model = getattr(output_json, "__name__", None) if output_json else None

    guardrail = getattr(task, "guardrail", None)
    guardrail_ref = None
    if callable(guardrail):
        guardrail_ref = (
            f"{guardrail.__module__}."
            f"{getattr(guardrail, '__qualname__', guardrail.__name__)}"
        )

    agent = getattr(task, "agent", None)

    return {
        "name": task_id_to_name[id(task)],
        "agent": _agent_name_from_role(agent) if agent is not None else None,
        "description": (getattr(task, "description", "") or "").strip(),
        "expected_output": (getattr(task, "expected_output", "") or "").strip(),
        "output_model": output_model,
        "inputs": _extract_input_vars(getattr(task, "description", "") or ""),
        "context": _resolve_context_names(task, task_id_to_name),
        "max_retries": getattr(task, "max_retries", None),
        "guardrail": guardrail_ref,
    }


def _crew_to_dict(entry: dict[str, Any]) -> dict[str, Any]:
    """Crew 1 つ分のマニフェストエントリ。未実装なら空リストで返す。"""
    base: dict[str, Any] = {
        "name": entry["name"],
        "implemented": entry["implemented"],
        "process": entry["process"],
    }
    if not entry["implemented"] or entry["factory"] is None:
        base.update(
            {
                "agents": [],
                "tasks": [],
                "todo": entry.get("todo", ""),
            }
        )
        return base

    crew = entry["factory"]()

    # Task 名の id -> name 辞書を先に組む (context の逆引きに必要)
    tasks = list(getattr(crew, "tasks", []) or [])
    task_id_to_name: dict[int, str] = {}
    for t in tasks:
        n = _task_name_from_output(t)
        if n is None:
            # output_json が無い場合はインデックスで fallback (通常起きない)
            n = f"task_{len(task_id_to_name)}"
        # 衝突があれば sfx を付ける (通常発生しないが安全側)
        base_n = n
        i = 2
        while n in task_id_to_name.values():
            n = f"{base_n}_{i}"
            i += 1
        task_id_to_name[id(t)] = n

    agents_out = [
        _agent_name_from_role(a) for a in (getattr(crew, "agents", []) or [])
    ]
    tasks_out = [_task_to_dict(t, task_id_to_name) for t in tasks]

    base.update(
        {
            "memory": bool(getattr(crew, "memory", False)),
            "agents": agents_out,
            "tasks": tasks_out,
        }
    )
    return base


def build_crews_manifest() -> dict[str, Any]:
    """3 Crew を列挙した crews.yaml 相当の dict を返す。"""
    return {
        "version": 1,
        "generated_from": "thor.{router,ingestion,analytics}.crew",
        "crews": [_crew_to_dict(e) for e in _crew_registry()],
    }


__all__ = [
    "build_tools_manifest",
    "build_agents_manifest",
    "build_crews_manifest",
]

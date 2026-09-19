"""Router Crew のファクトリと kickoff ラッパ (プラン準拠, 2 タスク Sequential)。

`kickoff_router()` は 2 モードで動く:

  * ``mode="heuristic"`` — LLM 未接続時の決定論的ルート。s3://<bucket>/<key>
    正規表現と日本語キーワードで意図を分類する。テストでも利用。
  * ``mode="llm"``       — RouterCrew を実行する (要 LLM)。LLM が
    IntentClassification / DispatchPlan を返す。

**セキュリティ**: Knox JWT / STS 資格情報は :class:`UserContext` に閉じ、
Task の ``inputs`` / ``context`` にも LLM プロンプトにも決して載せない。
子 Crew (Ingestion / Analytics) の起動は Python 側で行う (LLM 経由の
Tool 呼び出しではない) ため、資格情報が LLM に漏れる経路は無い。
"""
from __future__ import annotations

import os
import re
from typing import Any, Literal, Optional

from thor.router.agents import make_dispatcher_agent, make_intent_classifier_agent
from thor.router.models import (
    DispatchPlan,
    IntentClassification,
    IntentLiteral,
    RouterResult,
)
from thor.router.tasks import make_classify_intent_task, make_dispatch_task
from thor.transport.logging import get_logger
from thor.transport.user_context import (
    UserContext,
    reset_user_context,
    set_user_context,
)

try:
    from crewai import Crew, Process  # type: ignore
except Exception:  # pragma: no cover
    class Process:  # type: ignore[no-redef]
        sequential = "sequential"
        hierarchical = "hierarchical"

    class Crew:  # type: ignore[no-redef]
        """crewai.Crew のスタブ。属性保持と no-op kickoff のみ。"""

        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

        def kickoff(self, inputs: Optional[dict[str, Any]] = None) -> Any:
            raise RuntimeError(
                "crewai is not installed. Cannot kickoff Crew in this environment."
            )


_logger = get_logger(__name__)


# ------------------------------------------------------------------ #
# Crew factory
# ------------------------------------------------------------------ #
def build_router_crew(
    llm_light: Optional[Any] = None,
    memory: bool = True,
) -> Crew:
    """Router Crew を組み立てる (2 タスク Sequential)。

    :param llm_light: 軽量モデル (Router / Dispatcher 共通)。
    :param memory: Crew.ai の短期メモリ有効化フラグ。テストでは False。
    """
    classifier = make_intent_classifier_agent(llm=llm_light)
    dispatcher = make_dispatcher_agent(llm=llm_light)

    t_classify = make_classify_intent_task(classifier)
    t_dispatch = make_dispatch_task(dispatcher, context=[t_classify])

    crew = Crew(
        agents=[classifier, dispatcher],
        tasks=[t_classify, t_dispatch],
        process=getattr(Process, "sequential"),
        memory=memory,
        verbose=False,
    )
    return crew


# ------------------------------------------------------------------ #
# Heuristic path (LLM 不要, MVP / テスト用)
# ------------------------------------------------------------------ #
_S3_URI_RE = re.compile(r"s3://([^/\s]+)/([^\s]+)")

_INGEST_KEYWORDS = (
    "取り込", "取込", "インジェスト", "インポート", "テーブルにして",
    "テーブル化", "ingest", "import",
)
_SUMMARY_KEYWORDS = (
    "サマリ", "サマリー", "まとめて", "要約", "概要", "傾向", "分析",
    "summary", "summarize", "summarise",
)
_DASHBOARD_KEYWORDS = (
    "ダッシュボード", "可視化", "グラフ", "チャート", "可視", "見える化",
    "dashboard", "visuali", "chart",
)
_CHITCHAT_KEYWORDS = (
    "こんにちは", "こんばんは", "おはよう", "ありがとう", "ヘルプ",
    "使い方", "何ができ", "hello", "hi ", "help",
)
_PRONOUN_KEYWORDS = ("その", "そのテーブル", "さっき", "先ほど", "that", "the same")


def heuristic_classify(
    prompt: str, entity_memory: Optional[dict[str, Any]] = None
) -> IntentClassification:
    """LLM を使わない決定論的分類。プランのフォールバック用。

    テーブル参照は entity_memory の ``last_table`` を優先。s3 URI が明示的に
    含まれれば INGEST が最優先。
    """
    entity_memory = entity_memory or {}
    p_lower = prompt.lower()

    # 1) INGEST が最優先 (s3 URI があれば確定)
    m = _S3_URI_RE.search(prompt)
    if m or any(kw in prompt for kw in _INGEST_KEYWORDS) or any(kw in p_lower for kw in _INGEST_KEYWORDS):
        if m:
            return IntentClassification(
                intent="INGEST",
                confidence=0.95,
                extracted_args={
                    "bucket": m.group(1),
                    "key": m.group(2),
                    "target_schema": "demo",
                },
                needs_clarification=False,
                reasoning="s3 URI matched by heuristic",
            )
        # キーワードのみ (s3 URI 無し) は情報不足
        return IntentClassification(
            intent="INGEST",
            confidence=0.5,
            extracted_args={},
            needs_clarification=True,
            clarification_prompt=(
                "取り込みたい S3 パスを教えてください (例: s3://demo-bucket/2024/sales.xlsx)。"
            ),
            reasoning="ingest keyword without s3 URI",
        )

    # 2) ANALYZE_DASHBOARD / ANALYZE_SUMMARY
    is_dashboard = any(kw in prompt for kw in _DASHBOARD_KEYWORDS) or any(
        kw in p_lower for kw in _DASHBOARD_KEYWORDS
    )
    is_summary = any(kw in prompt for kw in _SUMMARY_KEYWORDS) or any(
        kw in p_lower for kw in _SUMMARY_KEYWORDS
    )
    if is_dashboard or is_summary:
        intent: IntentLiteral = "ANALYZE_DASHBOARD" if is_dashboard else "ANALYZE_SUMMARY"
        # 代名詞 or 対象未指定 -> entity_memory.last_table を試す
        fq = entity_memory.get("last_table")
        if fq:
            return IntentClassification(
                intent=intent,
                confidence=0.85,
                extracted_args={"fq_table_name": fq, "question": prompt},
                needs_clarification=False,
                reasoning=f"resolved via entity_memory.last_table={fq}",
            )
        return IntentClassification(
            intent=intent,
            confidence=0.5,
            extracted_args={"question": prompt},
            needs_clarification=True,
            clarification_prompt=(
                "対象のテーブル名を教えてください (例: iceberg.demo.sales_2024)。"
            ),
            reasoning="analyze keyword but no last_table in entity_memory",
        )

    # 3) CHITCHAT
    if any(kw in prompt for kw in _CHITCHAT_KEYWORDS) or any(
        kw in p_lower for kw in _CHITCHAT_KEYWORDS
    ):
        return IntentClassification(
            intent="CHITCHAT",
            confidence=0.8,
            extracted_args={},
            needs_clarification=False,
            reasoning="chitchat keyword",
        )

    # 4) UNKNOWN
    return IntentClassification(
        intent="UNKNOWN",
        confidence=0.3,
        extracted_args={},
        needs_clarification=True,
        clarification_prompt=(
            "ご依頼の内容を『S3 パスを取り込む』『テーブルをサマリーする』"
            "『テーブルからダッシュボードを作る』のいずれかで教えてください。"
        ),
        reasoning="no heuristic matched",
    )


def build_dispatch_plan(
    classification: IntentClassification,
) -> DispatchPlan:
    """IntentClassification から DispatchPlan を作る (heuristic / llm 共通)。"""
    intent = classification.intent
    args = dict(classification.extracted_args or {})

    if classification.needs_clarification:
        return DispatchPlan(
            intent=intent,
            child_crew="none",
            inputs={},
            response_markdown=(
                classification.clarification_prompt
                or "もう少し詳しく教えてください。"
            ),
            skip_child=True,
        )

    if intent == "INGEST":
        # 必須引数チェック (LLM が抜けたら clarify)
        if "bucket" not in args or "key" not in args:
            return DispatchPlan(
                intent=intent,
                child_crew="none",
                inputs={},
                response_markdown=(
                    "取り込み対象の S3 パス (bucket / key) が特定できませんでした。"
                    "s3://<bucket>/<key> の形で指定してください。"
                ),
                skip_child=True,
            )
        args.setdefault("target_schema", "demo")
        return DispatchPlan(
            intent=intent,
            child_crew="ingestion",
            inputs=args,
            response_markdown="",
            skip_child=False,
        )

    if intent == "ANALYZE_SUMMARY":
        if not args.get("fq_table_name"):
            return DispatchPlan(
                intent=intent,
                child_crew="none",
                inputs={},
                response_markdown="サマリー対象のテーブル名を教えてください。",
                skip_child=True,
            )
        return DispatchPlan(
            intent=intent,
            child_crew="analytics_summary",
            inputs=args,
            response_markdown="",
            skip_child=False,
        )

    if intent == "ANALYZE_DASHBOARD":
        if not args.get("fq_table_name"):
            return DispatchPlan(
                intent=intent,
                child_crew="none",
                inputs={},
                response_markdown="ダッシュボード対象のテーブル名を教えてください。",
                skip_child=True,
            )
        return DispatchPlan(
            intent=intent,
            child_crew="analytics_dashboard",
            inputs=args,
            response_markdown="",
            skip_child=False,
        )

    if intent == "CHITCHAT":
        return DispatchPlan(
            intent=intent,
            child_crew="none",
            inputs={},
            response_markdown=(
                "Thor です。S3 パスを取り込むか、既存テーブルからサマリー / "
                "ダッシュボードを作れます。何をしましょうか?"
            ),
            skip_child=True,
        )

    # UNKNOWN fallback
    return DispatchPlan(
        intent=intent,
        child_crew="none",
        inputs={},
        response_markdown=(
            "ご依頼の内容を判別できませんでした。もう少し具体的に教えてください。"
        ),
        skip_child=True,
    )


# ------------------------------------------------------------------ #
# kickoff wrapper
# ------------------------------------------------------------------ #
RouterMode = Literal["heuristic", "llm", "auto"]


def _resolve_mode(mode: RouterMode) -> RouterMode:
    """``auto`` を環境変数から解決する。

    ``THOR_ROUTER_MODE`` が 'llm' で、かつ LLM 環境変数
    (``CAI_INFERENCE_ENDPOINT`` 等) が最低 1 つ設定されていれば 'llm'、
    それ以外は 'heuristic'。
    """
    if mode != "auto":
        return mode
    env_mode = os.environ.get("THOR_ROUTER_MODE", "heuristic").lower()
    if env_mode == "llm":
        return "llm"
    return "heuristic"


def kickoff_router(
    *,
    user_ctx: UserContext,
    prompt: str,
    session_id: Optional[str] = None,
    entity_memory: Optional[dict[str, Any]] = None,
    llm_light: Optional[Any] = None,
    mode: RouterMode = "auto",
) -> RouterResult:
    """Router を 1 回実行して :class:`RouterResult` を返す。

    :param user_ctx: エンドユーザーの実行コンテキスト (Knox JWT / STS を含む)。
    :param prompt:   ユーザーの発話。
    :param session_id: セッション ID (未指定なら user_ctx.session_id)。
    :param entity_memory: 現在セッションの entity_memory スナップショット。
    :param llm_light: LLM (mode='llm' の時のみ使う)。
    :param mode:  'heuristic' / 'llm' / 'auto' (環境変数で決定)。

    :returns: :class:`RouterResult`。呼び出し側は ``plan.skip_child`` を見て
        子 Crew を起動するかどうか決める。
    """
    resolved_mode = _resolve_mode(mode)
    sid = session_id or user_ctx.session_id or "unknown"
    entity_memory = entity_memory or {}

    if resolved_mode == "heuristic" or llm_light is None:
        _logger.info(
            "router.heuristic",
            user=user_ctx.user_name,
            session_id=sid,
            prompt_len=len(prompt),
        )
        classification = heuristic_classify(prompt, entity_memory)
        plan = build_dispatch_plan(classification)
        return RouterResult(
            classification=classification,
            plan=plan,
            response_markdown=plan.response_markdown,
        )

    # LLM 経路
    token = set_user_context(user_ctx)
    try:
        crew = build_router_crew(llm_light=llm_light, memory=True)
        _logger.info(
            "router.llm_kickoff",
            user=user_ctx.user_name,
            session_id=sid,
            prompt_len=len(prompt),
        )
        try:
            raw = crew.kickoff(
                inputs={
                    "prompt": prompt,
                    "session_id": sid,
                    "entity_memory_hint": _summarize_entity_memory(entity_memory),
                }
            )
        except Exception as e:  # noqa: BLE001
            _logger.error(
                "router.crew_exception",
                user=user_ctx.user_name,
                session_id=sid,
                error=str(e),
            )
            # LLM が落ちても heuristic にフォールバック
            classification = heuristic_classify(prompt, entity_memory)
            plan = build_dispatch_plan(classification)
            return RouterResult(
                classification=classification,
                plan=plan,
                response_markdown=plan.response_markdown,
            )
    finally:
        reset_user_context(token)

    classification = _extract_classification(raw) or heuristic_classify(
        prompt, entity_memory
    )
    plan = _extract_plan(raw) or build_dispatch_plan(classification)
    return RouterResult(
        classification=classification,
        plan=plan,
        response_markdown=plan.response_markdown,
    )


def _summarize_entity_memory(entity_memory: dict[str, Any]) -> str:
    """entity_memory を LLM プロンプトに安全に載せられる短い文字列にする。

    Knox JWT / STS のような秘匿情報が万一入っていても、`_SAFE_KEYS` 以外は
    渡さない。
    """
    _SAFE_KEYS = {
        "last_table",
        "last_dashboard_id",
        "last_s3_path",
        "last_ossie_path",
        "last_summary_artifact_id",
    }
    if not entity_memory:
        return "(no entity memory yet)"
    parts = []
    for k in sorted(_SAFE_KEYS):
        v = entity_memory.get(k)
        if v is not None:
            parts.append(f"{k}={v}")
    return "; ".join(parts) if parts else "(no entity memory yet)"


def _extract_classification(raw: Any) -> Optional[IntentClassification]:
    """Crew 出力から IntentClassification を最善努力で取り出す。"""
    return _extract_model(raw, IntentClassification, task_index=0)


def _extract_plan(raw: Any) -> Optional[DispatchPlan]:
    """Crew 出力から DispatchPlan を最善努力で取り出す (最終タスク出力)。"""
    return _extract_model(raw, DispatchPlan, task_index=-1)


def _extract_model(raw: Any, model_cls: type, task_index: int) -> Optional[Any]:
    if raw is None:
        return None
    # crewai の CrewOutput は .tasks_output[i].pydantic を持つ
    tasks_output = getattr(raw, "tasks_output", None)
    if tasks_output:
        try:
            task_out = tasks_output[task_index]
        except (IndexError, TypeError):
            task_out = None
        if task_out is not None:
            for attr in ("pydantic", "json_dict", "raw"):
                payload = getattr(task_out, attr, None)
                if payload is None:
                    continue
                if isinstance(payload, model_cls):
                    return payload
                if isinstance(payload, dict):
                    try:
                        return model_cls.model_validate(payload)
                    except Exception:  # noqa: BLE001
                        continue
    # フォールバック: 最終出力のみを想定した属性
    for attr in ("pydantic", "json_dict", "raw"):
        payload = getattr(raw, attr, None)
        if payload is None:
            continue
        if isinstance(payload, model_cls):
            return payload
        if isinstance(payload, dict):
            try:
                return model_cls.model_validate(payload)
            except Exception:  # noqa: BLE001
                continue
    if isinstance(raw, model_cls):
        return raw
    if isinstance(raw, dict):
        try:
            return model_cls.model_validate(raw)
        except Exception:  # noqa: BLE001
            return None
    return None


__all__ = [
    "build_router_crew",
    "kickoff_router",
    "heuristic_classify",
    "build_dispatch_plan",
]

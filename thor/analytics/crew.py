"""Analytics Crew のファクトリと kickoff ラッパ (Summary パス, Sequential)。

**方針**: プランは Hierarchical (Manager が Summary / Dashboard を選ぶ) を
最終形として掲げているが、まずは Sequential の 2 タスク Crew を Summary
専用として実装する。Router が既に intent (ANALYZE_SUMMARY /
ANALYZE_DASHBOARD) を分けているので、Python 側で該当 Crew を選べば十分。
Dashboard パスが実装されたら別 Crew (:func:`build_analytics_dashboard_crew`)
として並置し、必要に応じて後日 Manager Crew に統合する。

**セキュリティ**:
  * 副作用なし (SELECT / SHOW STATS / OssieRead のみ) だが、Trino アクセスは
    必ずエンドユーザーの Knox JWT を使う (Ranger 判定はユーザー主体)。
  * Knox JWT / STS は ``UserContext`` (ContextVar) 経由でのみ Tool が参照し、
    Task の ``inputs`` / ``context`` にも LLM プロンプトにも決して載せない。
"""
from __future__ import annotations

from typing import Any, Optional

from thor.analytics.agents import (
    make_summary_writer_agent,
    make_table_inspector_agent,
)
from thor.analytics.models import SummaryReport
from thor.analytics.tasks import (
    inspect_table_guardrail,
    make_inspect_table_task,
    make_write_summary_task,
)
from thor.transport.errors import ErrorCode, err, ok
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
def build_analytics_summary_crew(
    llm_light: Optional[Any] = None,
    llm_strong: Optional[Any] = None,
    memory: bool = True,
) -> Crew:
    """Summary パスの Analytics Crew を組み立てる (Sequential 2 タスク)。

    :param llm_light: 軽量モデル (TableInspector)。
    :param llm_strong: 精度重視モデル (SummaryWriter)。
    :param memory: Crew.ai の短期メモリを有効化するか。
    """
    inspector = make_table_inspector_agent(llm=llm_light)
    writer = make_summary_writer_agent(llm=llm_strong)

    t_inspect = make_inspect_table_task(inspector)
    # guardrail は crewai の Task 属性で設定 (setattr で保険)
    setattr(t_inspect, "guardrail", inspect_table_guardrail)

    t_summary = make_write_summary_task(writer, context=[t_inspect])

    crew = Crew(
        agents=[inspector, writer],
        tasks=[t_inspect, t_summary],
        process=getattr(Process, "sequential"),
        memory=memory,
        verbose=False,
    )
    return crew


# ------------------------------------------------------------------ #
# kickoff wrapper
# ------------------------------------------------------------------ #
def kickoff_analytics_summary(
    *,
    user_ctx: UserContext,
    fq_table_name: str,
    question: Optional[str] = None,
    llm_light: Optional[Any] = None,
    llm_strong: Optional[Any] = None,
) -> dict[str, Any]:
    """Summary Crew を 1 回実行する。

    :param user_ctx: エンドユーザー実行コンテキスト (Knox JWT を含む)。
    :param fq_table_name: 対象テーブル (catalog.schema.table)。
    :param question: 具体的な問い (省略可、Crew が使えるように渡すだけ)。
    :param llm_light: 軽量モデル。
    :param llm_strong: 精度重視モデル。

    戻り値は :func:`thor.transport.errors.ok` / :func:`err` 形式。成功時は
    ``payload["report"]`` に :class:`SummaryReport` の dict を含む。
    ``fq_table_name`` が 3 セグメントでない (catalog.schema.table でない) 場合
    は BAD_REQUEST 相当のエラーで即座に返す (LLM を回さない)。
    """
    if not fq_table_name or fq_table_name.count(".") != 2:
        return err(
            "BAD_REQUEST",
            "fq_table_name must be 'catalog.schema.table' (got: "
            f"{fq_table_name!r}).",
        )

    token = set_user_context(user_ctx)
    try:
        crew = build_analytics_summary_crew(
            llm_light=llm_light, llm_strong=llm_strong, memory=True
        )
        _logger.info(
            "analytics_summary.kickoff",
            user=user_ctx.user_name,
            fq_table_name=fq_table_name,
            request_id=user_ctx.request_id,
        )
        try:
            result = crew.kickoff(
                inputs={
                    "fq_table_name": fq_table_name,
                    # question は Crew が使わなくても Task 変数として置いておく
                    # (将来 write_summary_task が {question} を使う可能性に備える)
                    "question": question or "",
                }
            )
        except Exception as e:  # noqa: BLE001
            _logger.error(
                "analytics_summary.crew_exception",
                user=user_ctx.user_name,
                fq_table_name=fq_table_name,
                error=str(e),
                request_id=user_ctx.request_id,
            )
            return err(
                ErrorCode.HTTP_UNAVAILABLE,
                f"AnalyticsSummaryCrew kickoff failed: {type(e).__name__}: {e}",
            )

        report = _extract_summary_report(result)
        return ok(
            {
                "report": report.model_dump(exclude_none=True) if report else None,
                "raw": _safe_repr(result),
            }
        )
    finally:
        reset_user_context(token)


def _extract_summary_report(result: Any) -> Optional[SummaryReport]:
    """Crew の最終出力を SummaryReport にキャストする (best-effort)。"""
    if result is None:
        return None
    for attr in ("pydantic", "json_dict", "raw"):
        payload = getattr(result, attr, None)
        if payload is None:
            continue
        if isinstance(payload, SummaryReport):
            return payload
        if isinstance(payload, dict):
            try:
                return SummaryReport.model_validate(payload)
            except Exception:  # noqa: BLE001
                continue
    if isinstance(result, SummaryReport):
        return result
    if isinstance(result, dict):
        try:
            return SummaryReport.model_validate(result)
        except Exception:  # noqa: BLE001
            return None
    return None


def _safe_repr(result: Any) -> str:
    try:
        return repr(result)[:1000]
    except Exception:  # noqa: BLE001
        return f"<{type(result).__name__}>"


__all__ = [
    "build_analytics_summary_crew",
    "kickoff_analytics_summary",
]

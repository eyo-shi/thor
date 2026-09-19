"""Ingestion Crew のファクトリと kickoff ラッパ。

Crew.ai の :class:`Crew` を組み立て、``Process.sequential`` で 8 タスクを
順に流す。副作用 (CREATE / Git commit) を持つタスクは ``max_retries=0``。

呼び出し側は :func:`kickoff_ingestion` を使う想定。この関数は:

  1. :class:`UserContext` を :func:`set_user_context` で ContextVar にセット
  2. Crew を kickoff し、最終出力を :class:`IngestionReport` として返す
  3. どんな例外もキャッチして構造化エラーにする (LLM 再試行暴走の防止)

**注意**: Knox JWT や STS 資格情報は決して Task の ``inputs`` / ``context``
に渡さない。Tool 側で ContextVar から直接取り出す。
"""
from __future__ import annotations

from typing import Any, Optional

from thor.ingestion.agents import (
    make_format_sniffer_agent,
    make_ossie_drafter_agent,
    make_s3_scout_agent,
    make_schema_drafter_agent,
    make_table_creator_agent,
)
from thor.ingestion.models import IngestionReport
from thor.ingestion.tasks import (
    conflict_permissions_guardrail,
    make_check_conflict_and_permissions_task,
    make_create_iceberg_table_task,
    make_draft_ossie_task,
    make_extract_dataframe_task,
    make_locate_s3_object_task,
    make_propose_schema_and_name_task,
    make_sniff_format_task,
    make_wrap_up_task,
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
def build_ingestion_crew(
    llm_light: Optional[Any] = None,
    llm_strong: Optional[Any] = None,
    memory: bool = True,
) -> Crew:
    """Ingestion Crew を組み立てる。

    :param llm_light: 軽量モデル (S3Scout / FormatSniffer / OssieDrafter)。
    :param llm_strong: 精度重視モデル (SchemaDrafter / TableCreator)。
    :param memory: Crew.ai の短期メモリを有効化するか。ユニットテストでは
        False にしてクリーンに走らせる。
    """
    # Agents
    s3_scout = make_s3_scout_agent(llm=llm_light)
    format_sniffer = make_format_sniffer_agent(llm=llm_light)
    schema_drafter = make_schema_drafter_agent(llm=llm_strong)
    table_creator = make_table_creator_agent(llm=llm_strong)
    ossie_drafter = make_ossie_drafter_agent(llm=llm_light)

    # Tasks
    t_locate = make_locate_s3_object_task(s3_scout)
    t_sniff = make_sniff_format_task(format_sniffer, context=[t_locate])
    t_extract = make_extract_dataframe_task(format_sniffer, context=[t_sniff])
    t_propose = make_propose_schema_and_name_task(
        schema_drafter, context=[t_locate, t_sniff, t_extract]
    )
    t_check = make_check_conflict_and_permissions_task(
        table_creator, context=[t_propose]
    )
    # guardrail は crewai の Task 属性で設定 (setattr で保険)
    setattr(t_check, "guardrail", conflict_permissions_guardrail)

    t_create = make_create_iceberg_table_task(
        table_creator, context=[t_propose, t_check]
    )
    t_ossie = make_draft_ossie_task(
        ossie_drafter,
        context=[t_locate, t_propose, t_create, t_extract],
    )
    t_wrap = make_wrap_up_task(
        ossie_drafter,
        context=[t_locate, t_propose, t_create, t_ossie],
    )

    crew = Crew(
        agents=[s3_scout, format_sniffer, schema_drafter, table_creator, ossie_drafter],
        tasks=[t_locate, t_sniff, t_extract, t_propose, t_check, t_create, t_ossie, t_wrap],
        process=getattr(Process, "sequential"),
        memory=memory,
        verbose=False,
    )
    return crew


# ------------------------------------------------------------------ #
# kickoff wrapper
# ------------------------------------------------------------------ #
def kickoff_ingestion(
    *,
    user_ctx: UserContext,
    bucket: str,
    key: str,
    target_schema: str,
    llm_light: Optional[Any] = None,
    llm_strong: Optional[Any] = None,
) -> dict[str, Any]:
    """Ingestion Crew を 1 回実行する。

    :param user_ctx: エンドユーザーの実行コンテキスト (Knox JWT / STS を含む)。
    :param bucket: 取り込み対象 S3 バケット。
    :param key: 取り込み対象オブジェクトキー。
    :param target_schema: 作成先の Trino/Iceberg スキーマ。
    :param llm_light: 軽量モデル (LiteLLM instance など)。
    :param llm_strong: 精度重視モデル。

    戻り値は :func:`thor.transport.errors.ok` / :func:`err` 形式。成功時は
    ``payload["report"]`` に :class:`IngestionReport` の dict を含む。
    """
    token = set_user_context(user_ctx)
    try:
        crew = build_ingestion_crew(
            llm_light=llm_light, llm_strong=llm_strong, memory=True
        )
        _logger.info(
            "ingestion.kickoff",
            user=user_ctx.user_name,
            bucket=bucket,
            key=key,
            target_schema=target_schema,
            request_id=user_ctx.request_id,
        )
        try:
            result = crew.kickoff(
                inputs={
                    "bucket": bucket,
                    "key": key,
                    "target_schema": target_schema,
                }
            )
        except Exception as e:  # noqa: BLE001
            _logger.error(
                "ingestion.crew_exception",
                user=user_ctx.user_name,
                error=str(e),
                request_id=user_ctx.request_id,
            )
            return err(
                ErrorCode.HTTP_UNAVAILABLE,
                f"IngestionCrew kickoff failed: {type(e).__name__}: {e}",
            )

        report = _extract_report(result)
        return ok(
            {
                "report": report.model_dump(exclude_none=True) if report else None,
                "raw": _safe_repr(result),
            }
        )
    finally:
        reset_user_context(token)


def _extract_report(result: Any) -> Optional[IngestionReport]:
    """Crew の最終出力を IngestionReport にキャストする (best-effort)。"""
    if result is None:
        return None
    # crewai の CrewOutput は .json_dict / .raw / .pydantic を持つ
    for attr in ("pydantic", "json_dict", "raw"):
        payload = getattr(result, attr, None)
        if payload is None:
            continue
        if isinstance(payload, IngestionReport):
            return payload
        if isinstance(payload, dict):
            try:
                return IngestionReport.model_validate(payload)
            except Exception:  # noqa: BLE001
                continue
    # 直接 dict / IngestionReport が返っている場合
    if isinstance(result, IngestionReport):
        return result
    if isinstance(result, dict):
        try:
            return IngestionReport.model_validate(result)
        except Exception:  # noqa: BLE001
            return None
    return None


def _safe_repr(result: Any) -> str:
    try:
        return repr(result)[:1000]
    except Exception:  # noqa: BLE001
        return f"<{type(result).__name__}>"


__all__ = ["build_ingestion_crew", "kickoff_ingestion"]

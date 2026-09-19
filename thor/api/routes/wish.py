"""Wish (自然言語プロンプト) SSE エンドポイント (`POST /api/wish`)。

ChatPane からのプロンプトを :func:`kickoff_router` に渡し、判定された intent
に応じて子 Crew (現時点では Ingestion のみ実装) を Python 側から起動する。
実行進捗は Server-Sent Events で逐次返す。

Router モードは :func:`kickoff_router` の ``mode='auto'`` に任せる:

  * ``THOR_ROUTER_MODE=llm`` かつ LLM が組み立てられる場合 -> LLM 経路
  * それ以外 -> heuristic (s3 URI 正規表現 + 日本語キーワード)

**注意**:
* Knox JWT は :class:`UserContext` に載せて ContextVar にセットしてから
  Crew を kickoff する (Task の inputs に載せない)
* Crew.kickoff は同期呼び出しなので :func:`asyncio.to_thread` で退避
* クライアント切断は ``request.is_disconnected`` で検出し、次イベントで抜ける
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Annotated, AsyncIterator, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from thor.api.auth import get_user_context
from thor.api.sse import sse_artifact, sse_done, sse_error, sse_step, sse_token
from thor.api.state import SessionTurn, get_store
from thor.ingestion.crew import kickoff_ingestion
from thor.router import DispatchPlan, RouterResult, kickoff_router
from thor.transport.logging import get_logger
from thor.transport.user_context import (
    UserContext,
    reset_user_context,
    set_user_context,
)

router = APIRouter(prefix="/api", tags=["wish"])
_logger = get_logger(__name__)


class WishRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=8192)
    session_id: Optional[str] = None
    #: Ingestion 用: 未指定なら Router 側で "demo"
    target_schema: Optional[str] = Field(None, max_length=128)


@router.post("/wish")
async def post_wish(
    body: WishRequest,
    request: Request,
    user_ctx: Annotated[UserContext, Depends(get_user_context)],
) -> EventSourceResponse:
    """SSE で Router + 子 Crew の実行を逐次配信する。"""
    store = get_store()
    sid = body.session_id or user_ctx.session_id
    session = store.get_or_create_session(sid, user_ctx.user_name)
    turn_id = f"turn_{uuid.uuid4().hex[:10]}"

    async def stream() -> AsyncIterator[dict]:
        artifacts_created: list[str] = []
        response_md_parts: list[str] = []
        error_holder: list[Optional[str]] = [None]

        try:
            # 1) Router — heuristic モードは同期でも十分速い
            yield sse_step("RouterCrew", "running", "意図を分類しています...")
            router_result = await asyncio.to_thread(
                _run_router,
                user_ctx=user_ctx,
                prompt=body.prompt,
                session_id=session.session_id,
                entity_memory=dict(session.entity_memory),
                target_schema_override=body.target_schema,
            )
            yield sse_step(
                "RouterCrew",
                "done",
                f"intent={router_result.classification.intent}",
            )

            plan = router_result.plan

            # 2) skip_child (CHITCHAT / clarify / UNKNOWN) は response_markdown を流す
            if plan.skip_child:
                md = router_result.response_markdown or plan.response_markdown or ""
                if md:
                    response_md_parts.append(md)
                    yield sse_token(md)
                # clarify / unknown はエラーではないので error_holder は None のまま
                return

            # 3) 子 Crew ディスパッチ
            if plan.child_crew == "ingestion":
                async for chunk in _handle_ingest(
                    request=request,
                    user_ctx=user_ctx,
                    session_id=session.session_id,
                    turn_id=turn_id,
                    bucket=str(plan.inputs.get("bucket", "")),
                    key=str(plan.inputs.get("key", "")),
                    target_schema=str(plan.inputs.get("target_schema") or "demo"),
                    artifacts_created=artifacts_created,
                    response_md_parts=response_md_parts,
                    error_holder=error_holder,
                ):
                    yield chunk
            elif plan.child_crew in ("analytics_summary", "analytics_dashboard"):
                # AnalyticsCrew 未実装 (Router では intent は取れるが実行手段が無い)
                error_holder[0] = "NOT_IMPLEMENTED"
                yield sse_error(
                    "NOT_IMPLEMENTED",
                    "AnalyticsCrew (サマリー / ダッシュボード) は未実装です。"
                    "現時点では Ingestion のみ対応しています。",
                )
            else:
                # child_crew='none' で skip_child=false は理論上ありえない
                error_holder[0] = "INTERNAL_ERROR"
                yield sse_error(
                    "INTERNAL_ERROR",
                    f"Unexpected dispatch plan: child_crew={plan.child_crew}",
                )

        except asyncio.CancelledError:
            _logger.info(
                "wish.cancelled",
                session_id=session.session_id,
                turn_id=turn_id,
            )
            raise
        except Exception as e:  # noqa: BLE001
            _logger.error(
                "wish.unexpected",
                session_id=session.session_id,
                turn_id=turn_id,
                error=str(e),
            )
            error_holder[0] = "INTERNAL_ERROR"
            yield sse_error(
                "INTERNAL_ERROR",
                f"Unexpected error: {type(e).__name__}: {e}",
            )
        finally:
            # 会話履歴に turn を追記
            store.append_turn(
                session.session_id,
                SessionTurn(
                    turn_id=turn_id,
                    prompt=body.prompt,
                    response_markdown="".join(response_md_parts),
                    artifact_ids=artifacts_created,
                    error_code=error_holder[0],
                ),
            )
            yield sse_done(
                turn_id=turn_id,
                artifacts=artifacts_created,
                ok=(error_holder[0] is None),
            )

    return EventSourceResponse(stream(), ping=15)


# ------------------------------------------------------------------ #
# Router 実行 (asyncio.to_thread で退避)
# ------------------------------------------------------------------ #
def _run_router(
    *,
    user_ctx: UserContext,
    prompt: str,
    session_id: str,
    entity_memory: dict,
    target_schema_override: Optional[str],
) -> RouterResult:
    """Router を実行し、target_schema の明示指定を plan に反映する。"""
    token = set_user_context(user_ctx)
    try:
        result = kickoff_router(
            user_ctx=user_ctx,
            prompt=prompt,
            session_id=session_id,
            entity_memory=entity_memory,
            # llm_light は未接続 (LiteLLM の構築は API 起動時に別途注入)
            llm_light=None,
            mode="auto",
        )
    finally:
        reset_user_context(token)

    # body.target_schema が明示されていれば Router の判定を上書き
    if (
        target_schema_override
        and result.plan.child_crew == "ingestion"
        and not result.plan.skip_child
    ):
        new_inputs = dict(result.plan.inputs)
        new_inputs["target_schema"] = target_schema_override
        result = RouterResult(
            classification=result.classification,
            plan=DispatchPlan(
                intent=result.plan.intent,
                child_crew=result.plan.child_crew,
                inputs=new_inputs,
                response_markdown=result.plan.response_markdown,
                skip_child=result.plan.skip_child,
            ),
            response_markdown=result.response_markdown,
        )
    return result


# ------------------------------------------------------------------ #
# Ingestion path
# ------------------------------------------------------------------ #
async def _handle_ingest(
    *,
    request: Request,
    user_ctx: UserContext,
    session_id: str,
    turn_id: str,
    bucket: str,
    key: str,
    target_schema: str,
    artifacts_created: list[str],
    response_md_parts: list[str],
    error_holder: list[Optional[str]],
) -> AsyncIterator[dict]:
    """Ingestion Crew を走らせて SSE イベントを yield する。"""
    if not bucket or not key:
        error_holder[0] = "INGESTION_MISSING_ARGS"
        yield sse_error(
            "INGESTION_MISSING_ARGS",
            "取り込み対象の S3 パス (bucket / key) が特定できませんでした。",
        )
        return

    yield sse_step(
        "IngestionCrew", "running", f"s3://{bucket}/{key} を取り込みます..."
    )

    # UserContext を持ち込む session_id つきの派生を用意
    scoped_ctx = UserContext(
        user_name=user_ctx.user_name,
        groups=user_ctx.groups,
        knox_jwt=user_ctx.knox_jwt,
        aws_credentials=user_ctx.aws_credentials,
        session_id=session_id,
    )

    # crew.kickoff は同期 & LLM 呼び出し込みで長い → to_thread で退避
    def _run() -> dict:
        # ContextVar は thread ごとに独立なので、この thread 内で set する
        token = set_user_context(scoped_ctx)
        try:
            return kickoff_ingestion(
                user_ctx=scoped_ctx,
                bucket=bucket,
                key=key,
                target_schema=target_schema,
            )
        finally:
            reset_user_context(token)

    task = asyncio.create_task(asyncio.to_thread(_run))

    # 実行中は 5 秒に 1 回「進行中」の step イベントを出しつつ、
    # クライアント切断も監視する
    while not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
        except asyncio.TimeoutError:
            if await request.is_disconnected():
                task.cancel()
                return
            yield sse_step("IngestionCrew", "running", "Crew 実行中...")

    result = await task
    if result.get("status") != "ok":
        code = result.get("error_code", "INGESTION_FAILED")
        msg = result.get("message", "Ingestion failed")
        error_holder[0] = code
        yield sse_error(code, msg)
        return

    report = (result.get("payload") or {}).get("report") or {}
    fq = report.get("fq_table_name")
    if fq:
        store = get_store()
        art = store.register_artifact(
            "table_preview",
            ref={"fq": fq, "catalog_schema_table": fq},
            session_id=session_id,
        )
        artifacts_created.append(art.artifact_id)
        store.update_entity_memory(
            session_id,
            {
                "last_table": fq,
                "last_s3_path": f"s3://{bucket}/{key}",
                "last_ossie_path": report.get("ossie_yaml_path"),
            },
        )
        yield sse_artifact(
            art.artifact_id,
            "table_preview",
            ref={"fq": fq},
        )

    md = _format_report_markdown(report, bucket, key)
    response_md_parts.append(md)
    yield sse_token(md)
    yield sse_step("IngestionCrew", "done", "取り込みが完了しました。")


def _format_report_markdown(
    report: dict, bucket: str, key: str
) -> str:
    """IngestionReport を ChatPane に流す Markdown にする。"""
    if not report:
        return f"s3://{bucket}/{key} の取り込みを試みましたが、結果が取得できませんでした。"
    fq = report.get("fq_table_name", "?")
    cols = report.get("column_count", 0)
    ossie = report.get("ossie_yaml_path", "")
    similar = report.get("similar_tables") or []
    lines = [
        f"**取り込み完了**: `{fq}` (columns: {cols})",
        f"- source: `s3://{bucket}/{key}`",
    ]
    if ossie:
        lines.append(f"- Ossie: `{ossie}`")
    if similar:
        lines.append(
            "- 類似テーブル: "
            + ", ".join(f"`{s.get('fq_name', s)}`" for s in similar[:3])
        )
    extra = report.get("summary_markdown")
    if extra:
        lines.append("")
        lines.append(extra)
    return "\n".join(lines) + "\n"

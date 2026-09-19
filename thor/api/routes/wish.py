"""Wish (自然言語プロンプト) SSE エンドポイント (`POST /api/wish`)。

ChatPane からのプロンプトを受け取り、Router → Crew の実行進捗を
Server-Sent Events で逐次返す。Router / Analytics Crew が未実装のうちは
以下のヒューリスティックで振り分ける:

* プロンプト中に ``s3://<bucket>/<key>`` が現れる → Ingestion Crew に流す
* それ以外 → 未実装エラーを ``event: error`` で返す

**注意**:
* Knox JWT は :class:`UserContext` に載せて ContextVar にセットしてから
  Crew を kickoff する (Task の inputs に載せない)
* Crew.kickoff は同期呼び出しなので :func:`asyncio.to_thread` で退避
* クライアント切断は ``request.is_disconnected`` で検出し、次イベントで抜ける
"""
from __future__ import annotations

import asyncio
import re
import uuid
from typing import Annotated, AsyncIterator, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from thor.api.auth import get_user_context
from thor.api.sse import sse_artifact, sse_done, sse_error, sse_step, sse_token
from thor.api.state import SessionTurn, get_store
from thor.ingestion.crew import kickoff_ingestion
from thor.transport.logging import get_logger
from thor.transport.user_context import (
    UserContext,
    reset_user_context,
    set_user_context,
)

router = APIRouter(prefix="/api", tags=["wish"])
_logger = get_logger(__name__)

_S3_URI_RE = re.compile(r"s3://([^/\s]+)/([^\s]+)")


class WishRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=8192)
    session_id: Optional[str] = None
    #: Ingestion 用: 未指定なら "demo"
    target_schema: Optional[str] = Field(None, max_length=128)


@router.post("/wish")
async def post_wish(
    body: WishRequest,
    request: Request,
    user_ctx: Annotated[UserContext, Depends(get_user_context)],
) -> EventSourceResponse:
    """SSE で Crew 実行を逐次配信する。"""
    store = get_store()
    # body.session_id が優先、次いで X-Thor-Session-Id ヘッダから拾う
    sid = body.session_id or user_ctx.session_id
    session = store.get_or_create_session(sid, user_ctx.user_name)
    turn_id = f"turn_{uuid.uuid4().hex[:10]}"

    async def stream() -> AsyncIterator[dict]:
        artifacts_created: list[str] = []
        response_md_parts: list[str] = []
        # ネストされたコルーチンからエラーコードを伝えるための可変ホルダ
        error_holder: list[Optional[str]] = [None]
        try:
            # 意図分類 (ヒューリスティック; RouterCrew 実装後に差し替え)
            m = _S3_URI_RE.search(body.prompt)
            if m:
                async for chunk in _handle_ingest(
                    request=request,
                    user_ctx=user_ctx,
                    session_id=session.session_id,
                    turn_id=turn_id,
                    bucket=m.group(1),
                    key=m.group(2),
                    target_schema=body.target_schema or "demo",
                    artifacts_created=artifacts_created,
                    response_md_parts=response_md_parts,
                    error_holder=error_holder,
                ):
                    yield chunk
            else:
                # RouterCrew / AnalyticsCrew 未実装のフォールバック
                error_holder[0] = "NOT_IMPLEMENTED"
                yield sse_error(
                    "NOT_IMPLEMENTED",
                    "Ingestion 以外のプロンプトは現在未対応です。"
                    "s3://bucket/key を含めて取り込みを依頼してください。",
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

    # 過負荷対策: SSE の keepalive を 15 秒間隔で送る
    return EventSourceResponse(stream(), ping=15)


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

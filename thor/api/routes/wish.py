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
from typing import Annotated, Any, AsyncIterator, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from thor.analytics.crew import (
    kickoff_analytics_dashboard,
    kickoff_analytics_summary,
)
from thor.api.auth import get_user_context
from thor.api.sse import sse_artifact, sse_done, sse_error, sse_step, sse_token
from thor.api.state import SessionTurn, get_store
from thor.ingestion.crew import kickoff_ingestion
from thor.router import DispatchPlan, RouterResult, kickoff_router
from thor.transport.config import get_llm_config
from thor.transport.llm_factory import build_llm_pair
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
) -> Any:
    """SSE で Router + 子 Crew の実行を逐次配信する。

    LLM が未設定なら SSE を開かず HTTP 503 + guided JSON を返す。UI
    (:mod:`thor_ui.src.api.client`) は 503 レスポンスから
    :class:`SetupGuideError` を投げ、ChatPane が SetupGuide カードを出す。
    """
    # LLM 未設定なら SSE を開かず 503 で早期リターン (UI が SetupGuide 表示)。
    # env 変更 → Application 再起動で解消する運用。
    if get_llm_config() is None:
        return JSONResponse(
            status_code=503,
            content={
                "error_code": "LLM_NOT_CONFIGURED",
                "message": "LLM プロバイダが設定されていません。",
                "instruction": (
                    "Project → Settings → Advanced → Environment Variables に "
                    "THOR_LLM_PROVIDER (cai / anthropic / openai / bedrock) と、"
                    "選択したプロバイダに必要な API キー / エンドポイントを設定し、"
                    "Application を再起動してください。"
                ),
            },
        )

    store = get_store()
    sid = body.session_id or user_ctx.session_id
    session = store.get_or_create_session(sid, user_ctx.user_name)
    turn_id = f"turn_{uuid.uuid4().hex[:10]}"

    # LLM は毎リクエストで env 経由に再解決する (env 変更 → Application 再起動で反映)。
    # 未設定は上の 503 で弾いているのでここで None にはならない想定だが、
    # crewai / litellm のインストール失敗等でファクトリが None を返す可能性は残る。
    llm_light, llm_strong = build_llm_pair()

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
                llm_light=llm_light,
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
                    llm_light=llm_light,
                    llm_strong=llm_strong,
                    artifacts_created=artifacts_created,
                    response_md_parts=response_md_parts,
                    error_holder=error_holder,
                ):
                    yield chunk
            elif plan.child_crew == "analytics_summary":
                async for chunk in _handle_summary(
                    request=request,
                    user_ctx=user_ctx,
                    session_id=session.session_id,
                    turn_id=turn_id,
                    fq_table_name=str(plan.inputs.get("fq_table_name", "")),
                    question=str(plan.inputs.get("question") or body.prompt),
                    llm_light=llm_light,
                    llm_strong=llm_strong,
                    artifacts_created=artifacts_created,
                    response_md_parts=response_md_parts,
                    error_holder=error_holder,
                ):
                    yield chunk
            elif plan.child_crew == "analytics_dashboard":
                async for chunk in _handle_dashboard(
                    request=request,
                    user_ctx=user_ctx,
                    session_id=session.session_id,
                    turn_id=turn_id,
                    fq_table_name=str(plan.inputs.get("fq_table_name", "")),
                    question=str(plan.inputs.get("question") or body.prompt),
                    llm_light=llm_light,
                    llm_strong=llm_strong,
                    artifacts_created=artifacts_created,
                    response_md_parts=response_md_parts,
                    error_holder=error_holder,
                ):
                    yield chunk
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
    llm_light: Optional[Any] = None,
) -> RouterResult:
    """Router を実行し、target_schema の明示指定を plan に反映する。"""
    token = set_user_context(user_ctx)
    try:
        result = kickoff_router(
            user_ctx=user_ctx,
            prompt=prompt,
            session_id=session_id,
            entity_memory=entity_memory,
            llm_light=llm_light,
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
    llm_light: Optional[Any],
    llm_strong: Optional[Any],
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
                llm_light=llm_light,
                llm_strong=llm_strong,
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


# ------------------------------------------------------------------ #
# Analytics Summary path
# ------------------------------------------------------------------ #
async def _handle_summary(
    *,
    request: Request,
    user_ctx: UserContext,
    session_id: str,
    turn_id: str,
    fq_table_name: str,
    question: str,
    llm_light: Optional[Any],
    llm_strong: Optional[Any],
    artifacts_created: list[str],
    response_md_parts: list[str],
    error_holder: list[Optional[str]],
) -> AsyncIterator[dict]:
    """AnalyticsCrew (Summary) を走らせて SSE イベントを yield する。"""
    if not fq_table_name or fq_table_name.count(".") != 2:
        error_holder[0] = "ANALYTICS_MISSING_ARGS"
        yield sse_error(
            "ANALYTICS_MISSING_ARGS",
            "サマリー対象のテーブル (catalog.schema.table) が特定できませんでした。",
        )
        return

    yield sse_step(
        "AnalyticsSummaryCrew",
        "running",
        f"{fq_table_name} を調査し、サマリーを作成します...",
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
        token = set_user_context(scoped_ctx)
        try:
            return kickoff_analytics_summary(
                user_ctx=scoped_ctx,
                fq_table_name=fq_table_name,
                question=question,
                llm_light=llm_light,
                llm_strong=llm_strong,
            )
        finally:
            reset_user_context(token)

    task = asyncio.create_task(asyncio.to_thread(_run))

    # 実行中は 5 秒に 1 回進捗イベントを出しつつクライアント切断も監視する
    while not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
        except asyncio.TimeoutError:
            if await request.is_disconnected():
                task.cancel()
                return
            yield sse_step("AnalyticsSummaryCrew", "running", "Crew 実行中...")

    result = await task
    if result.get("status") != "ok":
        code = result.get("error_code", "ANALYTICS_SUMMARY_FAILED")
        msg = result.get("message", "AnalyticsSummary failed")
        error_holder[0] = code
        yield sse_error(code, msg)
        return

    report = (result.get("payload") or {}).get("report") or {}
    md = _format_summary_markdown(report, fq_table_name)

    # summary artifact を artifact ストアに登録 (ResultPane タブから開ける)
    store = get_store()
    art = store.register_artifact(
        "summary",
        ref={
            "fq": fq_table_name,
            "markdown": md,
            "observations": report.get("observations") or [],
            "warnings": report.get("warnings") or [],
            "sample_queries": report.get("sample_queries") or [],
        },
        session_id=session_id,
    )
    artifacts_created.append(art.artifact_id)

    # entity_memory を更新して次ターンの「そのサマリー」参照に備える
    store.update_entity_memory(
        session_id,
        {
            "last_table": fq_table_name,
            "last_summary_artifact_id": art.artifact_id,
        },
    )

    yield sse_artifact(art.artifact_id, "summary", ref={"fq": fq_table_name})
    response_md_parts.append(md)
    yield sse_token(md)
    yield sse_step("AnalyticsSummaryCrew", "done", "サマリーを作成しました。")


# ------------------------------------------------------------------ #
# Analytics Dashboard path
# ------------------------------------------------------------------ #
async def _handle_dashboard(
    *,
    request: Request,
    user_ctx: UserContext,
    session_id: str,
    turn_id: str,
    fq_table_name: str,
    question: str,
    llm_light: Optional[Any],
    llm_strong: Optional[Any],
    artifacts_created: list[str],
    response_md_parts: list[str],
    error_holder: list[Optional[str]],
) -> AsyncIterator[dict]:
    """AnalyticsCrew (Dashboard) を走らせて SSE イベントを yield する。"""
    if not fq_table_name or fq_table_name.count(".") != 2:
        error_holder[0] = "ANALYTICS_MISSING_ARGS"
        yield sse_error(
            "ANALYTICS_MISSING_ARGS",
            "ダッシュボード対象のテーブル (catalog.schema.table) が特定できませんでした。",
        )
        return

    yield sse_step(
        "AnalyticsDashboardCrew",
        "running",
        f"{fq_table_name} からダッシュボードを構築します...",
    )

    # UserContext を持ち込む session_id つきの派生を用意
    scoped_ctx = UserContext(
        user_name=user_ctx.user_name,
        groups=user_ctx.groups,
        knox_jwt=user_ctx.knox_jwt,
        aws_credentials=user_ctx.aws_credentials,
        session_id=session_id,
    )

    # crew.kickoff は同期 & LLM 呼び出し + CDV への複数 API 呼び出しで長い
    # → to_thread で退避
    def _run() -> dict:
        token = set_user_context(scoped_ctx)
        try:
            return kickoff_analytics_dashboard(
                user_ctx=scoped_ctx,
                fq_table_name=fq_table_name,
                question=question,
                llm_light=llm_light,
                llm_strong=llm_strong,
            )
        finally:
            reset_user_context(token)

    task = asyncio.create_task(asyncio.to_thread(_run))

    # 実行中は 5 秒に 1 回進捗イベントを出しつつクライアント切断も監視する
    while not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
        except asyncio.TimeoutError:
            if await request.is_disconnected():
                task.cancel()
                return
            yield sse_step("AnalyticsDashboardCrew", "running", "Crew 実行中...")

    result = await task
    if result.get("status") != "ok":
        code = result.get("error_code", "ANALYTICS_DASHBOARD_FAILED")
        msg = result.get("message", "AnalyticsDashboard failed")
        error_holder[0] = code
        yield sse_error(code, msg)
        return

    dashboard = (result.get("payload") or {}).get("dashboard") or {}
    if not dashboard or not dashboard.get("dashboard_url"):
        # LLM が最終 JSON を組めなかった / CDV 応答が不完全だった
        error_holder[0] = "ANALYTICS_DASHBOARD_FAILED"
        yield sse_error(
            "ANALYTICS_DASHBOARD_FAILED",
            "ダッシュボードは作成されませんでした (最終 JSON が不完全)。",
        )
        return

    md = _format_dashboard_markdown(dashboard, fq_table_name)

    # dashboard artifact を artifact ストアに登録 (ResultPane が iframe で開く)
    store = get_store()
    art = store.register_artifact(
        "dashboard",
        ref={
            "fq": fq_table_name,
            "dashboard_id": dashboard.get("dashboard_id"),
            "dashboard_url": dashboard.get("dashboard_url"),
            "title": dashboard.get("title"),
            "visual_ids": dashboard.get("visual_ids") or [],
            "dataset_id": dashboard.get("dataset_id"),
            "notes": dashboard.get("notes"),
        },
        session_id=session_id,
    )
    artifacts_created.append(art.artifact_id)

    # entity_memory を更新して次ターンの「そのダッシュボード」参照に備える
    store.update_entity_memory(
        session_id,
        {
            "last_table": fq_table_name,
            "last_dashboard_id": dashboard.get("dashboard_id"),
            "last_dashboard_url": dashboard.get("dashboard_url"),
        },
    )

    yield sse_artifact(
        art.artifact_id,
        "dashboard",
        ref={
            "fq": fq_table_name,
            "dashboard_url": dashboard.get("dashboard_url"),
        },
    )
    response_md_parts.append(md)
    yield sse_token(md)
    yield sse_step(
        "AnalyticsDashboardCrew", "done", "ダッシュボードを作成しました。"
    )


def _format_dashboard_markdown(dashboard: dict, fq_table_name: str) -> str:
    """BuildDashboardResult を ChatPane に流す Markdown にする。"""
    title = dashboard.get("title") or f"{fq_table_name} のダッシュボード"
    url = dashboard.get("dashboard_url") or ""
    visual_count = len(dashboard.get("visual_ids") or [])
    reused = dashboard.get("dataset_reused")

    lines = [
        f"**ダッシュボードを作成しました**: [{title}]({url})",
        f"- 元テーブル: `{fq_table_name}`",
        f"- Visual 数: {visual_count}",
    ]
    if reused is True:
        lines.append("- Dataset: 既存を再利用")
    elif reused is False:
        lines.append("- Dataset: 新規作成")
    notes = dashboard.get("notes")
    if notes:
        lines.append("")
        lines.append(f"_notes_: {notes}")
    return "\n".join(lines) + "\n"


def _format_summary_markdown(report: dict, fq_table_name: str) -> str:
    """SummaryReport を ChatPane に流す Markdown にする。

    ``summary_markdown`` は LLM が書き切っている前提だが、空だった場合の
    最低限フォールバックとして観察点だけでも並べる。
    """
    if not report:
        return f"`{fq_table_name}` のサマリー生成を試みましたが、結果が取得できませんでした。\n"

    md = str(report.get("summary_markdown") or "").strip()
    if md:
        # 先頭に見出しが無ければ付ける
        if not md.startswith("#"):
            md = f"## `{fq_table_name}` のサマリー\n\n{md}"
        parts = [md]
    else:
        parts = [f"## `{fq_table_name}` のサマリー", ""]
        for obs in report.get("observations") or []:
            headline = obs.get("headline") or ""
            if headline:
                parts.append(f"- {headline}")

    warnings = report.get("warnings") or []
    if warnings:
        parts.append("")
        parts.append("**注意点**:")
        for w in warnings:
            parts.append(f"- {w}")

    samples = report.get("sample_queries") or []
    if samples:
        parts.append("")
        parts.append("**次に見ると良さそうな SQL**:")
        for s in samples[:3]:
            q = s.get("question") or ""
            sql = s.get("sql") or ""
            if q and sql:
                parts.append(f"- {q}")
                parts.append(f"  ```sql\n  {sql}\n  ```")

    return "\n".join(parts).rstrip() + "\n"

"""Server-Sent Events (SSE) ヘルパ。

`/api/wish` は Crew の進捗を SSE で逐次配信する。イベント種別:

* ``event: step``      各 Agent / Task のステータス更新
* ``event: token``     LLM 応答 (もしくは最終メッセージ) の追記
* ``event: artifact``  ResultPane タブに開かせたい成果物 (table / dashboard 等)
* ``event: error``     ハンドリング可能なエラー (error_code + message)
* ``event: done``      完了 (`turn_id` と成果物 ID 一覧)

このモジュールは Pydantic 型と、それらを ``sse-starlette`` の
:class:`EventSourceResponse` が受け付ける ``{"event": ..., "data": ...}``
辞書に変換するヘルパを提供する。実際のストリーミングは :mod:`.routes.wish`
が行う。
"""
from __future__ import annotations

import json
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class WishStepEvent(BaseModel):
    agent: str = Field(..., description="現在実行中の Agent 名 (role slug)")
    status: Literal["running", "done", "skipped", "error"] = "running"
    message: str = Field(..., description="人間向けの一言 (日本語 OK)")


class WishTokenEvent(BaseModel):
    delta: str = Field(..., description="ChatPane に追記する文字列")


class WishArtifactEvent(BaseModel):
    id: str = Field(..., description="artifact_id")
    type: Literal[
        "table_preview",
        "dashboard",
        "summary",
        "sql",
        "file_preview",
    ]
    ref: dict[str, Any] = Field(
        default_factory=dict,
        description="タブが再取得に使う参照情報 (fq name / URL / path 等)",
    )


class WishErrorEvent(BaseModel):
    error_code: str
    message: str


class WishDoneEvent(BaseModel):
    turn_id: str
    artifacts: list[str] = Field(default_factory=list)
    ok: bool = True


def _envelope(event_name: str, payload: BaseModel) -> dict[str, str]:
    """sse-starlette の :class:`EventSourceResponse` が受け取る形に整える。

    ``data`` は 1 行 JSON にしておく (改行があると SSE のフレームが割れる)。
    """
    return {
        "event": event_name,
        "data": json.dumps(
            payload.model_dump(exclude_none=True),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }


def sse_step(agent: str, status: str = "running", message: str = "") -> dict[str, str]:
    return _envelope(
        "step", WishStepEvent(agent=agent, status=status, message=message)
    )


def sse_token(delta: str) -> dict[str, str]:
    return _envelope("token", WishTokenEvent(delta=delta))


def sse_artifact(
    artifact_id: str, artifact_type: str, ref: Optional[dict[str, Any]] = None
) -> dict[str, str]:
    return _envelope(
        "artifact",
        WishArtifactEvent(id=artifact_id, type=artifact_type, ref=ref or {}),
    )


def sse_error(error_code: str, message: str) -> dict[str, str]:
    return _envelope("error", WishErrorEvent(error_code=error_code, message=message))


def sse_done(
    turn_id: str, artifacts: list[str], ok: bool = True
) -> dict[str, str]:
    return _envelope(
        "done", WishDoneEvent(turn_id=turn_id, artifacts=artifacts, ok=ok)
    )

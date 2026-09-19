"""会話セッション API (`/api/sessions/*`)。

ブラウザリロード時に会話履歴 + entity_memory を復元するために使う。
インメモリ実装なのでプロセス再起動で全て消える (デモ想定)。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from thor.api.state import get_store

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
    sess = get_store().get_session(session_id)
    if sess is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "SESSION_NOT_FOUND",
                "message": f"No session with id={session_id}",
            },
        )
    return {
        "session_id": sess.session_id,
        "user_name": sess.user_name,
        "created_at": sess.created_at,
        "turns": [
            {
                "turn_id": t.turn_id,
                "prompt": t.prompt,
                "response_markdown": t.response_markdown,
                "artifact_ids": t.artifact_ids,
                "error_code": t.error_code,
                "created_at": t.created_at,
            }
            for t in sess.turns
        ],
        "entity_memory": sess.entity_memory,
    }

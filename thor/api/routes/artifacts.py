"""ResultPane タブの成果物 API (`/api/artifacts/*`)。

Wish の SSE で ``event: artifact`` として通知した ``artifact_id`` を、UI が
中央ペインで再表示するときに叩く。実際のコンテンツ (行データ / iframe URL /
markdown) は artifact.type ごとに ``ref`` の中身が異なる。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from thor.api.state import get_store

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


@router.get("/{artifact_id}")
def get_artifact(artifact_id: str) -> dict[str, Any]:
    art = get_store().get_artifact(artifact_id)
    if art is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "ARTIFACT_NOT_FOUND",
                "message": f"No artifact with id={artifact_id}",
            },
        )
    return {
        "id": art.artifact_id,
        "type": art.type,
        "ref": art.ref,
        "session_id": art.session_id,
        "created_at": art.created_at,
    }

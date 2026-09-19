"""ヘルスチェック。Workbench Application の readiness にも使う。"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "thor.api"}

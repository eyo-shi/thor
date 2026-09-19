"""Ossie YAML の raw 参照 API (`/api/ossie/*`)。

Ingestion Crew が書き出した semantic/datasets/<catalog>/<schema>/<table>.yaml を
そのまま返す。中央ペインの「SQL」タブ横に「Ossie を見る」を出す用途。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from thor.semantic.store import read_dataset

router = APIRouter(prefix="/api/ossie", tags=["ossie"])


@router.get("/{fq}")
def get_ossie(fq: str) -> dict[str, Any]:
    """``fq`` は ``catalog.schema.table`` (例: ``iceberg.demo.sales_2024``)。"""
    parts = fq.split(".")
    if len(parts) != 3:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "BAD_REQUEST",
                "message": "fq must be 'catalog.schema.table'",
            },
        )
    result = read_dataset(fq)
    if result.get("status") == "error":
        code = result.get("error_code", "OSSIE_NOT_FOUND")
        status = 404 if code == "OSSIE_NOT_FOUND" else 502
        raise HTTPException(status_code=status, detail=result)
    return result

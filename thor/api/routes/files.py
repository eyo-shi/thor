"""S3 ブラウザ API (`/api/files/*`)。

TreePane の左ペイン `s3` ルートに対応。IDBroker で発行されたエンドユーザー
STS 資格情報で S3 を叩く。

エンドポイント:

* ``GET /api/files/list``     — 指定 prefix 下の一覧 (フォルダ + オブジェクト)

**未実装**: ``/api/files/preview`` (Excel / CSV / JSON / Parquet プレビュー) は
Ingestion 側の Tool と共有するロジックを別途切り出してから追加する。
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from thor.api.auth import require_user_context
from thor.transport.user_context import UserContext
from thor.tools._s3_client import map_s3_error, s3_client_for_user

router = APIRouter(prefix="/api/files", tags=["files"])


@router.get("/list")
def list_objects(
    user_ctx: Annotated[UserContext, Depends(require_user_context)],
    bucket: Annotated[str, Query(min_length=1, max_length=256)],
    prefix: Annotated[str, Query(max_length=1024)] = "",
    delimiter: Annotated[str, Query(max_length=4)] = "/",
    max_keys: Annotated[int, Query(ge=1, le=10000)] = 1000,
) -> dict[str, Any]:
    client = s3_client_for_user(user_ctx)
    if isinstance(client, dict):
        raise HTTPException(status_code=502, detail=client)
    try:
        resp = client.list_objects_v2(
            Bucket=bucket,
            Prefix=prefix,
            Delimiter=delimiter,
            MaxKeys=max_keys,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=map_s3_error(e, bucket, prefix)) from e

    objects = [
        {
            "key": o["Key"],
            "size": o["Size"],
            "last_modified": o["LastModified"].isoformat()
            if hasattr(o.get("LastModified"), "isoformat")
            else str(o.get("LastModified", "")),
        }
        for o in resp.get("Contents", [])
    ]
    subfolders = [p["Prefix"] for p in resp.get("CommonPrefixes", [])]
    return {
        "bucket": bucket,
        "prefix": prefix,
        "delimiter": delimiter,
        "objects": objects,
        "subfolders": subfolders,
        "is_truncated": bool(resp.get("IsTruncated")),
    }

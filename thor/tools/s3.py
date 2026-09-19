"""S3 アクセス Tool。

IDBroker で発行された STS 短期資格情報を使い、エンドユーザー権限で S3 を叩く。
全 Tool は boto3 の :class:`ClientError` を Thor エラーコードに写像する
(``S3_NOT_FOUND`` / ``S3_ACCESS_DENIED`` / ``S3_RANGE_FAILED``)。

* :class:`S3ListTool` / :class:`S3HeadTool` は Agent 直接呼び出し用
* :class:`S3GetRangeTool` はフォーマット判定 Tool から使う想定 (Content は base64)
"""
from __future__ import annotations

import base64
from typing import Any, Optional

from pydantic import BaseModel, Field

from thor.transport.errors import ok
from thor.transport.logging import get_logger
from thor.transport.tool_base import BaseThorTool
from thor.transport.user_context import UserContext
from thor.tools._s3_client import map_s3_error, s3_client_for_user

_logger = get_logger(__name__)


# ------------------------------------------------------------------ #
# S3ListTool
# ------------------------------------------------------------------ #
class S3ListArgs(BaseModel):
    bucket: str = Field(..., description="S3 バケット名")
    prefix: str = Field("", description="キーの prefix (フォルダ相当)")
    delimiter: str = Field(
        "/", description="prefix delimiter (階層列挙する場合は '/')"
    )
    max_keys: int = Field(
        1000, ge=1, le=10000, description="1 コールで返す最大件数"
    )


class S3ListTool(BaseThorTool):
    """指定 bucket / prefix 配下のオブジェクトとサブフォルダを列挙する。"""

    name: str = "s3_list"
    description: str = (
        "List objects and sub-prefixes under an S3 bucket/prefix. "
        "Use delimiter='/' to walk the tree one level at a time."
    )
    args_schema: type[BaseModel] = S3ListArgs

    def run(
        self,
        user_ctx: Optional[UserContext],
        bucket: str,
        prefix: str = "",
        delimiter: str = "/",
        max_keys: int = 1000,
        **_: Any,
    ) -> dict[str, Any]:
        client = s3_client_for_user(user_ctx)
        if isinstance(client, dict):
            return client
        try:
            resp = client.list_objects_v2(
                Bucket=bucket,
                Prefix=prefix,
                Delimiter=delimiter,
                MaxKeys=max_keys,
            )
        except Exception as e:  # noqa: BLE001
            return map_s3_error(e, bucket, prefix)
        objects = [
            {
                "key": o["Key"],
                "size": o["Size"],
                "last_modified": o["LastModified"].isoformat(),
            }
            for o in resp.get("Contents", [])
        ]
        subfolders = [p["Prefix"] for p in resp.get("CommonPrefixes", [])]
        return ok(
            {
                "bucket": bucket,
                "prefix": prefix,
                "objects": objects,
                "subfolders": subfolders,
                "truncated": resp.get("IsTruncated", False),
            }
        )


# ------------------------------------------------------------------ #
# S3HeadTool
# ------------------------------------------------------------------ #
class S3HeadArgs(BaseModel):
    bucket: str = Field(..., description="S3 バケット名")
    key: str = Field(..., description="オブジェクトキー")


class S3HeadTool(BaseThorTool):
    """S3 オブジェクトのメタ情報 (サイズ / ContentType / ETag) を返す。"""

    name: str = "s3_head"
    description: str = (
        "Get metadata (size, content-type, etag, last-modified) for a single "
        "S3 object without downloading the body."
    )
    args_schema: type[BaseModel] = S3HeadArgs

    def run(
        self,
        user_ctx: Optional[UserContext],
        bucket: str,
        key: str,
        **_: Any,
    ) -> dict[str, Any]:
        client = s3_client_for_user(user_ctx)
        if isinstance(client, dict):
            return client
        try:
            resp = client.head_object(Bucket=bucket, Key=key)
        except Exception as e:  # noqa: BLE001
            return map_s3_error(e, bucket, key)
        return ok(
            {
                "bucket": bucket,
                "key": key,
                "size": resp["ContentLength"],
                "content_type": resp.get("ContentType", "application/octet-stream"),
                "etag": resp.get("ETag", "").strip('"'),
                "last_modified": resp["LastModified"].isoformat(),
            }
        )


# ------------------------------------------------------------------ #
# S3GetRangeTool
# ------------------------------------------------------------------ #
class S3GetRangeArgs(BaseModel):
    bucket: str = Field(..., description="S3 バケット名")
    key: str = Field(..., description="オブジェクトキー")
    start: int = Field(0, ge=0, description="開始オフセット (バイト)")
    length: int = Field(
        1_048_576,
        ge=1,
        le=10_485_760,
        description="取得バイト数 (最大 10 MiB)",
    )


class S3GetRangeTool(BaseThorTool):
    """S3 オブジェクトの Range GET。フォーマット判定用に先頭 1 MiB を取る想定。

    レスポンスの ``content_b64`` は base64 エンコード済み。LLM プロンプトに
    そのまま流すと壊れるので、これは Agent が別 Tool (MagicByteTool 等) の
    context として渡すルートで使う。
    """

    name: str = "s3_get_range"
    description: str = (
        "Fetch a byte range of an S3 object and return it as base64. "
        "Intended for format sniffing (first ~1MB); do not use for full downloads."
    )
    args_schema: type[BaseModel] = S3GetRangeArgs

    def run(
        self,
        user_ctx: Optional[UserContext],
        bucket: str,
        key: str,
        start: int = 0,
        length: int = 1_048_576,
        **_: Any,
    ) -> dict[str, Any]:
        client = s3_client_for_user(user_ctx)
        if isinstance(client, dict):
            return client
        end = start + length - 1
        try:
            resp = client.get_object(
                Bucket=bucket, Key=key, Range=f"bytes={start}-{end}"
            )
            body: bytes = resp["Body"].read()
        except Exception as e:  # noqa: BLE001
            return map_s3_error(e, bucket, key)
        return ok(
            {
                "bucket": bucket,
                "key": key,
                "start": start,
                "length": len(body),
                "content_b64": base64.b64encode(body).decode("ascii"),
            }
        )

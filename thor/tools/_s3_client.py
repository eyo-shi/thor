"""boto3 S3 client を UserContext から組み立てるヘルパ。

Tool 実装 (:mod:`thor.tools.s3` など) が薄く共通利用する。

認証情報は毎回 IDBroker STS 経由で ``UserContext`` から取得する
(:func:`thor.tools._idbroker.get_or_fetch_credentials`)。region や
endpoint_url は :mod:`thor.transport.config` の :func:`get_s3_config` から
取得する (Data Connection 由来 or env fallback)。
"""
from __future__ import annotations

from typing import Any, Union
from urllib.parse import urlparse

from thor.transport.config import get_s3_config
from thor.transport.errors import ErrorCode, err
from thor.transport.user_context import AwsCredentials, UserContext
from thor.tools._idbroker import get_or_fetch_credentials

try:  # boto3 は本番依存。テストではモックする。
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:  # pragma: no cover
    boto3 = None  # type: ignore[assignment]

    class BotoCoreError(Exception):
        pass

    class ClientError(Exception):
        def __init__(self, response: dict, operation_name: str = "") -> None:
            super().__init__(str(response))
            self.response = response
            self.operation_name = operation_name


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """``s3://bucket/prefix/key`` を ``(bucket, key)`` に分解する。"""
    if not uri.startswith("s3://"):
        raise ValueError(f"Not an s3:// URI: {uri}")
    parsed = urlparse(uri)
    return parsed.netloc, parsed.path.lstrip("/")


def s3_client_for_user(user_ctx: UserContext) -> Union[Any, dict[str, Any]]:
    """ユーザー権限で boto3 S3 client を返す。失敗時は err() dict。

    region / endpoint_url は :func:`get_s3_config` の解決結果を使う
    (Data Connection or env or default ``us-east-1``)。
    認証情報は IDBroker STS 経由で毎リクエスト取得する。
    """
    if boto3 is None:
        return err(ErrorCode.S3_ASSUMEROLE_FAILED, "boto3 is not installed")
    creds = get_or_fetch_credentials(user_ctx)
    if isinstance(creds, dict):
        return creds
    cfg = get_s3_config()
    # get_s3_config は必ず S3Config を返す設計 (region の default があるため
    # None にはならない)。ただし将来 None を返すようになった場合の safeguard。
    region = cfg.region if cfg else "us-east-1"
    endpoint_url = cfg.endpoint_url if cfg else None
    kwargs: dict[str, Any] = dict(
        aws_access_key_id=creds.access_key_id,
        aws_secret_access_key=creds.secret_access_key,
        aws_session_token=creds.session_token,
        region_name=region,
    )
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    return boto3.client("s3", **kwargs)


def map_s3_error(e: Exception, bucket: str, key: str) -> dict[str, Any]:
    """boto3 の例外を ThorErrorResult に写像する。"""
    if isinstance(e, ClientError):
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "NoSuchBucket", "404"):
            return err(ErrorCode.S3_NOT_FOUND, f"S3 not found: s3://{bucket}/{key}")
        if code in ("AccessDenied", "403"):
            return err(
                ErrorCode.S3_ACCESS_DENIED,
                f"S3 access denied: s3://{bucket}/{key}",
            )
        if code in ("InvalidRange", "416"):
            return err(
                ErrorCode.S3_RANGE_FAILED,
                f"Invalid range on s3://{bucket}/{key}",
            )
        return err(
            ErrorCode.S3_ASSUMEROLE_FAILED,
            f"S3 client error {code}: {e}",
        )
    if isinstance(e, BotoCoreError):
        return err(ErrorCode.S3_ASSUMEROLE_FAILED, f"boto3 error: {e}")
    return err(ErrorCode.S3_ASSUMEROLE_FAILED, f"Unexpected S3 error: {e}")

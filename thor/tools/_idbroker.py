"""Knox JWT を IDBroker CAB エンドポイントで AWS STS 短期資格情報と交換する。

Cloudera IDBroker は Knox JWT を受け取り、ユーザー / グループごとに mapping
された IAM ロールで AssumeRole を実施し、短期の (AccessKeyId, SecretAccessKey,
SessionToken, Expiration) を返す。この短期資格情報を UserContext スコープで
使い回すことで **エンドユーザー権限** の S3 アクセスを実現する。

環境変数:
  THOR_IDBROKER_URL - IDBroker のベース URL (例: https://knox.example.com:8443/gateway)

セキュリティ:
  * fetched credentials は module-level dict にキャッシュされるが、キーは
    ``UserContext.request_id`` (UUID hex 32 桁) なので他リクエストと混同しない
  * expiration まで 60 秒を切ったら再取得
  * 資格情報の値は絶対にログ・LLM プロンプトへ流さない (redact filter 前提)
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any, Union

import httpx

from thor.transport.errors import ErrorCode, err
from thor.transport.logging import get_logger
from thor.transport.user_context import AwsCredentials, UserContext

_logger = get_logger(__name__)

_IDBROKER_TIMEOUT_S = 15.0
_CAB_PATH = "/aws-cab/cab/api/v1/credentials"
_REFRESH_MARGIN_S = 60  # 期限まで 60 秒未満なら再取得

# request_id -> AwsCredentials のキャッシュ (module-level)
# 1 リクエスト内で複数の S3 Tool 呼び出しが発生しても IDBroker を叩くのは 1 回
_creds_cache: dict[str, AwsCredentials] = {}


def clear_credentials(request_id: str) -> None:
    """FastAPI middleware がリクエスト終了時に呼ぶ。メモリリークの予防。"""
    _creds_cache.pop(request_id, None)


def _gc_expired(now: float) -> None:
    for rid in list(_creds_cache):
        if _creds_cache[rid].expiration_epoch <= now:
            del _creds_cache[rid]


def _parse_expiration(exp: Any) -> int:
    """IDBroker が返す Expiration を epoch 秒に変換する。

    IDBroker のバージョンにより epoch (int) だったり ISO8601 文字列だったりする。
    """
    if isinstance(exp, (int, float)):
        return int(exp)
    if isinstance(exp, str):
        # "2026-09-19T12:34:56Z" 形式
        return int(datetime.fromisoformat(exp.replace("Z", "+00:00")).timestamp())
    # 不明ならデフォ 30 分
    return int(time.time()) + 1800


def fetch_aws_credentials(user_ctx: UserContext) -> Union[AwsCredentials, dict[str, Any]]:
    """Knox JWT を IDBroker に渡して STS credentials を取得する。

    成功時は :class:`AwsCredentials`、失敗時は ``{"status": "error", ...}`` dict。
    """
    idb_url = os.environ.get("THOR_IDBROKER_URL")
    if not idb_url:
        return err(
            ErrorCode.S3_ASSUMEROLE_FAILED,
            "THOR_IDBROKER_URL is not configured; cannot obtain S3 credentials.",
        )
    if not user_ctx.knox_jwt:
        return err(
            ErrorCode.AUTH_MISSING,
            "Knox JWT is required to exchange for AWS credentials.",
        )

    endpoint = f"{idb_url.rstrip('/')}{_CAB_PATH}"
    headers = {
        "Authorization": f"Bearer {user_ctx.knox_jwt}",
        "Accept": "application/json",
    }
    try:
        with httpx.Client(timeout=_IDBROKER_TIMEOUT_S) as client:
            resp = client.get(endpoint, headers=headers)
    except httpx.TimeoutException:
        _logger.warning("idbroker.timeout", user=user_ctx.user_name)
        return err(ErrorCode.S3_ASSUMEROLE_FAILED, "IDBroker request timed out.")
    except httpx.HTTPError as e:
        _logger.warning("idbroker.error", error=str(e), user=user_ctx.user_name)
        return err(ErrorCode.S3_ASSUMEROLE_FAILED, f"IDBroker call failed: {e}")

    if resp.status_code in (401, 403):
        _logger.warning(
            "idbroker.access_denied",
            status=resp.status_code,
            user=user_ctx.user_name,
        )
        return err(
            ErrorCode.S3_ACCESS_DENIED,
            f"IDBroker rejected Knox JWT with HTTP {resp.status_code}.",
            status_code=resp.status_code,
        )
    if resp.status_code >= 400:
        _logger.warning(
            "idbroker.status",
            status=resp.status_code,
            user=user_ctx.user_name,
        )
        return err(
            ErrorCode.S3_ASSUMEROLE_FAILED,
            f"IDBroker returned HTTP {resp.status_code}.",
            status_code=resp.status_code,
        )
    try:
        data = resp.json()
    except ValueError:
        return err(
            ErrorCode.S3_ASSUMEROLE_FAILED,
            "IDBroker returned non-JSON body.",
        )
    try:
        creds = AwsCredentials(
            access_key_id=data["AccessKeyId"],
            secret_access_key=data["SecretAccessKey"],
            session_token=data["SessionToken"],
            expiration_epoch=_parse_expiration(data.get("Expiration")),
        )
    except (KeyError, TypeError) as e:
        return err(
            ErrorCode.S3_ASSUMEROLE_FAILED,
            f"IDBroker response malformed: missing/invalid {e}",
        )
    _logger.info(
        "idbroker.ok",
        user=user_ctx.user_name,
        expires_in_s=creds.expiration_epoch - int(time.time()),
    )
    return creds


def get_or_fetch_credentials(
    user_ctx: UserContext,
) -> Union[AwsCredentials, dict[str, Any]]:
    """キャッシュにあればそれ、なければ IDBroker を叩いて格納する。"""
    now = time.time()
    _gc_expired(now)
    cached = _creds_cache.get(user_ctx.request_id)
    if cached and cached.expiration_epoch - now > _REFRESH_MARGIN_S:
        return cached
    result = fetch_aws_credentials(user_ctx)
    if isinstance(result, AwsCredentials):
        _creds_cache[user_ctx.request_id] = result
    return result

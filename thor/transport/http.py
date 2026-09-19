"""HTTP クライアント (httpx ベース + retry + 構造化ログ)。

Trino / CDV / IDBroker などの外部エンドポイントに Knox JWT を付けて呼び出す
ための共通クライアント。指数バックオフの retry と、5xx / タイムアウトを
標準エラーコードに正規化して返す。
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

import httpx

from thor.transport.auth import bearer_header
from thor.transport.errors import ErrorCode, err
from thor.transport.logging import get_logger
from thor.transport.user_context import UserContext, get_user_context

_logger = get_logger(__name__)

# 一時的な障害と見なして retry する状態コード
_RETRY_STATUS = frozenset({502, 503, 504})
_DEFAULT_TIMEOUT_S = 30.0
_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_BACKOFF_S = 0.5


def _backoff(attempt: int) -> float:
    return _DEFAULT_BACKOFF_S * (2 ** (attempt - 1))


@contextmanager
def _timed(op: str, **kv: Any) -> Iterator[dict[str, Any]]:
    start = time.perf_counter()
    span: dict[str, Any] = {"op": op, **kv}
    try:
        yield span
    finally:
        span["latency_ms"] = round((time.perf_counter() - start) * 1000, 1)


class ThorHttpClient:
    """1 リクエストスコープで使う HTTP クライアント。

    Trino / CDV など特定エンドポイント用のサブクラスから利用することを想定。
    """

    def __init__(
        self,
        base_url: str = "",
        timeout: float = _DEFAULT_TIMEOUT_S,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
        verify: bool | str = True,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0)),
            verify=verify,
            follow_redirects=True,
        )

    # --- context manager --- #
    def __enter__(self) -> "ThorHttpClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # --- request core --- #
    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Optional[Any] = None,
        headers: Optional[dict[str, str]] = None,
        auth_from_context: bool = True,
        expect_json: bool = True,
    ) -> dict[str, Any]:
        url = path if path.startswith("http") else f"{self._base_url}{path}"
        merged_headers: dict[str, str] = {"Accept": "application/json"}
        if auth_from_context:
            ctx: UserContext = get_user_context()
            merged_headers.update(bearer_header(ctx))
            merged_headers["X-Thor-User"] = ctx.user_name
            merged_headers["X-Thor-Request-Id"] = ctx.request_id
        if headers:
            merged_headers.update(headers)

        last_exc: Optional[Exception] = None
        for attempt in range(1, self._max_attempts + 1):
            with _timed("http_request", method=method, url=url, attempt=attempt) as span:
                try:
                    resp = self._client.request(
                        method,
                        url,
                        params=params,
                        json=json,
                        headers=merged_headers,
                    )
                except httpx.TimeoutException as e:
                    last_exc = e
                    span["outcome"] = "timeout"
                    _logger.warning("http.timeout", **span)
                    if attempt >= self._max_attempts:
                        return err(ErrorCode.HTTP_TIMEOUT, f"HTTP timeout after {attempt} attempts: {url}")
                    time.sleep(_backoff(attempt))
                    continue
                except httpx.HTTPError as e:
                    last_exc = e
                    span["outcome"] = "http_error"
                    _logger.warning("http.error", error=str(e), **span)
                    if attempt >= self._max_attempts:
                        return err(ErrorCode.HTTP_UNAVAILABLE, f"HTTP error: {e}")
                    time.sleep(_backoff(attempt))
                    continue

                span["status_code"] = resp.status_code
                if resp.status_code in _RETRY_STATUS and attempt < self._max_attempts:
                    _logger.warning("http.retry", **span)
                    time.sleep(_backoff(attempt))
                    continue

                if resp.status_code >= 400:
                    span["outcome"] = "status_error"
                    _logger.warning("http.status", body=_safe_snippet(resp.text), **span)
                    code = _map_status_to_error_code(resp.status_code)
                    return err(
                        code,
                        f"HTTP {resp.status_code} from {url}",
                        status_code=resp.status_code,
                        body=_safe_snippet(resp.text),
                    )

                _logger.info("http.ok", **span)
                if not expect_json:
                    return {"status": "ok", "text": resp.text}
                try:
                    return {"status": "ok", "data": resp.json()}
                except ValueError:
                    return {"status": "ok", "text": resp.text}

        # ここに到達するのは全 attempt が retry 対象で終わった場合のみ
        return err(
            ErrorCode.HTTP_UNAVAILABLE,
            f"Exhausted retries for {url}: {last_exc}",
        )

    # --- convenience --- #
    def get(self, path: str, **kw: Any) -> dict[str, Any]:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> dict[str, Any]:
        return self.request("POST", path, **kw)

    def put(self, path: str, **kw: Any) -> dict[str, Any]:
        return self.request("PUT", path, **kw)

    def delete(self, path: str, **kw: Any) -> dict[str, Any]:
        return self.request("DELETE", path, **kw)


def _map_status_to_error_code(status: int) -> str:
    if status in (401, 403):
        return ErrorCode.PERM_UNKNOWN
    if status == 404:
        return ErrorCode.HTTP_UNAVAILABLE
    return ErrorCode.HTTP_UNAVAILABLE


def _safe_snippet(text: str, limit: int = 500) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "..."

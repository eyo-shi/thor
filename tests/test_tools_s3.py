"""S3 Tool のテスト (boto3 client をモック化)。

実際の S3 は叩かず、boto3 の :class:`ClientError` から Thor エラーコードへの
写像だけを検証する。IDBroker との交換は :mod:`thor.tools._idbroker` の別テスト
(現状は integration テスト扱い) で確認する。
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from unittest import mock

import pytest

from thor.tools import _s3_client
from thor.tools.s3 import S3GetRangeTool, S3HeadTool, S3ListTool
from thor.transport.user_context import (
    AwsCredentials,
    UserContext,
    reset_user_context,
    set_user_context,
)


@pytest.fixture
def user_ctx() -> UserContext:
    return UserContext(
        user_name="alice",
        knox_jwt="fake-jwt",
        aws_credentials=AwsCredentials(
            access_key_id="AKIA",
            secret_access_key="secret",
            session_token="token",
            expiration_epoch=9999999999,
        ),
    )


@pytest.fixture
def auth_ctx(user_ctx: UserContext):
    token = set_user_context(user_ctx)
    yield user_ctx
    reset_user_context(token)


class TestS3ListTool:
    def test_success(self, auth_ctx: UserContext) -> None:
        fake_client = mock.MagicMock()
        fake_client.list_objects_v2.return_value = {
            "Contents": [
                {
                    "Key": "data/a.csv",
                    "Size": 1024,
                    "LastModified": datetime(2024, 1, 1, tzinfo=timezone.utc),
                },
                {
                    "Key": "data/b.csv",
                    "Size": 2048,
                    "LastModified": datetime(2024, 1, 2, tzinfo=timezone.utc),
                },
            ],
            "CommonPrefixes": [{"Prefix": "data/subdir/"}],
            "IsTruncated": False,
        }
        with mock.patch.object(_s3_client, "s3_client_for_user", return_value=fake_client):
            result = S3ListTool()._run(bucket="demo", prefix="data/", delimiter="/")
        assert result["status"] == "ok"
        assert len(result["objects"]) == 2
        assert result["objects"][0]["key"] == "data/a.csv"
        assert result["objects"][0]["size"] == 1024
        assert result["subfolders"] == ["data/subdir/"]
        assert result["truncated"] is False

    def test_access_denied(self, auth_ctx: UserContext) -> None:
        from thor.tools._s3_client import ClientError

        fake_client = mock.MagicMock()
        fake_client.list_objects_v2.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Access denied"}},
            "ListObjectsV2",
        )
        with mock.patch.object(_s3_client, "s3_client_for_user", return_value=fake_client):
            result = S3ListTool()._run(bucket="secret", prefix="")
        assert result["status"] == "error"
        assert result["error_code"] == "S3_ACCESS_DENIED"

    def test_missing_auth(self) -> None:
        # UserContext 未セット
        result = S3ListTool()._run(bucket="x", prefix="")
        assert result["status"] == "error"
        assert result["error_code"] == "AUTH_MISSING"


class TestS3HeadTool:
    def test_success(self, auth_ctx: UserContext) -> None:
        fake_client = mock.MagicMock()
        fake_client.head_object.return_value = {
            "ContentLength": 4096,
            "ContentType": "application/vnd.ms-excel",
            "ETag": '"abc123"',
            "LastModified": datetime(2024, 3, 15, tzinfo=timezone.utc),
        }
        with mock.patch.object(_s3_client, "s3_client_for_user", return_value=fake_client):
            result = S3HeadTool()._run(bucket="demo", key="file.xlsx")
        assert result["status"] == "ok"
        assert result["size"] == 4096
        assert result["content_type"] == "application/vnd.ms-excel"
        assert result["etag"] == "abc123"  # クォート除去済み

    def test_not_found(self, auth_ctx: UserContext) -> None:
        from thor.tools._s3_client import ClientError

        fake_client = mock.MagicMock()
        fake_client.head_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Key not found"}},
            "HeadObject",
        )
        with mock.patch.object(_s3_client, "s3_client_for_user", return_value=fake_client):
            result = S3HeadTool()._run(bucket="demo", key="missing.csv")
        assert result["status"] == "error"
        assert result["error_code"] == "S3_NOT_FOUND"


class TestS3GetRangeTool:
    def test_success_returns_base64(self, auth_ctx: UserContext) -> None:
        fake_body = mock.MagicMock()
        fake_body.read.return_value = b"PK\x03\x04hello"
        fake_client = mock.MagicMock()
        fake_client.get_object.return_value = {"Body": fake_body}
        with mock.patch.object(_s3_client, "s3_client_for_user", return_value=fake_client):
            result = S3GetRangeTool()._run(
                bucket="demo", key="file.xlsx", start=0, length=1024
            )
        assert result["status"] == "ok"
        assert result["length"] == len(b"PK\x03\x04hello")
        decoded = base64.b64decode(result["content_b64"])
        assert decoded == b"PK\x03\x04hello"
        # Range が正しく組み立てられているか
        fake_client.get_object.assert_called_once()
        kwargs = fake_client.get_object.call_args.kwargs
        assert kwargs["Range"] == "bytes=0-1023"

    def test_invalid_range(self, auth_ctx: UserContext) -> None:
        from thor.tools._s3_client import ClientError

        fake_client = mock.MagicMock()
        fake_client.get_object.side_effect = ClientError(
            {"Error": {"Code": "InvalidRange", "Message": "range not satisfiable"}},
            "GetObject",
        )
        with mock.patch.object(_s3_client, "s3_client_for_user", return_value=fake_client):
            result = S3GetRangeTool()._run(bucket="demo", key="tiny.txt", start=99999, length=1024)
        assert result["status"] == "error"
        assert result["error_code"] == "S3_RANGE_FAILED"


class TestParseS3Uri:
    def test_basic(self) -> None:
        bucket, key = _s3_client.parse_s3_uri("s3://demo-bucket/2024/data.csv")
        assert bucket == "demo-bucket"
        assert key == "2024/data.csv"

    def test_no_key(self) -> None:
        bucket, key = _s3_client.parse_s3_uri("s3://demo-bucket/")
        assert bucket == "demo-bucket"
        assert key == ""

    def test_non_s3_scheme_raises(self) -> None:
        with pytest.raises(ValueError):
            _s3_client.parse_s3_uri("https://example.com/foo")

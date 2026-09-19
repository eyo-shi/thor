"""thor.transport 単体テスト。

Knox JWT の抽出・redaction・UserContext / ContextVar / エラー形の契約を検証。
"""
from __future__ import annotations

import base64
import io
import json
import logging

import pytest

from thor.transport import (
    ErrorCode,
    UserContext,
    bearer_header,
    build_user_context_from_headers,
    configure_logging,
    err,
    extract_jwt,
    get_logger,
    get_user_context,
    get_user_context_optional,
    ok,
    reset_user_context,
    set_user_context,
)
from thor.transport.auth import _decode_jwt_payload_unverified


# ---------- helpers ----------

def _fake_jwt(payload: dict) -> str:
    """テスト用 JWT (署名部は無効、ペイロードだけ有効)。"""
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    sig = base64.urlsafe_b64encode(b"sig").rstrip(b"=").decode()
    return f"{header}.{body}.{sig}"


# ---------- UserContext ----------

class TestUserContext:
    def test_default_request_id_is_unique(self):
        a = UserContext(user_name="alice")
        b = UserContext(user_name="alice")
        assert a.request_id != b.request_id
        assert len(a.request_id) == 32

    def test_llm_safe_dict_excludes_credentials(self):
        ctx = UserContext(
            user_name="alice",
            groups=("data-eng", "admin"),
            knox_jwt="secret.jwt.token",
            session_id="s1",
        )
        d = ctx.to_llm_safe_dict()
        assert d == {
            "user_name": "alice",
            "groups": ["data-eng", "admin"],
            "session_id": "s1",
            "request_id": ctx.request_id,
        }
        assert "knox_jwt" not in d
        assert "aws_credentials" not in d

    def test_get_user_context_raises_when_unset(self):
        # 別 ContextVar スコープを保証するため reset
        assert get_user_context_optional() is None
        with pytest.raises(RuntimeError, match="UserContext is not set"):
            get_user_context()

    def test_set_and_reset_user_context(self):
        ctx = UserContext(user_name="bob")
        token = set_user_context(ctx)
        try:
            assert get_user_context() is ctx
            assert get_user_context_optional() is ctx
        finally:
            reset_user_context(token)
        assert get_user_context_optional() is None


# ---------- auth ----------

class TestExtractJwt:
    def test_bearer_authorization(self):
        assert extract_jwt({"Authorization": "Bearer abc.def.ghi"}) == "abc.def.ghi"

    def test_lowercase_header(self):
        assert extract_jwt({"authorization": "Bearer abc"}) == "abc"

    def test_authorization_without_bearer_prefix_is_ignored(self):
        # Bearer 以外の authorization スキームは取り出さない
        assert extract_jwt({"Authorization": "Basic abc"}) is None

    def test_x_knox_jwt(self):
        assert extract_jwt({"X-Knox-JWT": "raw.token.here"}) == "raw.token.here"

    def test_x_forwarded_access_token(self):
        assert extract_jwt({"x-forwarded-access-token": "tok"}) == "tok"

    def test_no_header_returns_none(self):
        assert extract_jwt({}) is None
        assert extract_jwt({"Content-Type": "application/json"}) is None


class TestDecodeJwt:
    def test_decode_valid_payload(self):
        token = _fake_jwt({"sub": "alice", "groups": ["data-eng"]})
        payload = _decode_jwt_payload_unverified(token)
        assert payload == {"sub": "alice", "groups": ["data-eng"]}

    def test_decode_garbage_returns_empty(self):
        assert _decode_jwt_payload_unverified("not-a-jwt") == {}
        assert _decode_jwt_payload_unverified("") == {}


class TestBuildUserContext:
    def test_explicit_headers_win(self):
        ctx = build_user_context_from_headers(
            {
                "X-Forwarded-User": "alice",
                "X-Forwarded-Groups": "data-eng, admin",
                "Authorization": "Bearer " + _fake_jwt({"sub": "bob"}),
            },
            session_id="s1",
        )
        assert ctx.user_name == "alice"  # ヘッダが JWT より優先
        assert ctx.groups == ("data-eng", "admin")
        assert ctx.knox_jwt is not None
        assert ctx.session_id == "s1"

    def test_falls_back_to_jwt_claims(self):
        token = _fake_jwt({"preferred_username": "carol", "groups": ["viewer"]})
        ctx = build_user_context_from_headers({"Authorization": f"Bearer {token}"})
        assert ctx.user_name == "carol"
        assert ctx.groups == ("viewer",)

    def test_anonymous_when_nothing(self):
        ctx = build_user_context_from_headers({})
        assert ctx.user_name == "anonymous"
        assert ctx.groups == ()
        assert ctx.knox_jwt is None


class TestBearerHeader:
    def test_with_jwt(self):
        ctx = UserContext(user_name="alice", knox_jwt="tok")
        assert bearer_header(ctx) == {"Authorization": "Bearer tok"}

    def test_without_jwt(self):
        assert bearer_header(UserContext(user_name="alice")) == {}


# ---------- errors ----------

class TestErrorHelpers:
    def test_ok_shape(self):
        r = ok({"data": [1, 2, 3]})
        assert r == {"status": "ok", "data": [1, 2, 3]}

    def test_err_shape(self):
        r = err(ErrorCode.PERM_SELECT_DENIED, "denied", table="foo")
        assert r["status"] == "error"
        assert r["error_code"] == "PERM_SELECT_DENIED"
        assert r["message"] == "denied"
        assert r["detail"] == {"table": "foo"}

    def test_err_without_detail(self):
        r = err(ErrorCode.AUTH_MISSING, "no auth")
        assert "detail" not in r
        assert r["error_code"] == "AUTH_MISSING"

    def test_all_error_code_prefixes_exist(self):
        # 契約: 7 プレフィックス + 3 transport-level
        for name in (
            "S3_NOT_FOUND", "FORMAT_UNSUPPORTED", "SCHEMA_INFER_FAILED",
            "TRINO_QUERY_FAILED", "PERM_UNKNOWN", "CDV_NOT_RUNNING",
            "OSSIE_YAML_INVALID", "HTTP_TIMEOUT", "HTTP_UNAVAILABLE", "AUTH_MISSING",
        ):
            assert hasattr(ErrorCode, name)


# ---------- redaction ----------

class TestRedactLogging:
    """機微キーがログに載らないことを検証する。"""

    def _capture_log(self, level: str = "INFO") -> io.StringIO:
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setLevel(getattr(logging, level))
        root = logging.getLogger()
        # 既存 handler を退避
        old_handlers = root.handlers[:]
        root.handlers = [handler]
        root.setLevel(getattr(logging, level))
        return buf, handler, old_handlers, root

    def test_knox_jwt_is_redacted(self):
        configure_logging("INFO")
        buf, handler, olds, root = self._capture_log()
        try:
            log = get_logger("test.redact")
            log.info("http.request", knox_jwt="secret", user="alice")
        finally:
            root.handlers = olds
        line = buf.getvalue()
        assert "secret" not in line
        assert "***REDACTED***" in line
        assert "alice" in line  # 非機微キーは残る

    def test_authorization_header_redacted_nested(self):
        configure_logging("INFO")
        buf, handler, olds, root = self._capture_log()
        try:
            log = get_logger("test.redact.nested")
            log.info(
                "http.request",
                headers={"Authorization": "Bearer super-secret", "Content-Type": "application/json"},
            )
        finally:
            root.handlers = olds
        line = buf.getvalue()
        assert "super-secret" not in line
        assert "application/json" in line

    def test_aws_secret_key_redacted(self):
        configure_logging("INFO")
        buf, handler, olds, root = self._capture_log()
        try:
            log = get_logger("test.redact.aws")
            log.info("sts.assume", access_key_id="AKIA", secret_access_key="sekrit")
        finally:
            root.handlers = olds
        line = buf.getvalue()
        assert "sekrit" not in line
        # access_key_id もパターンにマッチする (access.?key) ので REDACTED になる
        assert "AKIA" not in line


# ---------- BaseThorTool ----------

class TestBaseThorTool:
    def test_auth_missing_when_no_context(self):
        from thor.transport.tool_base import BaseThorTool

        class MyTool(BaseThorTool):
            name = "my_tool"
            description = "test"

            def run(self, user_ctx, **kwargs):
                return ok({"echo": kwargs})

        result = MyTool()._run(x=1)
        assert result["status"] == "error"
        assert result["error_code"] == "AUTH_MISSING"

    def test_success_path(self):
        from thor.transport.tool_base import BaseThorTool

        class EchoTool(BaseThorTool):
            name = "echo"
            description = "echo"

            def run(self, user_ctx, **kwargs):
                return ok({"who": user_ctx.user_name, "args": kwargs})

        token = set_user_context(UserContext(user_name="alice"))
        try:
            result = EchoTool()._run(msg="hi")
        finally:
            reset_user_context(token)
        assert result == {"status": "ok", "who": "alice", "args": {"msg": "hi"}}

    def test_exception_is_caught(self):
        from thor.transport.tool_base import BaseThorTool

        class BoomTool(BaseThorTool):
            name = "boom"
            description = "raises"

            def run(self, user_ctx, **kwargs):
                raise RuntimeError("kaboom")

        token = set_user_context(UserContext(user_name="alice"))
        try:
            result = BoomTool()._run()
        finally:
            reset_user_context(token)
        assert result["status"] == "error"
        assert "kaboom" in result["message"]

    def test_bad_result_shape_rejected(self):
        from thor.transport.tool_base import BaseThorTool

        class BadTool(BaseThorTool):
            name = "bad"
            description = "returns wrong shape"

            def run(self, user_ctx, **kwargs):
                return "not a dict"  # type: ignore[return-value]

        token = set_user_context(UserContext(user_name="alice"))
        try:
            result = BadTool()._run()
        finally:
            reset_user_context(token)
        assert result["status"] == "error"
        assert "unexpected shape" in result["message"]

    def test_requires_auth_false(self):
        from thor.transport.tool_base import BaseThorTool

        class OpenTool(BaseThorTool):
            name = "open"
            description = "no auth needed"
            requires_auth = False

            def run(self, user_ctx, **kwargs):
                return ok({"user": user_ctx.user_name if user_ctx else None})

        result = OpenTool()._run()
        assert result == {"status": "ok", "user": None}

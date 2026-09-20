"""thor.transport.config の解決順テスト。

env / Cloudera AI Workbench の cml.data_v1 (Data Connections) の解決順を、
実際に cml が入っていない local dev でも通るように monkeypatch でテストする。

対象:
* :func:`get_trino_config` — 4 段解決 (named → auto-detect → env → None)
* :func:`get_s3_config`    — 3 段解決 + region default
* :func:`get_llm_config`   — 4 provider の必須 env 判定
* :func:`get_cdv_config`   — env only
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Optional

import pytest

from thor.transport import config as cfg_mod


# ------------------------------------------------------------------ #
# 事前クリーンアップ: すべての解決 env をテスト前に必ず消しておく。
# ------------------------------------------------------------------ #
_ALL_ENV_KEYS = [
    # trino
    "THOR_TRINO_CONNECTION_NAME",
    "THOR_TRINO_HOST",
    "THOR_TRINO_PORT",
    "THOR_TRINO_SCHEME",
    "THOR_TRINO_VERIFY_SSL",
    "THOR_TRINO_CATALOG",
    "THOR_TRINO_SCHEMA",
    # s3
    "THOR_S3_CONNECTION_NAME",
    "AWS_REGION",
    "THOR_AWS_REGION",
    "THOR_S3_ENDPOINT_URL",
    # llm
    "THOR_LLM_PROVIDER",
    "THOR_LLM_ROUTER_MODEL",
    "THOR_LLM_ANALYTICS_MODEL",
    "CAI_INFERENCE_BASE_URL",
    "CAI_INFERENCE_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    # cdv
    "THOR_CDV_BASE_URL",
    "THOR_CDV_TRINO_CONNECTION_ID",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """各テストで env を白紙に。cml.data も default で無効化する。"""
    for k in _ALL_ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    # cml.data_v1 が local dev 環境で偶然入っていても、テストでは無効化する
    monkeypatch.setattr(cfg_mod, "_cmldata", None)


def _fake_conn(**params: Any) -> SimpleNamespace:
    """cml.data の Connection オブジェクトを模したもの。

    :func:`_conn_type` / :func:`_conn_params` が
    ``type`` 属性と ``parameters`` (dict) 属性を見る仕様なのでそれに合わせる。
    """
    conn_type: str = params.pop("_type", "trino")
    conn_name: Optional[str] = params.pop("_name", None)
    return SimpleNamespace(type=conn_type, name=conn_name, parameters=params)


# ================================================================== #
# get_trino_config
# ================================================================== #
class TestGetTrinoConfig:
    def test_returns_none_when_no_env_and_no_cml(self) -> None:
        assert cfg_mod.get_trino_config() is None

    def test_env_fallback_minimal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("THOR_TRINO_HOST", "trino.example.com")
        c = cfg_mod.get_trino_config()
        assert c is not None
        assert c.host == "trino.example.com"
        assert c.port == 443
        assert c.scheme == "https"
        assert c.catalog == "iceberg"  # default
        assert c.schema == "demo"  # default
        assert c.connection_name is None
        assert c.verify_ssl is True

    def test_env_fallback_full_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("THOR_TRINO_HOST", "cdw.internal")
        monkeypatch.setenv("THOR_TRINO_PORT", "8443")
        monkeypatch.setenv("THOR_TRINO_SCHEME", "https")
        monkeypatch.setenv("THOR_TRINO_VERIFY_SSL", "false")
        monkeypatch.setenv("THOR_TRINO_CATALOG", "hive_prod")
        monkeypatch.setenv("THOR_TRINO_SCHEMA", "analytics")
        c = cfg_mod.get_trino_config()
        assert c is not None
        assert c.host == "cdw.internal"
        assert c.port == 8443
        assert c.verify_ssl is False
        assert c.catalog == "hive_prod"
        assert c.schema == "analytics"

    def test_verify_ssl_ca_bundle_path_passthrough(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("THOR_TRINO_HOST", "trino.example.com")
        monkeypatch.setenv("THOR_TRINO_VERIFY_SSL", "/etc/ssl/certs/ca.pem")
        c = cfg_mod.get_trino_config()
        assert c is not None
        assert c.verify_ssl == "/etc/ssl/certs/ca.pem"

    def test_named_connection_resolved_from_cml(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("THOR_TRINO_CONNECTION_NAME", "prod-cdw")
        conn = _fake_conn(
            _type="cdw",
            _name="prod-cdw",
            host="cdw.company.example",
            port=443,
            catalog="iceberg",
            schema="finance",
        )

        def fake_get(name: str) -> Any:
            assert name == "prod-cdw"
            return conn

        monkeypatch.setattr(cfg_mod, "_cml_get_connection", fake_get)
        c = cfg_mod.get_trino_config()
        assert c is not None
        assert c.host == "cdw.company.example"
        assert c.connection_name == "prod-cdw"
        assert c.schema == "finance"

    def test_env_catalog_overrides_data_connection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """env の THOR_TRINO_CATALOG は Data Connection より優先される。"""
        monkeypatch.setenv("THOR_TRINO_CONNECTION_NAME", "prod-cdw")
        monkeypatch.setenv("THOR_TRINO_CATALOG", "override_catalog")
        conn = _fake_conn(
            _type="cdw",
            _name="prod-cdw",
            host="cdw.example.com",
            catalog="original_catalog",
        )
        monkeypatch.setattr(
            cfg_mod, "_cml_get_connection", lambda name: conn
        )
        c = cfg_mod.get_trino_config()
        assert c is not None
        assert c.catalog == "override_catalog"

    def test_auto_detect_first_matching_connection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """名前指定が無くても Trino/CDW タイプがあれば自動採用。"""
        s3_conn = _fake_conn(_type="s3", _name="my-s3", region="us-east-1")
        trino_conn = _fake_conn(
            _type="trino",
            _name="auto-trino",
            host="auto.trino.example",
            port=443,
        )
        monkeypatch.setattr(
            cfg_mod, "_cml_list_connections", lambda: [s3_conn, trino_conn]
        )
        c = cfg_mod.get_trino_config()
        assert c is not None
        assert c.host == "auto.trino.example"
        assert c.connection_name == "auto-trino"

    def test_named_missing_falls_through_to_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """明示名指定が cml から取れなくても env host があれば動く。"""
        monkeypatch.setenv("THOR_TRINO_CONNECTION_NAME", "does-not-exist")
        monkeypatch.setenv("THOR_TRINO_HOST", "fallback.trino.example")
        monkeypatch.setattr(
            cfg_mod, "_cml_get_connection", lambda name: None
        )
        monkeypatch.setattr(cfg_mod, "_cml_list_connections", lambda: [])
        c = cfg_mod.get_trino_config()
        assert c is not None
        assert c.host == "fallback.trino.example"
        assert c.connection_name is None

    def test_connection_without_host_falls_through_to_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Data Connection に host が無ければ env fallback に落ちる。"""
        monkeypatch.setenv("THOR_TRINO_CONNECTION_NAME", "broken")
        monkeypatch.setenv("THOR_TRINO_HOST", "fallback.example")
        broken = _fake_conn(_type="trino", _name="broken")  # no host
        monkeypatch.setattr(
            cfg_mod, "_cml_get_connection", lambda name: broken
        )
        monkeypatch.setattr(cfg_mod, "_cml_list_connections", lambda: [])
        c = cfg_mod.get_trino_config()
        assert c is not None
        assert c.host == "fallback.example"


# ================================================================== #
# get_s3_config
# ================================================================== #
class TestGetS3Config:
    def test_default_region_when_nothing_set(self) -> None:
        c = cfg_mod.get_s3_config()
        assert c is not None
        assert c.region == "us-east-1"
        assert c.connection_name is None
        assert c.endpoint_url is None

    def test_env_region_and_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AWS_REGION", "ap-northeast-1")
        monkeypatch.setenv("THOR_S3_ENDPOINT_URL", "https://minio.example")
        c = cfg_mod.get_s3_config()
        assert c is not None
        assert c.region == "ap-northeast-1"
        assert c.endpoint_url == "https://minio.example"

    def test_named_connection_from_cml(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("THOR_S3_CONNECTION_NAME", "customer-lake")
        conn = _fake_conn(
            _type="s3",
            _name="customer-lake",
            region="us-west-2",
        )
        monkeypatch.setattr(
            cfg_mod, "_cml_get_connection", lambda name: conn
        )
        c = cfg_mod.get_s3_config()
        assert c is not None
        assert c.region == "us-west-2"
        assert c.connection_name == "customer-lake"

    def test_auto_detect_s3_connection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trino = _fake_conn(_type="trino", _name="t", host="h")
        s3 = _fake_conn(_type="s3", _name="auto-s3", region="eu-west-1")
        monkeypatch.setattr(
            cfg_mod, "_cml_list_connections", lambda: [trino, s3]
        )
        c = cfg_mod.get_s3_config()
        assert c is not None
        assert c.connection_name == "auto-s3"
        assert c.region == "eu-west-1"


# ================================================================== #
# get_llm_config
# ================================================================== #
class TestGetLLMConfig:
    def test_no_provider_returns_none(self) -> None:
        assert cfg_mod.get_llm_config() is None

    def test_unknown_provider_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("THOR_LLM_PROVIDER", "invalid_xyz")
        assert cfg_mod.get_llm_config() is None

    # ---- CAI ----
    def test_cai_requires_base_url_and_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("THOR_LLM_PROVIDER", "cai")
        # base URL のみ → まだ None
        monkeypatch.setenv("CAI_INFERENCE_BASE_URL", "https://cai.example")
        assert cfg_mod.get_llm_config() is None
        monkeypatch.setenv("CAI_INFERENCE_API_KEY", "secret-key")
        c = cfg_mod.get_llm_config()
        assert c is not None
        assert c.provider == "cai"
        assert c.api_base == "https://cai.example"
        assert c.api_key == "secret-key"
        assert c.model_light == "llama-3-8b-instruct"
        assert c.model_strong == "llama-3-70b-instruct"

    def test_cai_backwards_compat_without_provider_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """provider 未指定でも CAI env が両方揃えば cai として動く。"""
        monkeypatch.setenv("CAI_INFERENCE_BASE_URL", "https://cai.example")
        monkeypatch.setenv("CAI_INFERENCE_API_KEY", "secret-key")
        c = cfg_mod.get_llm_config()
        assert c is not None
        assert c.provider == "cai"

    # ---- Anthropic ----
    def test_anthropic_requires_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("THOR_LLM_PROVIDER", "anthropic")
        assert cfg_mod.get_llm_config() is None
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
        c = cfg_mod.get_llm_config()
        assert c is not None
        assert c.provider == "anthropic"
        assert c.api_key == "sk-ant-xxx"
        assert c.api_base is None
        assert c.model_light == "claude-3-5-haiku-latest"

    def test_anthropic_optional_base_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("THOR_LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example")
        c = cfg_mod.get_llm_config()
        assert c is not None
        assert c.api_base == "https://proxy.example"

    # ---- OpenAI ----
    def test_openai_requires_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("THOR_LLM_PROVIDER", "openai")
        assert cfg_mod.get_llm_config() is None
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-yyy")
        c = cfg_mod.get_llm_config()
        assert c is not None
        assert c.provider == "openai"
        assert c.model_light == "gpt-4o-mini"

    # ---- Bedrock ----
    def test_bedrock_requires_region(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("THOR_LLM_PROVIDER", "bedrock")
        assert cfg_mod.get_llm_config() is None
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        c = cfg_mod.get_llm_config()
        assert c is not None
        assert c.provider == "bedrock"
        assert c.aws_region == "us-east-1"
        assert c.api_key is None

    # ---- Model overrides ----
    def test_custom_router_and_analytics_models(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("THOR_LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
        monkeypatch.setenv(
            "THOR_LLM_ROUTER_MODEL", "claude-3-opus-20240229"
        )
        monkeypatch.setenv(
            "THOR_LLM_ANALYTICS_MODEL", "claude-3-5-sonnet-latest"
        )
        c = cfg_mod.get_llm_config()
        assert c is not None
        assert c.model_light == "claude-3-opus-20240229"
        assert c.model_strong == "claude-3-5-sonnet-latest"

    def test_provider_case_insensitive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("THOR_LLM_PROVIDER", "Anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
        c = cfg_mod.get_llm_config()
        assert c is not None
        assert c.provider == "anthropic"


# ================================================================== #
# get_cdv_config
# ================================================================== #
class TestGetCDVConfig:
    def test_none_when_base_url_missing(self) -> None:
        assert cfg_mod.get_cdv_config() is None

    def test_base_url_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("THOR_CDV_BASE_URL", "https://cdv.example.com/")
        c = cfg_mod.get_cdv_config()
        assert c is not None
        assert c.base_url == "https://cdv.example.com"  # trailing slash 除去
        assert c.trino_connection_id is None

    def test_with_trino_connection_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("THOR_CDV_BASE_URL", "https://cdv.example.com")
        monkeypatch.setenv("THOR_CDV_TRINO_CONNECTION_ID", "42")
        c = cfg_mod.get_cdv_config()
        assert c is not None
        assert c.trino_connection_id == "42"


# ================================================================== #
# 内部ヘルパ (_env, _parse_verify_ssl)
# ================================================================== #
class TestInternalHelpers:
    def test_env_strips_and_returns_none_for_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SOME_KEY", "  hello  ")
        assert cfg_mod._env("SOME_KEY") == "hello"
        monkeypatch.setenv("SOME_KEY", "   ")
        assert cfg_mod._env("SOME_KEY") is None
        monkeypatch.delenv("SOME_KEY", raising=False)
        assert cfg_mod._env("SOME_KEY") is None

    @pytest.mark.parametrize(
        "raw,expected",
        [
            (None, True),
            ("true", True),
            ("True", True),
            ("1", True),
            ("yes", True),
            ("false", False),
            ("False", False),
            ("0", False),
            ("no", False),
            ("/etc/ssl/ca.pem", "/etc/ssl/ca.pem"),
        ],
    )
    def test_parse_verify_ssl(
        self, raw: Optional[str], expected: Any
    ) -> None:
        assert cfg_mod._parse_verify_ssl(raw) == expected

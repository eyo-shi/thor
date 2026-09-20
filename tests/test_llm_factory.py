"""thor.transport.llm_factory の provider prefix / kwargs 組み立てテスト。

Crew.ai の :class:`crewai.LLM` を実際に構築せずに、
どんな kwargs で呼ばれたかを spy して検証する。
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from thor.transport import config as cfg_mod
from thor.transport import llm_factory


# ------------------------------------------------------------------ #
# ヘルパ: crewai.LLM を spy で差し替える
# ------------------------------------------------------------------ #
class _SpyLLM:
    """crewai.LLM の代役。build kwargs を丸ごと保持する。"""

    last_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        _SpyLLM.last_kwargs = dict(kwargs)
        self.kwargs = kwargs


@pytest.fixture
def spy_llm(monkeypatch: pytest.MonkeyPatch) -> type[_SpyLLM]:
    """`from crewai import LLM` を _SpyLLM に差し替える。

    :mod:`thor.transport.llm_factory` は関数内 import しているため、
    ``sys.modules["crewai"]`` の LLM 属性を書き換えるだけで足りる。
    ``crewai`` 本体が未インストールでも仮の module を注入して通す。
    """
    import sys
    import types

    fake_crewai = types.ModuleType("crewai")
    fake_crewai.LLM = _SpyLLM  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "crewai", fake_crewai)
    _SpyLLM.last_kwargs = {}
    return _SpyLLM


@pytest.fixture
def stub_config(monkeypatch: pytest.MonkeyPatch):
    """任意の LLMConfig を返すよう get_llm_config を差し替えるファクトリ。"""

    def _install(cfg: Optional[cfg_mod.LLMConfig]) -> None:
        monkeypatch.setattr(llm_factory, "get_llm_config", lambda: cfg)

    return _install


# ================================================================== #
# _apply_prefix
# ================================================================== #
class TestApplyPrefix:
    @pytest.mark.parametrize(
        "provider,model,expected",
        [
            ("cai", "llama-3-8b-instruct", "openai/llama-3-8b-instruct"),
            ("openai", "gpt-4o", "openai/gpt-4o"),
            ("anthropic", "claude-3-5-haiku-latest", "anthropic/claude-3-5-haiku-latest"),
            (
                "bedrock",
                "anthropic.claude-3-5-sonnet-20241022-v2:0",
                "bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0",
            ),
        ],
    )
    def test_common_providers(
        self, provider: str, model: str, expected: str
    ) -> None:
        assert llm_factory._apply_prefix(provider, model) == expected

    def test_slash_in_model_is_respected(self) -> None:
        """呼び出し側が明示的に openai/foo/bar と付けていればそのまま通す。"""
        assert (
            llm_factory._apply_prefix("cai", "openai/custom-name")
            == "openai/custom-name"
        )

    def test_unknown_provider_passthrough(self) -> None:
        """未知の provider は prefix を付けずに LiteLLM へ渡す。"""
        assert (
            llm_factory._apply_prefix("weird", "some-model") == "some-model"
        )


# ================================================================== #
# build_llm
# ================================================================== #
class TestBuildLLM:
    def test_none_when_no_config(self, stub_config: Any) -> None:
        stub_config(None)
        assert llm_factory.build_llm("light") is None
        assert llm_factory.build_llm("strong") is None

    def test_cai_light_prefixes_openai_and_sets_base_url(
        self, spy_llm: type[_SpyLLM], stub_config: Any
    ) -> None:
        stub_config(
            cfg_mod.LLMConfig(
                provider="cai",
                model_light="llama-3-8b-instruct",
                model_strong="llama-3-70b-instruct",
                api_base="https://cai.example",
                api_key="sk-cai-abc",
            )
        )
        obj = llm_factory.build_llm("light")
        assert obj is not None
        k = spy_llm.last_kwargs
        assert k["model"] == "openai/llama-3-8b-instruct"
        assert k["base_url"] == "https://cai.example"
        assert k["api_key"] == "sk-cai-abc"
        assert "aws_region_name" not in k

    def test_cai_strong_uses_analytics_model(
        self, spy_llm: type[_SpyLLM], stub_config: Any
    ) -> None:
        stub_config(
            cfg_mod.LLMConfig(
                provider="cai",
                model_light="llama-3-8b-instruct",
                model_strong="llama-3-70b-instruct",
                api_base="https://cai.example",
                api_key="sk-cai-abc",
            )
        )
        obj = llm_factory.build_llm("strong")
        assert obj is not None
        assert spy_llm.last_kwargs["model"] == "openai/llama-3-70b-instruct"

    def test_anthropic(
        self, spy_llm: type[_SpyLLM], stub_config: Any
    ) -> None:
        stub_config(
            cfg_mod.LLMConfig(
                provider="anthropic",
                model_light="claude-3-5-haiku-latest",
                model_strong="claude-3-5-sonnet-latest",
                api_base=None,
                api_key="sk-ant-xxx",
            )
        )
        obj = llm_factory.build_llm("light")
        assert obj is not None
        k = spy_llm.last_kwargs
        assert k["model"] == "anthropic/claude-3-5-haiku-latest"
        assert k["api_key"] == "sk-ant-xxx"
        assert "base_url" not in k
        assert "aws_region_name" not in k

    def test_openai_with_optional_base_url(
        self, spy_llm: type[_SpyLLM], stub_config: Any
    ) -> None:
        stub_config(
            cfg_mod.LLMConfig(
                provider="openai",
                model_light="gpt-4o-mini",
                model_strong="gpt-4o",
                api_base="https://openai-proxy.example",
                api_key="sk-oai-xxx",
            )
        )
        obj = llm_factory.build_llm("strong")
        assert obj is not None
        k = spy_llm.last_kwargs
        assert k["model"] == "openai/gpt-4o"
        assert k["base_url"] == "https://openai-proxy.example"
        assert k["api_key"] == "sk-oai-xxx"

    def test_bedrock_passes_aws_region_name(
        self, spy_llm: type[_SpyLLM], stub_config: Any
    ) -> None:
        stub_config(
            cfg_mod.LLMConfig(
                provider="bedrock",
                model_light="anthropic.claude-3-5-haiku-20241022-v1:0",
                model_strong="anthropic.claude-sonnet-4-20250514-v1:0",
                api_base=None,
                api_key=None,
                aws_region="us-east-1",
            )
        )
        obj = llm_factory.build_llm("strong")
        assert obj is not None
        k = spy_llm.last_kwargs
        assert (
            k["model"]
            == "bedrock/anthropic.claude-sonnet-4-20250514-v1:0"
        )
        assert k["aws_region_name"] == "us-east-1"
        # bedrock は IAM role 前提: api_key/base_url を渡さない
        assert "api_key" not in k
        assert "base_url" not in k

    def test_returns_none_when_crewai_missing(
        self,
        stub_config: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """crewai の import が失敗すれば None を返し、例外は投げない。"""
        import builtins
        import sys

        # 前のテストで sys.modules["crewai"] に fake が入っている可能性があるので
        # 明示的に取り除き、真の import ロジックを走らせる。
        monkeypatch.delitem(sys.modules, "crewai", raising=False)

        real_import = builtins.__import__

        def blocked_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "crewai" or name.startswith("crewai."):
                raise ImportError("simulated: crewai not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", blocked_import)
        stub_config(
            cfg_mod.LLMConfig(
                provider="anthropic",
                model_light="claude-3-5-haiku-latest",
                model_strong="claude-3-5-sonnet-latest",
                api_base=None,
                api_key="sk-ant",
            )
        )
        assert llm_factory.build_llm("light") is None

    def test_returns_none_when_llm_ctor_raises(
        self,
        stub_config: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """crewai.LLM の構築で例外が飛んでも None を返す (呼び出し側が 503 化)。"""
        import sys
        import types

        class _BoomLLM:
            def __init__(self, **kwargs: Any) -> None:
                raise RuntimeError("boom")

        fake_crewai = types.ModuleType("crewai")
        fake_crewai.LLM = _BoomLLM  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "crewai", fake_crewai)

        stub_config(
            cfg_mod.LLMConfig(
                provider="anthropic",
                model_light="claude-3-5-haiku-latest",
                model_strong="claude-3-5-sonnet-latest",
                api_base=None,
                api_key="sk-ant",
            )
        )
        assert llm_factory.build_llm("light") is None


# ================================================================== #
# build_llm_pair
# ================================================================== #
class TestBuildLLMPair:
    def test_returns_two_llms_when_configured(
        self, spy_llm: type[_SpyLLM], stub_config: Any
    ) -> None:
        stub_config(
            cfg_mod.LLMConfig(
                provider="anthropic",
                model_light="claude-3-5-haiku-latest",
                model_strong="claude-3-5-sonnet-latest",
                api_base=None,
                api_key="sk-ant",
            )
        )
        light, strong = llm_factory.build_llm_pair()
        assert light is not None
        assert strong is not None
        # 2 回目の build_llm 呼び出し (strong) で last_kwargs が上書きされている
        assert (
            spy_llm.last_kwargs["model"]
            == "anthropic/claude-3-5-sonnet-latest"
        )

    def test_returns_none_pair_when_no_config(self, stub_config: Any) -> None:
        stub_config(None)
        assert llm_factory.build_llm_pair() == (None, None)

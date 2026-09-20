"""Crew.ai / LiteLLM 用の LLM オブジェクト構築ファクトリ。

:mod:`thor.transport.config` の :func:`get_llm_config` で解決した設定を、
Crew.ai の :class:`crewai.LLM` (中身は LiteLLM ``completion``) が食える形に
組み立てる。provider ごとの prefix ルールと必須 kwargs をここに集約する:

* ``cai`` (Cloudera AI Inference): OpenAI 互換 → ``openai/{model}`` + ``api_base``
* ``anthropic``: ``anthropic/{model}`` + ``api_key``
* ``openai``: ``openai/{model}`` + ``api_key`` (+ optional ``api_base``)
* ``bedrock``: ``bedrock/{model}`` + ``aws_region`` (認証は IAM role 前提)

**注意**:
* Crew.ai / LiteLLM が未インストールの環境 (local dev) では ``None`` を返す。
  呼び出し側 (:mod:`thor.api.routes.wish`) はそれを受け取ったら Router を
  heuristic モードで動かし、Ingestion / Analytics は LLM 無しの制約
  (crewai が LLM 無しでどこまで動くかは Crew 側の実装依存) で走らせる。
* ``api_key`` は :class:`crewai.LLM` オブジェクト内で保持されるが、Task
  inputs には決して漏らさない (Crew.ai の repr は key を伏せる)。
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from thor.transport.config import LLMConfig, get_llm_config
from thor.transport.logging import get_logger

_logger = get_logger(__name__)

LLMRole = Literal["light", "strong"]


def _apply_prefix(provider: str, model: str) -> str:
    """LiteLLM が要求する provider prefix を付ける。既に付いていればそのまま。"""
    if "/" in model:
        return model  # 呼び出し側が明示指定した場合は尊重
    if provider == "cai":
        # Cloudera AI Inference は OpenAI 互換
        return f"openai/{model}"
    if provider == "anthropic":
        return f"anthropic/{model}"
    if provider == "openai":
        return f"openai/{model}"
    if provider == "bedrock":
        return f"bedrock/{model}"
    # unknown provider — LiteLLM に素通し (エラーは LiteLLM が返す)
    return model


def build_llm(role: LLMRole = "light") -> Optional[Any]:
    """Crew.ai の LLM オブジェクトを 1 つ組み立てる。

    :param role: ``"light"`` (Router / Text2SQL の軽量モデル) または
                 ``"strong"`` (Summary / VizPlanner の精度モデル)。

    :returns: :class:`crewai.LLM` インスタンス、または以下のいずれかで ``None``:

      * ``get_llm_config()`` が ``None`` (未設定 or env 不足)
      * ``crewai`` が未インストール
      * :class:`crewai.LLM` 構築で例外
    """
    cfg = get_llm_config()
    if cfg is None:
        _logger.debug("llm_factory.no_config", role=role)
        return None

    try:
        from crewai import LLM  # type: ignore
    except ImportError:
        _logger.debug("llm_factory.crewai_not_installed", role=role)
        return None

    model = cfg.model_light if role == "light" else cfg.model_strong
    prefixed = _apply_prefix(cfg.provider, model)

    kwargs: dict[str, Any] = {"model": prefixed}
    if cfg.api_base:
        kwargs["base_url"] = cfg.api_base
    if cfg.api_key:
        kwargs["api_key"] = cfg.api_key
    if cfg.provider == "bedrock" and cfg.aws_region:
        # LiteLLM は環境変数 AWS_REGION_NAME でも読むが、明示的に渡しておく
        kwargs["aws_region_name"] = cfg.aws_region

    try:
        return LLM(**kwargs)
    except Exception as e:  # noqa: BLE001
        _logger.warning(
            "llm_factory.build_failed",
            provider=cfg.provider,
            role=role,
            error=str(e),
        )
        return None


def build_llm_pair() -> tuple[Optional[Any], Optional[Any]]:
    """(llm_light, llm_strong) を一度にまとめて返す。

    Crew factory (ingestion / analytics) は両方受け取るため、ルート
    (:mod:`thor.api.routes.wish`) から 2 回 :func:`build_llm` するより
    こちらの方が意図が明確。
    """
    return build_llm("light"), build_llm("strong")


__all__ = ["build_llm", "build_llm_pair", "LLMRole"]

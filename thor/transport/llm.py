"""LLM 呼び出しの薄いラッパ (LiteLLM 経由)。

provider (cai / anthropic / openai / bedrock) は :func:`get_llm_config` で
解決する。ここは低レイヤなので、config が None なら黙って ``None`` を返す
(Tool 側でその変換をする責務)。

**方針**:

* LiteLLM が未インストール、または :func:`get_llm_config` が None (env 不足)
  なら ``None`` を返す。呼び出し元 (Tool / Task) は「LLM が使えない環境なので
  heuristic を採用する」fallback を取る。
* 例外は投げず、::class:`ThorErrorResult` 相当の error dict は返さない。
* 常に JSON 応答を期待する ``try_json_completion`` のみ提供する。
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from thor.transport.config import get_llm_config
from thor.transport.llm_factory import _apply_prefix
from thor.transport.logging import get_logger

_logger = get_logger(__name__)

# 応答本文から ```json ... ``` フェンスを剥がす
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)


def try_json_completion(
    prompt: str,
    *,
    model: Optional[str] = None,
    max_tokens: int = 512,
    temperature: float = 0.0,
    timeout: float = 30.0,
) -> Optional[dict[str, Any]]:
    """LLM に JSON 応答を要求し、パースして dict を返す。

    LiteLLM の :func:`completion` を使う。以下のいずれかの理由で **None** を返す:

    * ``litellm`` が未インストール
    * :func:`get_llm_config` が ``None`` (provider 未設定 or env 不足)
    * LLM 応答が JSON にパースできない
    * ネットワーク例外 / タイムアウト

    :param model: 明示的なモデル ID (省略時は provider の light モデル)
    """
    try:
        import litellm  # type: ignore
    except ImportError:
        _logger.debug("llm.litellm_not_installed")
        return None

    cfg = get_llm_config()
    if cfg is None:
        _logger.debug("llm.config_missing")
        return None

    resolved_model = model or cfg.model_light
    prefixed = _apply_prefix(cfg.provider, resolved_model)

    kwargs: dict[str, Any] = {
        "model": prefixed,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a strict JSON responder. Reply with ONLY a JSON "
                    "object (no prose, no code fences)."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "timeout": timeout,
    }
    if cfg.api_base:
        kwargs["api_base"] = cfg.api_base
    if cfg.api_key:
        kwargs["api_key"] = cfg.api_key
    if cfg.provider == "bedrock" and cfg.aws_region:
        kwargs["aws_region_name"] = cfg.aws_region

    try:
        resp = litellm.completion(**kwargs)  # type: ignore[attr-defined]
        # OpenAI 互換の choices[0].message.content
        content = resp["choices"][0]["message"]["content"]  # type: ignore[index]
    except Exception as e:  # noqa: BLE001
        _logger.warning(
            "llm.completion_failed", provider=cfg.provider, error=str(e)
        )
        return None

    return _parse_json_lenient(content)


def _parse_json_lenient(text: Any) -> Optional[dict[str, Any]]:
    """LLM 応答本文 (文字列想定) を JSON dict に落とす。"""
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped:
        return None
    # 直パース
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        # ```json ... ``` フェンス剥がし
        m = _JSON_FENCE_RE.search(stripped)
        if not m:
            _logger.debug("llm.json_parse_failed", text_prefix=stripped[:80])
            return None
        try:
            value = json.loads(m.group(1))
        except json.JSONDecodeError:
            _logger.debug("llm.json_parse_failed_fence", text_prefix=stripped[:80])
            return None
    return value if isinstance(value, dict) else None


__all__ = ["try_json_completion"]

"""LLM 呼び出しの薄いラッパ (LiteLLM 経由)。

Cloudera AI Inference は OpenAI 互換 API を提供するため、``litellm.completion``
に ``api_base`` と ``api_key`` を渡すだけで叩ける。

**方針**:

* LiteLLM が未インストール、または環境変数が不足していれば ``None`` を返す。
  呼び出し元 (Tool / Task) は「LLM が使えない環境なので heuristic を採用する」
  fallback を取る。
* 例外は投げず、::class:`ThorErrorResult` 相当の error dict は返さない
  (Tool 側でその変換をする責務のため、ここは低レイヤ)。
* 常に JSON 応答を期待する ``try_json_completion`` のみ提供する
  (自由形式の LLM 応答は Crew.ai の Task 側で扱うので、ここでは不要)。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

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
    * 必須環境変数 ``CAI_INFERENCE_BASE_URL`` / ``CAI_INFERENCE_API_KEY`` が未設定
    * LLM 応答が JSON にパースできない
    * ネットワーク例外 / タイムアウト

    :param model: 明示的なモデル ID (省略時は ``THOR_LLM_ROUTER_MODEL`` を使う)
    """
    try:
        import litellm  # type: ignore
    except ImportError:
        _logger.debug("llm.litellm_not_installed")
        return None

    base_url = os.environ.get("CAI_INFERENCE_BASE_URL", "").strip()
    api_key = os.environ.get("CAI_INFERENCE_API_KEY", "").strip()
    if not base_url or not api_key:
        _logger.debug("llm.env_missing", base_url_present=bool(base_url))
        return None

    resolved_model = model or os.environ.get(
        "THOR_LLM_ROUTER_MODEL", "llama-3-8b-instruct"
    )

    try:
        resp = litellm.completion(  # type: ignore[attr-defined]
            model=f"openai/{resolved_model}",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a strict JSON responder. Reply with ONLY a JSON "
                        "object (no prose, no code fences)."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            api_base=base_url,
            api_key=api_key,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
        )
        # OpenAI 互換の choices[0].message.content
        content = resp["choices"][0]["message"]["content"]  # type: ignore[index]
    except Exception as e:  # noqa: BLE001
        _logger.warning("llm.completion_failed", error=str(e))
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

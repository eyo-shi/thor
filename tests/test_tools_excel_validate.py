"""``ExcelHeaderValidateTool`` (Excel ヘッダー LLM 検証段) のテスト。

LLM は :func:`thor.transport.llm.try_json_completion` を monkeypatch で
差し替えて挙動を検証する。実際の LiteLLM や CAI Inference は叩かない。
"""
from __future__ import annotations

from typing import Any, Optional
from unittest import mock

import pytest

from thor.tools.excel import ExcelHeaderValidateTool


# ------------------------------------------------------------------ #
# Fixtures / helpers
# ------------------------------------------------------------------ #
def _valid_candidate(header_row: Optional[int] = 3) -> dict[str, Any]:
    return {
        "sheet": "Sheet1",
        "header_row": header_row,
        "columns": ["region", "order_date", "revenue"],
        "meta_kv": [
            {"key": "Report", "value": "Sales 2024"},
            {"key": "Owner", "value": "SalesOps"},
        ],
        "surrounding_rows": [
            ["Report", "Sales 2024"],
            ["Owner", "SalesOps"],
            [None, None],
            ["region", "order_date", "revenue"],
            ["Tokyo", "2024-01-01", 100000],
            ["Osaka", "2024-01-02", 50000],
        ],
    }


# ------------------------------------------------------------------ #
# LLM 未設定 → heuristic 素通し
# ------------------------------------------------------------------ #
def test_validate_passes_through_when_llm_unavailable() -> None:
    """LiteLLM が None を返すとき、heuristic を素通し (is_valid=True) で返す。"""
    tool = ExcelHeaderValidateTool()
    with mock.patch(
        "thor.tools.excel.try_json_completion", return_value=None
    ):
        result = tool.run(user_ctx=None, candidates=[_valid_candidate()])
    assert result["status"] == "ok"
    v = result["validations"][0]
    assert v["is_valid"] is True
    assert v["source"] == "fallback"
    assert v["confidence"] == "low"
    assert v["candidate_header_row"] == 3


# ------------------------------------------------------------------ #
# LLM が妥当性を承認
# ------------------------------------------------------------------ #
def test_validate_accepts_llm_approval() -> None:
    tool = ExcelHeaderValidateTool()
    llm_response = {
        "is_valid": True,
        "revised_header_row": None,
        "confidence": "high",
        "reason": "row 3 has string-typed unique values consistent with a table header",
    }
    with mock.patch(
        "thor.tools.excel.try_json_completion", return_value=llm_response
    ):
        result = tool.run(user_ctx=None, candidates=[_valid_candidate()])
    v = result["validations"][0]
    assert v["is_valid"] is True
    assert v["source"] == "llm"
    assert v["confidence"] == "high"
    assert v["revised_header_row"] is None


# ------------------------------------------------------------------ #
# LLM が別の行を提案
# ------------------------------------------------------------------ #
def test_validate_returns_llm_revision() -> None:
    tool = ExcelHeaderValidateTool()
    llm_response = {
        "is_valid": False,
        "revised_header_row": 4,
        "confidence": "medium",
        "reason": "row 3 looks like a section title; row 4 is the real header",
    }
    with mock.patch(
        "thor.tools.excel.try_json_completion", return_value=llm_response
    ):
        result = tool.run(user_ctx=None, candidates=[_valid_candidate()])
    v = result["validations"][0]
    assert v["is_valid"] is False
    assert v["revised_header_row"] == 4
    assert v["source"] == "llm"


# ------------------------------------------------------------------ #
# heuristic 側が header 未検出のときは LLM を呼ばずに reject
# ------------------------------------------------------------------ #
def test_validate_shortcircuits_when_heuristic_had_no_header() -> None:
    tool = ExcelHeaderValidateTool()
    # LLM を呼ばない (呼ばれたら失敗)
    with mock.patch(
        "thor.tools.excel.try_json_completion",
        side_effect=AssertionError("LLM must not be called"),
    ):
        result = tool.run(
            user_ctx=None, candidates=[_valid_candidate(header_row=None)]
        )
    v = result["validations"][0]
    assert v["is_valid"] is False
    assert v["source"] == "heuristic"
    assert "could not locate" in v["reason"]


# ------------------------------------------------------------------ #
# 不正入力 (dict でも list でもない) は構造化エラー
# ------------------------------------------------------------------ #
def test_validate_rejects_bad_candidate_type() -> None:
    tool = ExcelHeaderValidateTool()
    result = tool.run(user_ctx=None, candidates=[42])  # type: ignore[list-item]
    assert result["status"] == "error"
    assert result["error_code"] == "FORMAT_EXCEL_HEADER_UNDETECTED"


# ------------------------------------------------------------------ #
# dict → _SheetCandidate 経路 (crewai 経由の呼び方) が通る
# ------------------------------------------------------------------ #
def test_validate_accepts_dict_candidate_shape() -> None:
    tool = ExcelHeaderValidateTool()
    with mock.patch(
        "thor.tools.excel.try_json_completion", return_value=None
    ):
        result = tool.run(user_ctx=None, candidates=[_valid_candidate()])
    assert result["status"] == "ok"
    assert len(result["validations"]) == 1


# ------------------------------------------------------------------ #
# revised_header_row が文字列の数字でも int に落ちる
# ------------------------------------------------------------------ #
def test_validate_coerces_string_int_revised_row() -> None:
    tool = ExcelHeaderValidateTool()
    llm_response = {
        "is_valid": False,
        "revised_header_row": "5",
        "confidence": "medium",
        "reason": "shifted",
    }
    with mock.patch(
        "thor.tools.excel.try_json_completion", return_value=llm_response
    ):
        result = tool.run(user_ctx=None, candidates=[_valid_candidate()])
    v = result["validations"][0]
    assert v["revised_header_row"] == 5


# ------------------------------------------------------------------ #
# 空 candidates は空 validations を返す
# ------------------------------------------------------------------ #
def test_validate_empty_input() -> None:
    tool = ExcelHeaderValidateTool()
    result = tool.run(user_ctx=None, candidates=[])
    assert result["status"] == "ok"
    assert result["validations"] == []

"""``thor.demo.warm`` (デモフォールバック) のテスト。

``THOR_DEMO_MODE`` 環境変数を monkeypatch で切り替え、canned data の露出が
モード依存で on/off することを検証する。
"""
from __future__ import annotations

import pytest

from thor.demo import warm


# ------------------------------------------------------------------ #
# is_warm_mode
# ------------------------------------------------------------------ #
def test_is_warm_mode_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("THOR_DEMO_MODE", raising=False)
    assert warm.is_warm_mode() is False


def test_is_warm_mode_off_when_explicit_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THOR_DEMO_MODE", "off")
    assert warm.is_warm_mode() is False


def test_is_warm_mode_on_when_warm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THOR_DEMO_MODE", "warm")
    assert warm.is_warm_mode() is True


def test_is_warm_mode_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THOR_DEMO_MODE", "WARM")
    assert warm.is_warm_mode() is True


# ------------------------------------------------------------------ #
# warm_table
# ------------------------------------------------------------------ #
def test_warm_table_returns_none_when_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("THOR_DEMO_MODE", raising=False)
    assert warm.warm_table("iceberg.demo.sales_2024") is None


def test_warm_table_returns_canned_when_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THOR_DEMO_MODE", "warm")
    t = warm.warm_table("iceberg.demo.sales_2024")
    assert t is not None
    assert t["fq"] == "iceberg.demo.sales_2024"
    assert t["row_count"] > 0
    assert any(c["name"] == "revenue" for c in t["columns"])
    assert t["ossie_path"].endswith(".yaml")


def test_warm_table_unknown_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THOR_DEMO_MODE", "warm")
    assert warm.warm_table("iceberg.nonexistent.table") is None


# ------------------------------------------------------------------ #
# warm_summary
# ------------------------------------------------------------------ #
def test_warm_summary_returns_none_when_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("THOR_DEMO_MODE", raising=False)
    assert warm.warm_summary("iceberg.demo.sales_2024") is None


def test_warm_summary_returns_markdown_when_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THOR_DEMO_MODE", "warm")
    s = warm.warm_summary("iceberg.demo.sales_2024")
    assert s is not None
    assert s.startswith("# sales_2024")
    assert "総売上" in s


# ------------------------------------------------------------------ #
# warm_dashboard
# ------------------------------------------------------------------ #
def test_warm_dashboard_returns_none_when_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("THOR_DEMO_MODE", raising=False)
    assert warm.warm_dashboard("iceberg.demo.sales_2024") is None


def test_warm_dashboard_returns_canned_when_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THOR_DEMO_MODE", "warm")
    monkeypatch.delenv("THOR_DEMO_CDV_BASE_URL", raising=False)
    d = warm.warm_dashboard("iceberg.demo.sales_2024")
    assert d is not None
    assert d["dashboard_id"] == 42
    assert d["dashboard_path"].startswith("/arc/apps/dashboard/")
    assert len(d["visuals"]) >= 2
    # base URL 無しなら dashboard_url は含まれない
    assert "dashboard_url" not in d


def test_warm_dashboard_composes_url_with_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THOR_DEMO_MODE", "warm")
    monkeypatch.setenv("THOR_DEMO_CDV_BASE_URL", "https://cdv.example.com/")
    d = warm.warm_dashboard("iceberg.demo.sales_2024")
    assert d is not None
    assert d["dashboard_url"] == "https://cdv.example.com/arc/apps/dashboard/42"


# ------------------------------------------------------------------ #
# list_warm_tables / has_warm_asset
# ------------------------------------------------------------------ #
def test_list_warm_tables_empty_when_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("THOR_DEMO_MODE", raising=False)
    assert warm.list_warm_tables() == []


def test_list_warm_tables_when_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THOR_DEMO_MODE", "warm")
    tables = warm.list_warm_tables()
    assert "iceberg.demo.sales_2024" in tables
    # ソート済みで返る
    assert tables == sorted(tables)


def test_has_warm_asset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THOR_DEMO_MODE", "warm")
    assert warm.has_warm_asset("iceberg.demo.sales_2024") is True
    assert warm.has_warm_asset("iceberg.demo.customers") is True
    assert warm.has_warm_asset("iceberg.nonexistent.table") is False


def test_has_warm_asset_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("THOR_DEMO_MODE", raising=False)
    assert warm.has_warm_asset("iceberg.demo.sales_2024") is False


# ------------------------------------------------------------------ #
# パッケージ経由の再エクスポート
# ------------------------------------------------------------------ #
def test_reexports_via_package(monkeypatch: pytest.MonkeyPatch) -> None:
    """thor.demo からも同名で参照できる (公開 API 契約)。"""
    from thor import demo as demo_pkg

    monkeypatch.setenv("THOR_DEMO_MODE", "warm")
    assert demo_pkg.is_warm_mode() is True
    assert demo_pkg.warm_table("iceberg.demo.sales_2024") is not None

"""thor.api の TestClient ベース smoke。

LLM / Trino / S3 は叩かない範囲でルーティング・認証・SSE 応答形式のみを検証する。
Ingestion Crew の kickoff 経路は crewai が未インストールの環境ではフォールバック
(スタブ Crew) で RuntimeError を投げるため、`event: error` が返ることを確認する。
"""
from __future__ import annotations

import json
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from thor.api.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    with TestClient(app) as c:
        yield c


# ------------------------------------------------------------------ #
# トリビアル系
# ------------------------------------------------------------------ #
def test_healthz(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "service": "thor.api"}


def test_root_placeholder_when_no_spa(client: TestClient) -> None:
    """静的ビルドがない状態では JSON プレースホルダを返す。"""
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "thor.api"
    assert body["ui"] == "not-built"


def test_openapi_paths_exposed(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    paths = set(schema["paths"].keys())
    for expected in [
        "/healthz",
        "/api/catalog/schemas",
        "/api/catalog/tables",
        "/api/catalog/columns",
        "/api/files/list",
        "/api/query",
        "/api/ossie/{fq}",
        "/api/artifacts/{artifact_id}",
        "/api/sessions/{session_id}",
        "/api/wish",
    ]:
        assert expected in paths, f"missing route: {expected}"


# ------------------------------------------------------------------ #
# 認証: Knox JWT なしの Trino 系は 401
# ------------------------------------------------------------------ #
def test_catalog_requires_knox_jwt(client: TestClient) -> None:
    r = client.get("/api/catalog/schemas")
    assert r.status_code == 401
    assert r.json()["detail"]["error_code"] == "AUTH_MISSING"


def test_files_requires_knox_jwt(client: TestClient) -> None:
    r = client.get("/api/files/list?bucket=x")
    assert r.status_code == 401
    assert r.json()["detail"]["error_code"] == "AUTH_MISSING"


def test_query_requires_knox_jwt(client: TestClient) -> None:
    r = client.post("/api/query", json={"sql": "SELECT 1"})
    assert r.status_code == 401


# ------------------------------------------------------------------ #
# エラー系
# ------------------------------------------------------------------ #
def test_query_rejects_mutation(client: TestClient) -> None:
    """認証があっても /api/query は DDL / DML を拒否する。"""
    r = client.post(
        "/api/query",
        headers={"Authorization": "Bearer fake.jwt.token"},
        json={"sql": "DROP TABLE x"},
    )
    # 認証は通るが SQL は 400
    assert r.status_code == 400
    assert r.json()["detail"]["error_code"] == "TRINO_QUERY_FAILED"


def test_ossie_bad_fq(client: TestClient) -> None:
    r = client.get("/api/ossie/not_a_fq")
    assert r.status_code == 400
    assert r.json()["detail"]["error_code"] == "BAD_REQUEST"


def test_artifact_not_found(client: TestClient) -> None:
    r = client.get("/api/artifacts/nonexistent")
    assert r.status_code == 404
    assert r.json()["detail"]["error_code"] == "ARTIFACT_NOT_FOUND"


def test_session_not_found(client: TestClient) -> None:
    r = client.get("/api/sessions/nonexistent")
    assert r.status_code == 404
    assert r.json()["detail"]["error_code"] == "SESSION_NOT_FOUND"


# ------------------------------------------------------------------ #
# Wish (SSE)
# ------------------------------------------------------------------ #
def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    current_event: str | None = None
    for line in text.splitlines():
        if line.startswith("event:"):
            current_event = line[len("event:") :].strip()
        elif line.startswith("data:") and current_event:
            payload = line[len("data:") :].strip()
            try:
                events.append((current_event, json.loads(payload)))
            except json.JSONDecodeError:
                events.append((current_event, {"_raw": payload}))
            current_event = None
    return events


def test_wish_chitchat_returns_greeting_no_error(client: TestClient) -> None:
    """Router が CHITCHAT と判定し、error なしで返答トークンを送る。"""
    r = client.post("/api/wish", json={"prompt": "こんにちは"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(r.text)
    kinds = [k for k, _ in events]
    assert "error" not in kinds
    assert "token" in kinds
    assert "done" in kinds
    done_evt = next(p for k, p in events if k == "done")
    assert done_evt["ok"] is True


def test_wish_unknown_asks_clarification(client: TestClient) -> None:
    """Router が UNKNOWN と判定した場合 clarification を token で返す。"""
    r = client.post("/api/wish", json={"prompt": "何か適当な要求"})
    assert r.status_code == 200
    events = _parse_sse(r.text)
    kinds = [k for k, _ in events]
    assert "error" not in kinds
    assert "token" in kinds
    done_evt = next(p for k, p in events if k == "done")
    assert done_evt["ok"] is True


def test_wish_analytics_summary_dispatches_to_summary_crew(
    client: TestClient,
) -> None:
    """Router が ANALYZE_SUMMARY と判定した後、AnalyticsSummaryCrew に届く。

    entity_memory.last_table を事前に流し込んでおくことで、Router 側の
    clarify を回避して AnalyticsSummaryCrew ディスパッチまで到達させる。
    crewai / LLM は未接続なので Crew 起動時に HTTP_UNAVAILABLE または
    それ相当の構造化エラーで返るが、いずれにせよ AnalyticsSummaryCrew の
    running step が SSE に出て、NOT_IMPLEMENTED では **ない** ことを検証。
    """
    from thor.api.state import get_store

    store = get_store()
    sess = store.get_or_create_session("sess_analytics_1", "alice")
    store.update_entity_memory(
        sess.session_id, {"last_table": "iceberg.demo.sales_2024"}
    )
    r = client.post(
        "/api/wish",
        json={"prompt": "そのテーブルをサマリーして", "session_id": sess.session_id},
    )
    assert r.status_code == 200
    events = _parse_sse(r.text)
    # AnalyticsSummaryCrew の running step が出ている
    step_events = [p for k, p in events if k == "step"]
    assert any(
        p.get("agent") == "AnalyticsSummaryCrew" and p.get("status") == "running"
        for p in step_events
    )
    # NOT_IMPLEMENTED では返らない (Summary パスは実装済み)
    error_events = [p for k, p in events if k == "error"]
    for e in error_events:
        assert e.get("error_code") != "NOT_IMPLEMENTED"


def test_wish_analytics_dashboard_dispatches_to_dashboard_crew(
    client: TestClient,
) -> None:
    """Router が ANALYZE_DASHBOARD と判定した後、AnalyticsDashboardCrew に届く。

    Summary と同じく entity_memory.last_table を仕込んで、AnalyticsDashboardCrew
    のディスパッチまで到達させる。crewai / CDV / LLM は未接続なので Crew
    起動時に HTTP_UNAVAILABLE (crewai 無し) または CDV_NOT_RUNNING
    (crewai 有り + THOR_CDV_BASE_URL 未設定) 相当の構造化エラーで返るが、
    いずれにせよ AnalyticsDashboardCrew の running step が SSE に出て、
    NOT_IMPLEMENTED では **ない** ことを検証。
    """
    from thor.api.state import get_store

    store = get_store()
    sess = store.get_or_create_session("sess_dashboard_1", "alice")
    store.update_entity_memory(
        sess.session_id, {"last_table": "iceberg.demo.sales_2024"}
    )
    r = client.post(
        "/api/wish",
        json={
            "prompt": "そのテーブルからダッシュボードを作って",
            "session_id": sess.session_id,
        },
    )
    assert r.status_code == 200
    events = _parse_sse(r.text)
    # AnalyticsDashboardCrew の running step が出ている
    step_events = [p for k, p in events if k == "step"]
    assert any(
        p.get("agent") == "AnalyticsDashboardCrew" and p.get("status") == "running"
        for p in step_events
    )
    # NOT_IMPLEMENTED では返らない (Dashboard パスは実装済み)
    error_events = [p for k, p in events if k == "error"]
    for e in error_events:
        assert e.get("error_code") != "NOT_IMPLEMENTED"


def test_wish_session_id_persisted(client: TestClient) -> None:
    """明示的 session_id (body) が保存され、履歴として読み戻せる。"""
    r = client.post(
        "/api/wish",
        json={"prompt": "こんにちは", "session_id": "sess_test_abc"},
    )
    assert r.status_code == 200

    r2 = client.get("/api/sessions/sess_test_abc")
    assert r2.status_code == 200
    body = r2.json()
    assert body["session_id"] == "sess_test_abc"
    assert len(body["turns"]) == 1
    # CHITCHAT はエラーなしで完了する
    assert body["turns"][0]["error_code"] is None


def test_wish_session_id_via_header(client: TestClient) -> None:
    """`X-Thor-Session-Id` ヘッダでもセッションが特定される。"""
    r = client.post(
        "/api/wish",
        headers={"X-Thor-Session-Id": "sess_hdr_1"},
        json={"prompt": "こんにちは"},
    )
    assert r.status_code == 200

    r2 = client.get("/api/sessions/sess_hdr_1")
    assert r2.status_code == 200
    assert len(r2.json()["turns"]) == 1

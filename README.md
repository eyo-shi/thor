# Thor

Cloudera AI Agent Studio 上で動作する自然言語データ分析マルチエージェント。ユーザーが日本語 / 英語で問い合わせるだけで **S3 → Iceberg 取り込み / テーブルサマリー生成 / Cloudera Data Visualization ダッシュボード作成** を一気通貫に実行する。3 ペイン Web UI (React + Vite) + FastAPI バックエンドを Cloudera AI Workbench Application として配信する。

セマンティックレイヤは **Apache Ossie**（YAML 仕様）を採用し、`semantic/` 配下で Git 管理する。

## 構成

```
thor/           Python パッケージ (Crew + FastAPI)
  router/       Router Crew (intent 分類 / ディスパッチ)
  ingestion/    Ingestion Crew (S3 → Iceberg)
  analytics/    Analytics Crew (サマリー / ダッシュボード)
  semantic/     Ossie YAML 読み書き / 検索
  session/      セッション、リクエスト、メモリ
  transport/    Knox JWT / HTTP / ロギング共通層
  tools/        CrewAI Tool 実装
  api/          FastAPI (SSE ストリーム、静的 SPA 配信)
  demo/         デモ用ウォームアップ

thor_ui/        React + TypeScript + Vite (3 ペイン UI)
  src/panes/
    TreePane/   左: iceberg / s3 エクスプローラ
    ResultPane/ 中央: Table / Dashboard / Summary / SQL / File のタブ
    ChatPane/   右: SSE ストリーム対応チャット

semantic/       Apache Ossie YAML の Git 管理領域
tests/          pytest
application.json  Cloudera AI Workbench Application マニフェスト
pyproject.toml
```

## 実装ステータス

現在: **骨格作成のみ**。実装は以下の順で進める (プランの実装優先順位より):

1. `thor.transport` (Knox JWT / HTTP / logging)
2. Trino / Iceberg / S3 Tool
3. `thor.ingestion.IngestionCrew` + `thor.semantic` 書き込み
4. `thor.analytics.AnalyticsCrew` (Summary)
5. `thor.analytics.AnalyticsCrew` (Dashboard, CDV Adapter)
6. `thor.router.RouterCrew` + Entity Memory
7. `thor.api` (FastAPI, `/api/wish` SSE ほか)
8. `thor_ui` の 3 ペイン中身
9. Excel ヘッダー検出の LLM 検証段
10. デモモード (`THOR_DEMO_MODE=warm`)
11. Workbench Application 登録

## 開発

### バックエンド

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest             # スモークテスト
uvicorn thor.api.main:app --reload   # (main.py 実装後)
```

### フロントエンド

```bash
cd thor_ui
npm ci
npm run dev        # http://localhost:5173  (API は /api を 127.0.0.1:8000 にプロキシ)
npm run build      # thor/api/static/ に SPA を出力
```

### Cloudera AI Workbench で配信

1. `cd thor_ui && npm ci && npm run build`
2. Workbench Application として `python -m thor.api.main` を起動 (`CDSW_APP_PORT` を uvicorn に渡す)
3. FastAPI が `/api/*` と `/` (SPA) を配信

## ライセンス

Apache-2.0

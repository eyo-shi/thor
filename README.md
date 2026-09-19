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
agent_studio_manifest/     Agent Studio 用 tools/agents/crews YAML (自動生成)
.project-metadata.yaml     Cloudera AI Workbench (AMP) マニフェスト
application.json           Workbench Application 起動定義
pyproject.toml
```

## 実装ステータス

MVP 実装完了。以下の全レイヤが `main` に入っている:

| モジュール | 内容 |
|---|---|
| `thor.transport` | Knox JWT / user_context / HTTP client / structlog / `BaseThorTool` |
| `thor.tools` | S3 / Trino / フォーマット判定 / Excel ヘッダー検出 (heuristic + LLM 検証) / CDV |
| `thor.ingestion.IngestionCrew` | S3 → Iceberg → Ossie の 8 タスク Sequential パイプライン |
| `thor.analytics.AnalyticsCrew` | Summary パス (2 タスク) + Dashboard パス (4 タスク、VizPlanner + CDV) |
| `thor.router.RouterCrew` | intent 分類 + Python レベルディスパッチ |
| `thor.api` | FastAPI (`/api/wish` SSE / `/api/catalog` / `/api/files/preview` / `/api/query` / `/api/artifacts` / SPA mount) |
| `thor_ui` | React + Vite 3 ペイン UI (TreePane / ResultPane 5 タブ / ChatPane SSE) |
| `thor.demo.warm` | `THOR_DEMO_MODE=warm` のキャンド応答フォールバック |
| `thor.manifest` | Python 定義から Agent Studio manifest (`tools.yaml` / `agents.yaml` / `crews.yaml`) を自動生成 |
| `.project-metadata.yaml` | Cloudera AI Workbench (AMP) 登録用マニフェスト |

未着手 (v2 候補): TablePreview の仮想スクロール、Ossie バッジの実 YAML 判定、セッション履歴の hydrate、Analytics の Hierarchical Process 移行。

## 開発

### バックエンド

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest             # スモークテスト
uvicorn thor.api.main:app --reload   # http://127.0.0.1:8000
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

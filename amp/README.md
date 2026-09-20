# AMP セットアップスクリプト

Cloudera AI Workbench の AMP カタログから 1 クリックで Thor を起動するための
セットアップ手順を、順番に並べたスクリプト群。`.project-metadata.yaml` の
`tasks:` から順に参照される。

| # | Script | 内容 |
|---|---|---|
| 1 | `01_install_python_deps.py` | `pip install -e .[dev]` で thor パッケージを editable install |
| 2 | `02_build_ui.py` | nvm で Node.js 20 を自動導入 → `npm ci` / `npm run build` → `thor/api/static/` |
| 3 | `03_seed_semantic.py` | `semantic/{datasets,metrics,...}` を用意し、Git 初期化 (既存 repo は no-op) |
| 4 | `04_verify_manifest.py` | `python -m thor.manifest --check` で Python ⇄ YAML drift を検出 |
| 5 | `05_start_application.py` | env 正規化後に FastAPI + uvicorn を Workbench Application として起動 |

各スクリプトは失敗時に非 0 で exit し、Workbench の AMP セットアップ画面に
エラーを表示する。ローカルで手動でも `python amp/0N_*.py` として実行可能。

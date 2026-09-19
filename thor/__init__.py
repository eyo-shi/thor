"""Thor — Cloudera AI Agent Studio 上の Genie 相当マルチエージェント。

サブパッケージ:
    thor.router      Router Crew (intent 分類 / ディスパッチ)
    thor.ingestion   Ingestion Crew (S3 → Iceberg 取り込み)
    thor.analytics   Analytics Crew (サマリー / ダッシュボード)
    thor.semantic    Apache Ossie YAML の読み書き / 検索
    thor.session     セッション、リクエスト、メモリ
    thor.transport   認証・HTTP・ロギングの共通層
    thor.tools       各種 CrewAI Tool 実装
    thor.api         FastAPI バックエンド
    thor.demo        デモ用ウォームアップ / フォールバック
"""

__version__ = "0.0.1"

"""FastAPI バックエンド (UI ⇄ Crew の橋渡し)。

エントリポイント:

    ``python -m thor.api.main``  または  ``thor-api``

公開する主なオブジェクト:

* :func:`thor.api.main.create_app` — FastAPI アプリのファクトリ
* :func:`thor.api.main.main` — uvicorn 起動関数 (pyproject の script entry)

エンドポイントは全て ``/api/*`` プレフィックスに置く。SPA は ``/`` で
``thor/api/static/`` から静的配信される (存在すれば)。
"""

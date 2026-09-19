# semantic/ — Apache Ossie セマンティックレイヤ

Thor が Text-to-SQL / ダッシュボード / サマリー生成に用いる **Apache Ossie** の YAML 定義を Git 管理するディレクトリ。Cloudera AI Workbench プロジェクトから同期する想定。

## 構造

| ディレクトリ | 用途 |
|---|---|
| `datasets/<catalog>/<schema>/<table>.yaml` | 1 テーブル = 1 ファイル。Ingestion Crew が自動ドラフトし、レビュー後にマージ。 |
| `metrics/<domain>.yaml` | 横断メトリクス定義。 |
| `relationships/<domain>.yaml` | テーブル間結合定義。 |
| `prompts/few_shot_examples.yaml` | Text-to-SQL の Few-shot サンプル。 |
| `index/embeddings.parquet` | dataset YAML のベクター化キャッシュ (自動生成、Git LFS または `.gitignore` 対象を検討)。 |

## Ossie YAML 最小テンプレート

```yaml
version: 1
dataset:
  name: <table>
  fq_name: <catalog>.<schema>.<table>
  description: "<自然言語>"
  owner: <user>
  source:
    type: s3
    path: s3://.../<file>
  dimensions:
    - { name: region, type: string }
  measures:
    - { name: revenue, type: decimal(18,2), default_aggregation: sum }
  relationships: []
  sample_queries:
    - question: "地域別月次売上"
      sql: "SELECT ..."
```

自動生成のロジックは `thor.ingestion.tasks.draft_ossie_task`、読み書きは `thor.semantic` にある。

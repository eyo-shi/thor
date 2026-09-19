/**
 * 左ペイン: iceberg カタログ + s3 バケットの 2 ルート構成のエクスプローラ。
 *
 * 実装ポリシー:
 * - 検索は横断で `filter` する (react-arborist の filter プロパティ)
 * - ノードクリックで:
 *   - table  -> Table Preview タブを開く
 *   - s3 obj -> File Preview タブを開く
 * - 右クリックメニュー (NodeMenu) から ChatPane にプロンプトを流し込む
 *
 * S3 バケット候補は環境依存 (Vite の環境変数 `VITE_THOR_S3_BUCKETS` を
 * カンマ区切りで受ける)。空なら "demo-bucket" ひとつだけ列挙する。
 */
import { useMemo, useState } from "react";
import { CatalogTree } from "./CatalogTree";
import { S3Tree } from "./S3Tree";

function parseBuckets(): string[] {
  const raw = (import.meta.env.VITE_THOR_S3_BUCKETS as string | undefined) ?? "";
  const list = raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  return list.length ? list : ["demo-bucket"];
}

export function TreePane() {
  const [filter, setFilter] = useState("");
  const buckets = useMemo(parseBuckets, []);

  return (
    <div className="tree-pane">
      <div className="pane-header">
        <span>Explorer</span>
      </div>
      <div className="tree-search">
        <input
          type="search"
          placeholder="Search…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
      </div>
      <div className="pane-body tree-body">
        <section className="tree-section">
          <h3>iceberg</h3>
          <CatalogTree catalog="iceberg" filter={filter} />
        </section>
        <section className="tree-section">
          <h3>s3</h3>
          {buckets.map((b) => (
            <S3Tree key={b} bucket={b} filter={filter} />
          ))}
        </section>
      </div>
    </div>
  );
}

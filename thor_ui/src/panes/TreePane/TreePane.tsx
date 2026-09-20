/**
 * 左ペイン: データ Explorer（Tables / Storage 切替）。
 *
 * 上部アイコンで Tables (Explore) / Storage (S3) を切替。
 * Tables: 検索 + フラットテーブル一覧 (ホバーでカラム、ダブルクリックで中央表示)
 * Storage: S3 階層
 */
import { useState } from "react";
import { ExploreView } from "./ExploreView";
import { IconStorage, IconTables } from "./ExplorerIcons";
import { StorageView } from "./StorageView";

export type ExplorerMode = "tables" | "storage";

export function TreePane() {
  const [mode, setMode] = useState<ExplorerMode>("tables");
  const [filter, setFilter] = useState("");

  return (
    <div className="tree-pane">
      <div className="explorer-toolbar">
        <button
          type="button"
          className={
            "explorer-toolbar__btn" +
            (mode === "tables" ? " explorer-toolbar__btn--active" : "")
          }
          aria-label="Tables"
          title="Tables"
          onClick={() => setMode("tables")}
        >
          <IconTables active={mode === "tables"} />
        </button>
        <button
          type="button"
          className={
            "explorer-toolbar__btn" +
            (mode === "storage" ? " explorer-toolbar__btn--active" : "")
          }
          aria-label="Storage"
          title="Storage"
          onClick={() => setMode("storage")}
        >
          <IconStorage active={mode === "storage"} />
        </button>
      </div>

      {mode === "tables" && (
        <div className="explorer-search">
          <input
            type="search"
            placeholder="Search SQL tables…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          <span className="explorer-search__icon" aria-hidden="true">
            ⌕
          </span>
        </div>
      )}

      <div className="tree-body explorer-body">
        {mode === "tables" ? (
          <ExploreView filter={filter} />
        ) : (
          <StorageView />
        )}
      </div>
    </div>
  );
}

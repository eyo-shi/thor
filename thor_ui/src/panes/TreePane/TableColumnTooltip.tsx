/**
 * テーブル行ホバー時にカラム定義をツールチップ表示する。
 */
import { useColumns } from "../../api/catalog";
import type { ColumnEntry } from "../../types";

interface Props {
  fq: string;
  anchor: DOMRect;
}

export function TableColumnTooltip({ fq, anchor }: Props) {
  const { data, isLoading, error } = useColumns(fq);
  const columns = data?.columns ?? [];

  const left = Math.min(anchor.right + 8, window.innerWidth - 280);
  const top = Math.min(anchor.top, window.innerHeight - 240);

  return (
    <div
      className="table-column-tooltip"
      style={{ top, left }}
      role="tooltip"
    >
      <div className="table-column-tooltip__title">{fq.split(".").pop()}</div>
      {isLoading && <p className="table-column-tooltip__muted">読み込み中…</p>}
      {error && (
        <p className="table-column-tooltip__muted">カラム取得に失敗</p>
      )}
      {!isLoading && !error && columns.length === 0 && (
        <p className="table-column-tooltip__muted">カラムなし</p>
      )}
      {!isLoading && !error && columns.length > 0 && (
        <ul className="table-column-tooltip__list">
          {columns.map((c) => (
            <li key={c.column_name}>
              <span className="table-column-tooltip__name">{c.column_name}</span>
              <span className="table-column-tooltip__type">{formatType(c)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function formatType(c: ColumnEntry): string {
  const nullable = c.is_nullable?.toUpperCase() === "YES" ? "?" : "";
  return `${c.data_type}${nullable}`;
}

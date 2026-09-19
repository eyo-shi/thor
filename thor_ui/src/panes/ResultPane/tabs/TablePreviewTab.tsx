/**
 * Table Preview タブ。
 * ref.fq = "catalog.schema.table" から SELECT * ... LIMIT 100 を投げて描画。
 * TanStack Table を使いつつ、まずは軽量な自前 <table> で描画する
 * (仮想スクロールは v2 で入れる)。
 */
import { useQuery } from "@tanstack/react-query";
import { fetchTablePreview } from "../../../api/query";
import type { TabDescriptor } from "../../../types";

interface Props {
  tab: TabDescriptor;
}

export function TablePreviewTab({ tab }: Props) {
  const fq = String(tab.ref.fq ?? "");
  const { data, isLoading, error } = useQuery({
    queryKey: ["table-preview", fq],
    enabled: !!fq,
    queryFn: () => fetchTablePreview(fq, 100),
  });

  if (!fq) return <p className="placeholder">fq が不正</p>;
  if (isLoading) return <p className="placeholder">読み込み中…</p>;
  if (error) {
    return (
      <div className="tab-error">
        <strong>Table Preview 取得に失敗</strong>
        <pre>{String((error as any).message ?? error)}</pre>
      </div>
    );
  }
  if (!data) return <p className="placeholder">no data</p>;

  return (
    <div className="tab-content">
      <div className="tab-meta">
        <code>{fq}</code>
        <span className="meta-badge">{data.rows.length} rows</span>
        {data.truncated && <span className="meta-badge meta-badge--warn">truncated</span>}
      </div>
      <div className="table-scroll">
        <table className="preview-table">
          <thead>
            <tr>
              {data.columns.map((c) => (
                <th key={c.name}>
                  <div className="th-name">{c.name}</div>
                  <div className="th-type">{c.type}</div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row, i) => (
              <tr key={i}>
                {row.map((v, j) => (
                  <td key={j}>{renderCell(v)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function renderCell(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

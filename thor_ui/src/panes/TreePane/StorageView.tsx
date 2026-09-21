/**
 * ストレージ (S3) ビュー — バケット配下の階層を表示。
 */
import { useMemo } from "react";
import { S3Tree } from "./S3Tree";

function parseBuckets(): string[] {
  const raw = (import.meta.env.VITE_THOR_S3_BUCKETS as string | undefined) ?? "";
  const list = raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  return list.length ? list : ["demo-bucket"];
}

interface StorageViewProps {
  filter: string;
}

export function StorageView({ filter }: StorageViewProps) {
  const buckets = useMemo(parseBuckets, []);

  return (
    <div className="explorer-view explorer-view--storage">
      <div className="explorer-section-head">
        <span className="explorer-section-title">Storage</span>
        <span className="explorer-section-count">({buckets.length})</span>
      </div>
      <div className="explorer-storage-body">
        {buckets.map((b) => (
          <S3Tree key={b} bucket={b} filter={filter} />
        ))}
      </div>
    </div>
  );
}

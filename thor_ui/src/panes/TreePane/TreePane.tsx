/**
 * 左ペイン: iceberg カタログと S3 バケットの 2 ルート構成のエクスプローラ。
 * 実装は後続タスクで CatalogTree / S3Tree に分割する。
 */
export function TreePane() {
  return (
    <div className="tree-pane">
      <div className="pane-header">
        <span>Explorer</span>
      </div>
      <div className="pane-body">
        <p className="placeholder">iceberg / s3 tree — TODO</p>
      </div>
    </div>
  );
}

/**
 * Dashboard タブ: CDV の URL を iframe で埋め込む。
 * ref.url が絶対 URL、無ければ ref.dashboard_path (相対) から組み立てる。
 */
import type { TabDescriptor } from "../../../types";

interface Props {
  tab: TabDescriptor;
}

export function DashboardTab({ tab }: Props) {
  const url = String(tab.ref.url ?? tab.ref.dashboard_url ?? tab.ref.dashboard_path ?? "");
  if (!url) {
    return (
      <div className="tab-content">
        <p className="placeholder">Dashboard URL が未設定</p>
      </div>
    );
  }
  return (
    <div className="tab-content tab-content--flush">
      <iframe
        title={tab.title}
        src={url}
        className="dashboard-iframe"
        allow="clipboard-write"
      />
    </div>
  );
}

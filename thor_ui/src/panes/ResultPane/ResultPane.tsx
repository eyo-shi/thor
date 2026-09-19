/**
 * 中央ペイン: タブバー + タブ内容 (Table / Dashboard / Summary / SQL / File)。
 * タブ状態は zustand の useTabStore に集約。
 */
import { useTabStore } from "../../stores/tabStore";
import { TabBar } from "./TabBar";
import { DashboardTab } from "./tabs/DashboardTab";
import { FilePreviewTab } from "./tabs/FilePreviewTab";
import { SQLTab } from "./tabs/SQLTab";
import { SummaryTab } from "./tabs/SummaryTab";
import { TablePreviewTab } from "./tabs/TablePreviewTab";

export function ResultPane() {
  const tabs = useTabStore((s) => s.tabs);
  const activeId = useTabStore((s) => s.activeId);
  const active = tabs.find((t) => t.id === activeId) ?? null;

  return (
    <div className="result-pane">
      <TabBar />
      <div className="pane-body result-body">
        {!active && (
          <div className="welcome">
            <h2>ようこそ Thor へ</h2>
            <p>
              左のツリーからテーブルや S3
              オブジェクトを選ぶか、右のチャットで指示してください。
            </p>
          </div>
        )}
        {active && <TabRenderer tabId={active.id} />}
      </div>
    </div>
  );
}

function TabRenderer({ tabId }: { tabId: string }) {
  const tab = useTabStore((s) => s.tabs.find((t) => t.id === tabId));
  if (!tab) return null;
  switch (tab.kind) {
    case "table_preview":
      return <TablePreviewTab tab={tab} />;
    case "dashboard":
      return <DashboardTab tab={tab} />;
    case "summary":
      return <SummaryTab tab={tab} />;
    case "sql":
      return <SQLTab tab={tab} />;
    case "file_preview":
      return <FilePreviewTab tab={tab} />;
    default:
      return <p className="placeholder">未対応のタブ種別</p>;
  }
}

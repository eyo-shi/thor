import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { AppHeader } from "./components/AppHeader";
import { ChatPane } from "./panes/ChatPane/ChatPane";
import { ResultPane } from "./panes/ResultPane/ResultPane";
import { TreePane } from "./panes/TreePane/TreePane";

/**
 * Thor ワークスペース骨格 (3 ペイン)。
 *   上: グローバルヘッダー (Cloudera ロゴ + Thor)
 *   左: TreePane    (iceberg / s3 エクスプローラ)
 *   中央: ResultPane (テーブル / Visual / Summary 等)
 *   右: ChatPane     (上: 履歴・返信 / 下: 入力欄)
 */
export default function App() {
  return (
    <div className="app-root">
      <AppHeader />
      <div className="app-main">
        <PanelGroup direction="horizontal" autoSaveId="thor-panes-3col">
          <Panel defaultSize={20} minSize={12} className="pane pane--left">
            <TreePane />
          </Panel>
          <PanelResizeHandle className="pane-handle" />
          <Panel defaultSize={55} minSize={30} className="pane pane--center">
            <ResultPane />
          </Panel>
          <PanelResizeHandle className="pane-handle" />
          <Panel defaultSize={25} minSize={18} className="pane pane--right">
            <ChatPane />
          </Panel>
        </PanelGroup>
      </div>
    </div>
  );
}

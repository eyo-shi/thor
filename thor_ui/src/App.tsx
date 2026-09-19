import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { ChatPane } from "./panes/ChatPane/ChatPane";
import { ResultPane } from "./panes/ResultPane/ResultPane";
import { TreePane } from "./panes/TreePane/TreePane";

/**
 * Thor 3 ペイン骨格。
 *   左: TreePane   (iceberg / s3 のエクスプローラ)
 *   中: ResultPane (タブ切替の結果表示)
 *   右: ChatPane   (エージェント対話)
 * デフォルト比率 20% / 55% / 25%、最小幅制限あり。
 */
export default function App() {
  return (
    <div className="app-root">
      <PanelGroup direction="horizontal" autoSaveId="thor-panes">
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
  );
}

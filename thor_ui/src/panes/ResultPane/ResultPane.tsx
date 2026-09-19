/**
 * 中央ペイン: タブ切替の結果表示 (Table / Dashboard / Summary / SQL / File)。
 * 実装は後続タスクで TabBar と各 Tab コンポーネントに分割する。
 */
export function ResultPane() {
  return (
    <div className="result-pane">
      <div className="pane-header">
        <span>Results</span>
      </div>
      <div className="pane-body">
        <p className="placeholder">ようこそ Thor へ</p>
      </div>
    </div>
  );
}

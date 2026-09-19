/**
 * 右ペイン: エージェント対話 (SSE ストリームによるサブステップ表示)。
 * 実装は後続タスクで MessageList / StepIndicator / PromptInput / useWishStream に分割する。
 */
export function ChatPane() {
  return (
    <div className="chat-pane">
      <div className="pane-header">
        <span>Chat</span>
      </div>
      <div className="pane-body">
        <p className="placeholder">Ask Thor…</p>
      </div>
      <div className="pane-footer">
        <textarea placeholder="prompt" disabled />
        <button disabled>Send ▶</button>
      </div>
    </div>
  );
}

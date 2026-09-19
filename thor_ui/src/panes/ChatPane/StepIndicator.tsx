/**
 * 現在ターンの Router/Crew 進捗を SSE `event: step` から可視化する。
 * 完了 (status=done) は緑、running は青のパルス、error は赤、skipped はグレー。
 */
import { useChatStore } from "../../stores/chatStore";

const STATUS_ICON: Record<string, string> = {
  running: "◌",
  done: "●",
  skipped: "◦",
  error: "✕",
};

export function StepIndicator() {
  const steps = useChatStore((s) => s.steps);
  const streaming = useChatStore((s) => s.streaming);
  if (steps.length === 0 && !streaming) return null;
  return (
    <div className="step-indicator">
      {steps.map((s, i) => (
        <div
          key={i}
          className={"step-row step-row--" + s.status}
          title={s.message}
        >
          <span className="step-icon">{STATUS_ICON[s.status] ?? "•"}</span>
          <span className="step-agent">{s.agent}</span>
          <span className="step-message">{s.message}</span>
        </div>
      ))}
    </div>
  );
}

/**
 * タブバー。× でタブを閉じる。ドラッグ並び替えは v2 で。
 * radix-ui/react-tabs は使わず素直に div + button で作る (背景が動的で軽量なため)。
 */
import { useTabStore } from "../../stores/tabStore";

const KIND_ICON: Record<string, string> = {
  table_preview: "▤",
  dashboard: "📊",
  summary: "📝",
  sql: "≡",
  file_preview: "📄",
};

export function TabBar() {
  const tabs = useTabStore((s) => s.tabs);
  const activeId = useTabStore((s) => s.activeId);
  const setActive = useTabStore((s) => s.setActive);
  const closeTab = useTabStore((s) => s.closeTab);

  if (tabs.length === 0) {
    return (
      <div className="tab-bar tab-bar--empty">
        <span className="placeholder">no tabs</span>
      </div>
    );
  }
  return (
    <div className="tab-bar">
      {tabs.map((t) => (
        <div
          key={t.id}
          className={
            "tab-chip" + (t.id === activeId ? " tab-chip--active" : "")
          }
          onClick={() => setActive(t.id)}
        >
          <span className="tab-chip-icon">{KIND_ICON[t.kind] ?? "•"}</span>
          <span className="tab-chip-title" title={t.title}>
            {t.title}
          </span>
          <button
            className="tab-chip-close"
            onClick={(e) => {
              e.stopPropagation();
              closeTab(t.id);
            }}
            aria-label="Close tab"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}

/**
 * TreePane 用の軽量右クリックメニュー。
 * ラジックスの Menu を使わないのは、ノードごとに動的な位置指定を素直にしたいため。
 */
import { useEffect } from "react";

export interface NodeMenuItem {
  label: string;
  onSelect: () => void;
}

interface NodeMenuProps {
  x: number;
  y: number;
  items: NodeMenuItem[];
  onClose: () => void;
}

export function NodeMenu({ x, y, items, onClose }: NodeMenuProps) {
  // Escape / 外側クリックで閉じる
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="node-menu"
      style={{ top: y, left: x }}
      onClick={(e) => e.stopPropagation()}
    >
      {items.map((it) => (
        <button
          key={it.label}
          className="node-menu-item"
          onClick={() => {
            it.onSelect();
            onClose();
          }}
        >
          {it.label}
        </button>
      ))}
    </div>
  );
}

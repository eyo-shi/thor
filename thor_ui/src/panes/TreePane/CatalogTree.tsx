/**
 * iceberg カタログのブラウザ。
 *
 * データ構造:
 *   catalog (root)
 *     └ schema (lazy)
 *          └ table (leaf, click で TablePreviewTab を開く)
 *
 * react-arborist に渡す tree は state で保持し、schema を開くたびに
 * `useTables` で子を差し込む (レイジーロード)。
 */
import { useMemo, useState, type MouseEvent as ReactMouseEvent } from "react";
import { useSchemas, useTables } from "../../api/catalog";
import { fetchTablePreview } from "../../api/query";
import { useTabStore } from "../../stores/tabStore";
import { useChatStore } from "../../stores/chatStore";
import { NodeMenu } from "./NodeMenu";

interface CatalogTreeProps {
  catalog: string;
  filter: string;
}

type NodeKind = "schema" | "table";
interface CTNode {
  id: string;
  name: string;
  kind: NodeKind;
  fq?: string;
  children?: CTNode[];
}

export function CatalogTree({ catalog, filter }: CatalogTreeProps) {
  const { data, isLoading, error } = useSchemas(catalog);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [menuFor, setMenuFor] = useState<{
    id: string;
    x: number;
    y: number;
    node: CTNode;
  } | null>(null);

  const openTab = useTabStore((s) => s.openTab);
  const setPendingPrompt = useChatStore((s) => s.setPendingPrompt);

  const filteredSchemas = useMemo(() => {
    const schemas = data?.schemas ?? [];
    if (!filter) return schemas;
    const f = filter.toLowerCase();
    return schemas.filter((s) => s.toLowerCase().includes(f));
  }, [data, filter]);

  function toggle(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function openTablePreview(fq: string) {
    // 事前に fetch は不要 — タブ側で useQuery が走る
    openTab({
      title: fq.split(".").slice(-1)[0] ?? fq,
      kind: "table_preview",
      ref: { fq },
      dedupeKey: `table:${fq}`,
    });
    // すぐに fetch を走らせて Reset-and-cache 効果を得る (エラー時は tab 内で表示)
    fetchTablePreview(fq, 100).catch(() => {
      /* タブ側で再度表示する */
    });
  }

  if (isLoading) return <p className="placeholder">Loading schemas…</p>;
  if (error) return <p className="tree-error">スキーマ取得に失敗</p>;

  return (
    <ul className="tree-list" onClick={() => setMenuFor(null)}>
      {filteredSchemas.map((schema) => {
        const sid = `${catalog}.${schema}`;
        const isOpen = expanded.has(sid);
        return (
          <li key={sid} className="tree-node">
            <div
              className="tree-row tree-row--schema"
              onClick={() => toggle(sid)}
            >
              <span className="tree-caret">{isOpen ? "▾" : "▸"}</span>
              <span className="tree-label">{schema}</span>
            </div>
            {isOpen && (
              <SchemaTables
                catalog={catalog}
                schema={schema}
                filter={filter}
                onOpen={(fq) => openTablePreview(fq)}
                onMenu={(e, node) => {
                  e.preventDefault();
                  setMenuFor({ id: node.id, x: e.clientX, y: e.clientY, node });
                }}
              />
            )}
          </li>
        );
      })}
      {menuFor && menuFor.node.kind === "table" && (
        <NodeMenu
          x={menuFor.x}
          y={menuFor.y}
          onClose={() => setMenuFor(null)}
          items={[
            {
              label: "Preview (sample 100 rows)",
              onSelect: () => openTablePreview(menuFor.node.fq!),
            },
            {
              label: "サマリーを作って",
              onSelect: () =>
                setPendingPrompt(
                  `テーブル ${menuFor.node.fq} のサマリーを作って`,
                ),
            },
            {
              label: "ダッシュボードを作って",
              onSelect: () =>
                setPendingPrompt(
                  `テーブル ${menuFor.node.fq} からダッシュボードを作って`,
                ),
            },
          ]}
        />
      )}
    </ul>
  );
}

interface SchemaTablesProps {
  catalog: string;
  schema: string;
  filter: string;
  onOpen: (fq: string) => void;
  onMenu: (e: ReactMouseEvent, node: CTNode) => void;
}

function SchemaTables({
  catalog,
  schema,
  filter,
  onOpen,
  onMenu,
}: SchemaTablesProps) {
  const { data, isLoading, error } = useTables(schema, catalog);
  if (isLoading) return <p className="placeholder tree-child">…</p>;
  if (error) return <p className="tree-error tree-child">読み込み失敗</p>;
  const tables = data?.tables ?? [];
  const f = filter.toLowerCase();
  const filtered = f ? tables.filter((t) => t.name.toLowerCase().includes(f)) : tables;
  return (
    <ul className="tree-child-list">
      {filtered.map((t) => {
        const node: CTNode = { id: t.fq, name: t.name, kind: "table", fq: t.fq };
        return (
          <li
            key={t.fq}
            className="tree-row tree-row--table"
            onClick={() => onOpen(t.fq)}
            onContextMenu={(e) => onMenu(e, node)}
          >
            <span className="tree-icon">▤</span>
            <span className="tree-label" title={t.fq}>
              {t.name}
            </span>
            <span className="tree-badge" title="Ossie YAML has been drafted">
              📜
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/**
 * テーブル Explore ビュー。スキーマ選択とフラットなテーブル一覧。
 * スキーマ選択 → フラットなテーブル一覧。検索・ホバーツールチップ・ダブルクリックで中央表示。
 */
import { useEffect, useMemo, useState, type MouseEvent as ReactMouseEvent } from "react";
import { SetupGuideError } from "../../api/client";
import { useSchemas, useTables } from "../../api/catalog";
import { fetchTablePreview } from "../../api/query";
import { useChatStore } from "../../stores/chatStore";
import { useTabStore } from "../../stores/tabStore";
import { IconDatabase, IconTableGrid } from "./ExplorerIcons";
import { NodeMenu } from "./NodeMenu";
import { TableColumnTooltip } from "./TableColumnTooltip";

const CATALOG = "iceberg";

interface ExploreViewProps {
  filter: string;
}

interface TableNode {
  id: string;
  name: string;
  fq: string;
}

export function ExploreView({ filter }: ExploreViewProps) {
  const { data, isLoading, error, refetch } = useSchemas(CATALOG);
  const [schema, setSchema] = useState<string | null>(null);
  const [autoSelected, setAutoSelected] = useState(false);
  const [hovered, setHovered] = useState<{ fq: string; rect: DOMRect } | null>(
    null,
  );
  const [menuFor, setMenuFor] = useState<{
    x: number;
    y: number;
    node: TableNode;
  } | null>(null);

  const openTab = useTabStore((s) => s.openTab);
  const setPendingPrompt = useChatStore((s) => s.setPendingPrompt);
  const setSetupError = useChatStore((s) => s.setSetupError);

  const schemas = data?.schemas ?? [];

  useEffect(() => {
    if (error instanceof SetupGuideError) {
      setSetupError(error);
    }
  }, [error, setSetupError]);

  // 初回のみ: default スキーマがあれば自動選択、なければ先頭
  useEffect(() => {
    if (autoSelected || schemas.length === 0) return;
    const preferred = schemas.find((s) => s.toLowerCase() === "default");
    setSchema(preferred ?? schemas[0] ?? null);
    setAutoSelected(true);
  }, [schemas, autoSelected]);

  function openTablePreview(fq: string) {
    openTab({
      title: fq.split(".").slice(-1)[0] ?? fq,
      kind: "table_preview",
      ref: { fq },
      dedupeKey: `table:${fq}`,
    });
    fetchTablePreview(fq, 100).catch(() => {
      /* タブ側で再表示 */
    });
  }

  if (isLoading) return <p className="explorer-placeholder">Loading…</p>;
  if (error instanceof SetupGuideError) {
    return (
      <p className="explorer-placeholder">
        Trino 未設定です。右ペインの設定手順を確認してください。
      </p>
    );
  }
  if (error) return <p className="explorer-error">スキーマ取得に失敗</p>;

  if (!schema) {
    return (
      <div className="explorer-view">
        <div className="explorer-section-head">
          <span className="explorer-section-title">Schemas</span>
          <span className="explorer-section-count">({schemas.length})</span>
        </div>
        <ul className="explorer-table-list">
          {schemas.map((s) => (
            <li key={s}>
              <button
                type="button"
                className="explorer-table-row"
                onClick={() => setSchema(s)}
              >
                <IconDatabase />
                <span className="explorer-table-name">{s}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  return (
    <div className="explorer-view" onClick={() => setMenuFor(null)}>
      <nav className="explorer-breadcrumb">
        <button
          type="button"
          className="explorer-breadcrumb__back"
          aria-label="スキーマ一覧へ戻る"
          onClick={() => setSchema(null)}
        >
          ‹
        </button>
        <IconDatabase />
        <span className="explorer-breadcrumb__label">{schema}</span>
      </nav>

      <SchemaTableList
        schema={schema}
        filter={filter}
        onOpen={openTablePreview}
        onHover={(fq, el) => {
          if (fq && el) setHovered({ fq, rect: el.getBoundingClientRect() });
          else setHovered(null);
        }}
        onMenu={(e, node) => {
          e.preventDefault();
          setMenuFor({ x: e.clientX, y: e.clientY, node });
        }}
        onRefresh={() => void refetch()}
      />

      {hovered && (
        <TableColumnTooltip fq={hovered.fq} anchor={hovered.rect} />
      )}

      {menuFor && (
        <NodeMenu
          x={menuFor.x}
          y={menuFor.y}
          onClose={() => setMenuFor(null)}
          items={[
            {
              label: "Preview (sample 100 rows)",
              onSelect: () => openTablePreview(menuFor.node.fq),
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
    </div>
  );
}

interface SchemaTableListProps {
  schema: string;
  filter: string;
  onOpen: (fq: string) => void;
  onHover: (fq: string | null, el: HTMLElement | null) => void;
  onMenu: (e: ReactMouseEvent, node: TableNode) => void;
  onRefresh: () => void;
}

function SchemaTableList({
  schema,
  filter,
  onOpen,
  onHover,
  onMenu,
  onRefresh,
}: SchemaTableListProps) {
  const { data, isLoading, error, refetch } = useTables(schema, CATALOG);

  const tables = useMemo(() => {
    const list = data?.tables ?? [];
    if (!filter.trim()) return list;
    const f = filter.toLowerCase();
    return list.filter(
      (t) =>
        t.name.toLowerCase().includes(f) ||
        t.fq.toLowerCase().includes(f),
    );
  }, [data, filter]);

  if (isLoading) return <p className="explorer-placeholder">Loading tables…</p>;
  if (error) return <p className="explorer-error">テーブル取得に失敗</p>;

  return (
    <>
      <div className="explorer-section-head">
        <span className="explorer-section-title">Tables</span>
        <span className="explorer-section-count">({tables.length})</span>
        <div className="explorer-section-actions">
          <button
            type="button"
            className="explorer-action-btn"
            aria-label="更新"
            title="更新"
            onClick={() => {
              void refetch();
              onRefresh();
            }}
          >
            ↻
          </button>
        </div>
      </div>
      <ul className="explorer-table-list">
        {tables.map((t) => {
          const node: TableNode = { id: t.fq, name: t.name, fq: t.fq };
          return (
            <li key={t.fq}>
              <button
                type="button"
                className="explorer-table-row"
                onDoubleClick={() => onOpen(t.fq)}
                onMouseEnter={(e) => onHover(t.fq, e.currentTarget)}
                onMouseLeave={() => onHover(null, null)}
                onContextMenu={(e) => onMenu(e, node)}
              >
                <IconTableGrid />
                <span className="explorer-table-name">{t.name}</span>
              </button>
            </li>
          );
        })}
        {tables.length === 0 && (
          <li className="explorer-placeholder">テーブルが見つかりません</li>
        )}
      </ul>
    </>
  );
}

/**
 * s3 バケットのブラウザ。
 *
 * データ構造 (すべて prefix 単位で lazy):
 *   bucket (root)
 *     └ subfolder (勾配区切り、末尾 '/' 付き)
 *         └ ... 再帰
 *     └ object (leaf, click で FilePreviewTab を開く)
 *
 * バケット直下だけを最初にロードし、subfolder を展開したら都度 useS3List。
 */
import { useState } from "react";
import { useS3List } from "../../api/files";
import { useTabStore } from "../../stores/tabStore";
import { useChatStore } from "../../stores/chatStore";
import { NodeMenu } from "./NodeMenu";

interface S3TreeProps {
  bucket: string;
  filter: string;
}

export function S3Tree({ bucket, filter }: S3TreeProps) {
  const [menuFor, setMenuFor] = useState<
    | {
        x: number;
        y: number;
        kind: "object";
        bucket: string;
        key: string;
        name: string;
      }
    | null
  >(null);
  const [rootExpanded, setRootExpanded] = useState(false);

  return (
    <ul className="tree-list" onClick={() => setMenuFor(null)}>
      <li className="tree-node">
        <div
          className="tree-row tree-row--schema"
          onClick={() => setRootExpanded((v) => !v)}
        >
          <span className="tree-caret">{rootExpanded ? "▾" : "▸"}</span>
          <span className="tree-label">{bucket}</span>
        </div>
        {rootExpanded && (
          <S3PrefixList
            bucket={bucket}
            prefix=""
            filter={filter}
            onContextObject={(x, y, key, name) =>
              setMenuFor({ x, y, kind: "object", bucket, key, name })
            }
          />
        )}
      </li>
      {menuFor && (
        <S3ObjectMenu
          x={menuFor.x}
          y={menuFor.y}
          bucket={menuFor.bucket}
          k={menuFor.key}
          name={menuFor.name}
          onClose={() => setMenuFor(null)}
        />
      )}
    </ul>
  );
}

// ---------------------- Recursive prefix listing ----------------------

interface S3PrefixListProps {
  bucket: string;
  prefix: string;
  filter: string;
  onContextObject: (x: number, y: number, key: string, name: string) => void;
}

function S3PrefixList({
  bucket,
  prefix,
  filter,
  onContextObject,
}: S3PrefixListProps) {
  const { data, isLoading, error } = useS3List(bucket, prefix);
  const [openSub, setOpenSub] = useState<Set<string>>(new Set());
  const openTab = useTabStore((s) => s.openTab);

  if (isLoading) return <p className="placeholder tree-child">…</p>;
  if (error) return <p className="tree-error tree-child">S3 一覧失敗</p>;

  const subs = data?.subfolders ?? [];
  const objs = data?.objects ?? [];
  const f = filter.toLowerCase();
  const filteredSubs = f
    ? subs.filter((s) => s.toLowerCase().includes(f))
    : subs;
  const filteredObjs = f
    ? objs.filter((o) => o.key.toLowerCase().includes(f))
    : objs;

  function toggleSub(p: string) {
    setOpenSub((prev) => {
      const next = new Set(prev);
      if (next.has(p)) next.delete(p);
      else next.add(p);
      return next;
    });
  }

  function openFilePreview(bucket: string, key: string) {
    const name = key.split("/").pop() || key;
    openTab({
      title: name,
      kind: "file_preview",
      ref: { bucket, key },
      dedupeKey: `file:${bucket}/${key}`,
    });
  }

  return (
    <ul className="tree-child-list">
      {filteredSubs.map((sp) => {
        const label = sp.slice(prefix.length).replace(/\/$/, "");
        const isOpen = openSub.has(sp);
        return (
          <li key={sp} className="tree-node">
            <div
              className="tree-row tree-row--schema"
              onClick={() => toggleSub(sp)}
            >
              <span className="tree-caret">{isOpen ? "▾" : "▸"}</span>
              <span className="tree-label" title={sp}>
                {label || sp}
              </span>
            </div>
            {isOpen && (
              <S3PrefixList
                bucket={bucket}
                prefix={sp}
                filter={filter}
                onContextObject={onContextObject}
              />
            )}
          </li>
        );
      })}
      {filteredObjs.map((o) => {
        const name = o.key.slice(prefix.length) || o.key;
        return (
          <li
            key={o.key}
            className="tree-row tree-row--table"
            onClick={() => openFilePreview(bucket, o.key)}
            onContextMenu={(e) => {
              e.preventDefault();
              onContextObject(e.clientX, e.clientY, o.key, name);
            }}
          >
            <span className="tree-icon">📄</span>
            <span className="tree-label" title={o.key}>
              {name}
            </span>
          </li>
        );
      })}
      {filteredSubs.length === 0 && filteredObjs.length === 0 && (
        <li className="placeholder tree-child">(empty)</li>
      )}
    </ul>
  );
}

// ---------------------- Object right-click menu ----------------------

interface S3ObjectMenuProps {
  x: number;
  y: number;
  bucket: string;
  k: string;
  name: string;
  onClose: () => void;
}

function S3ObjectMenu({ x, y, bucket, k, name, onClose }: S3ObjectMenuProps) {
  const openTab = useTabStore((s) => s.openTab);
  const setPendingPrompt = useChatStore((s) => s.setPendingPrompt);
  return (
    <NodeMenu
      x={x}
      y={y}
      onClose={onClose}
      items={[
        {
          label: `Preview: ${name}`,
          onSelect: () =>
            openTab({
              title: name,
              kind: "file_preview",
              ref: { bucket, key: k },
              dedupeKey: `file:${bucket}/${k}`,
            }),
        },
        {
          label: "このファイルを取り込んで",
          onSelect: () =>
            setPendingPrompt(
              `s3://${bucket}/${k} を取り込んでテーブルを作って`,
            ),
        },
      ]}
    />
  );
}

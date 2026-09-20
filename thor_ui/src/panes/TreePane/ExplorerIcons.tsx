/** Explorer 上部ツールバー用 SVG アイコン */

export function IconTables({ active }: { active?: boolean }) {
  return (
    <svg
      className={"explorer-icon" + (active ? " explorer-icon--active" : "")}
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <ellipse cx="12" cy="5" rx="8" ry="2.5" fill="currentColor" />
      <path
        d="M4 5v5c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5V5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
      />
      <path
        d="M4 10v5c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5v-5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
      />
    </svg>
  );
}

export function IconStorage({ active }: { active?: boolean }) {
  return (
    <svg
      className={"explorer-icon" + (active ? " explorer-icon--active" : "")}
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <path
        d="M4 6h16v12H4z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
      />
      <path
        d="M4 10h16M8 6V4h8v2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function IconTableGrid() {
  return (
    <svg className="explorer-table-icon" viewBox="0 0 16 16" aria-hidden="true">
      <rect x="2" y="2" width="12" height="12" rx="1" fill="none" stroke="currentColor" strokeWidth="1.25" />
      <path d="M2 6h12M2 10h12M6 2v12M10 2v12" stroke="currentColor" strokeWidth="1" />
    </svg>
  );
}

export function IconDatabase() {
  return (
    <svg className="explorer-breadcrumb-icon" viewBox="0 0 16 16" aria-hidden="true">
      <ellipse cx="8" cy="4" rx="5" ry="1.75" fill="currentColor" />
      <path d="M3 4v4c0 .97 2.24 1.75 5 1.75s5-.78 5-1.75V4" fill="none" stroke="currentColor" strokeWidth="1.1" />
      <path d="M3 8v4c0 .97 2.24 1.75 5 1.75s5-.78 5-1.75V8" fill="none" stroke="currentColor" strokeWidth="1.1" />
    </svg>
  );
}

export function IconSearch() {
  return (
    <svg className="explorer-search-icon" viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.25" />
      <path d="M10.5 10.5L14 14" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" />
    </svg>
  );
}

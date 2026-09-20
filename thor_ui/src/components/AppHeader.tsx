/**
 * グローバルヘッダー: Cloudera ロゴ + Thor プロダクト名。
 * 全画面共通のトップバー（Cloudera ロゴ + Thor）。
 */
export function AppHeader() {
  return (
    <header className="app-header">
      <div className="app-header__brand">
        <img
          src="/cloudera-logo.jpg"
          alt="Cloudera"
          className="app-header__logo"
          height={28}
        />
        <span className="app-header__divider" aria-hidden="true" />
        <span className="app-header__product">Thor</span>
      </div>
    </header>
  );
}

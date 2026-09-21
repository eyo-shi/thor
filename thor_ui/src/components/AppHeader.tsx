/**
 * グローバルヘッダー: Cloudera ロゴ。
 */
export function AppHeader() {
  return (
    <header className="app-header">
      <div className="app-header__brand">
        <img
          src="/cloudera-logo.png"
          srcSet="/cloudera-logo.png 1x, /cloudera-logo@2x.png 2x"
          alt="Cloudera"
          className="app-header__logo"
          width={231}
          height={28}
        />
      </div>
    </header>
  );
}

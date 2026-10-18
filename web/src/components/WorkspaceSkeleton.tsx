import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import type { Role } from '../operations';
import { ChhayaBrand } from './ChhayaBrand';
import { DemoBand, ModeSwitch } from './ModeSwitch';
import { RoleBadge, WorkspaceNav } from './SidebarLayout';
import { text, type UiLanguage } from '../lib/wardLocale';

// The workspace's frame while its first read is under way: the sidebar and top bar are drawn at once, and grey
// blocks stand where the page's cards will be, so the page does not open blank.

/** Grey stand-ins for a page's cards: a row of figures, then two cards of lines. */
export const SkeletonCards: React.FC<{ label: string }> = ({ label }) => (
  <div className="skeleton-page" role="status" aria-live="polite">
    <span className="sr-only">{label}</span>
    <div className="skeleton-row" aria-hidden="true">
      {[0, 1, 2, 3].map(i => <div key={i} className="card skeleton-card"><span className="skeleton-line short" /><span className="skeleton-line tall" /><span className="skeleton-line" /></div>)}
    </div>
    {[0, 1].map(i => (
      <div key={i} className="card skeleton-card" aria-hidden="true">
        <span className="skeleton-line short" />
        <span className="skeleton-line" />
        <span className="skeleton-line" />
        <span className="skeleton-line medium" />
      </div>
    ))}
  </div>
);

export const WorkspaceSkeleton: React.FC<{ role: Role; language: UiLanguage; error: string }> = ({ role, language: lang, error }) => {
  const location = useLocation();
  const view = role === 'crew' ? 'ward' : 'regional';
  // The menu keeps the area asked for in the address; a resident's case is not carried to another page.
  const query = new URLSearchParams(location.search);
  query.delete('resident');
  query.delete('case');
  const withScope = (path: string) => `${path}${query.size ? `?${query}` : ''}`;
  const loading = text(lang, 'Loading calls and visits…', 'कॉल और मुलाकातों की जानकारी आ रही है…');

  return (
    <div lang={lang} className={`app-container workspace-shell ops-app ${view === 'ward' ? 'ops-crew-app' : ''}`}>
      <div className="mobile-header">
        <Link to="/" className="sidebar-brand"><ChhayaBrand /></Link>
      </div>
      <aside className="sidebar" aria-label={text(lang, 'Workspace navigation', 'मुख्य मेनू')}>
        <div className="sidebar-header">
          <Link to="/" className="sidebar-brand"><ChhayaBrand /></Link>
          <RoleBadge role={view} lang={lang} />
        </div>
        <WorkspaceNav role={view} lang={lang} withScope={withScope} />
      </aside>
      <div className="main-wrapper">
        <div className="workspace-sticky">
          <header className="workspace-topbar">
            <div className="topbar-context">
              <span className="topbar-scope skeleton-line short" aria-hidden="true" />
            </div>
            {view === 'regional' && <div className="topbar-actions"><ModeSwitch /></div>}
          </header>
          <DemoBand />
        </div>
        <main className="content-pane">
          <div className="workspace-page">
            {error ? (
              <section className="card workspace-empty">
                <p className="ops-error" role="alert">{error}</p>
                <a href="/login">{text(lang, 'Sign in or reload the page', 'साइन इन करें या पेज दोबारा खोलें')}</a>
              </section>
            ) : <SkeletonCards label={loading} />}
          </div>
        </main>
      </div>
    </div>
  );
};

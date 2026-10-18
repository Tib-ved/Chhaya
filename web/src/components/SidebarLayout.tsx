import React, { Suspense, lazy, useState, useEffect, useRef } from 'react';
import { NavLink, Link, useLocation } from 'react-router-dom';
import { useWorkspace, useWorkspaceNavigate } from './WorkspaceContext';
import { WorkspaceFilters, WorkspaceEmpty, AssignedWard } from './WorkspaceTools';
import { ChhayaBrand } from './ChhayaBrand';
import { Sparkle } from './Sparkle';

// The assistant is read when its panel first opens.
const AskChhaya = lazy(() => import('./AskChhaya').then(m => ({ default: m.AskChhaya })));
import { localTime, setUiLanguage, text, type UiLanguage } from '../lib/wardLocale';
import { ModeSwitch, DemoBand } from './ModeSwitch';
import { openInDemo } from '../lib/mode';

interface SidebarLayoutProps {
  role: 'regional' | 'ward';
  children: React.ReactNode;
  breadcrumbs?: React.ReactNode;
  header?: React.ReactNode;
  filters?: boolean;
  narrow?: boolean;
}

export const SidebarLayout: React.FC<SidebarLayoutProps> = ({
  role,
  children,
  breadcrumbs,
  header,
  filters = false,
  narrow = false
}) => {
  const navigate = useWorkspaceNavigate();
  const workspace = useWorkspace();
  const { data, scopeLabel, session, withScope, language: lang } = workspace;
  const location = useLocation();
  const [mobilePath, setMobilePath] = useState<string | null>(null);
  const mobileOpen = mobilePath === location.pathname;
  const setMobileOpen = (open: boolean) => setMobilePath(open ? location.pathname : null);
  const menu = useRef<HTMLElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);

  const churuStats = { redOpen: data.cases.filter(c => c.tier === 'red' && c.status === 'open').length, open: data.counts.to_visit };
  const showEmpty = !data.has_data && !location.pathname.endsWith('/districts') && !location.pathname.endsWith('/agent-tester') && !location.pathname.endsWith('/weather');

  const handleLangChange = setUiLanguage;

  useEffect(() => {
    if (!mobileOpen) return;
    const controls = () => Array.from(menu.current?.querySelectorAll<HTMLElement>('a[href], button:not([disabled])') || []).filter(node => node.getClientRects().length);
    controls()[0]?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); setMobilePath(null); }
      if (event.key !== 'Tab') return;
      const nodes = controls(), first = nodes[0], last = nodes.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', keydown);
    const opener = menuButton.current;
    return () => { document.removeEventListener('keydown', keydown); opener?.focus(); };
  }, [mobileOpen]);

  // Ask Chhaya stays open across pages: the layout remounts on each page, so the choice is kept for the tab.
  const [askOpen, setAskOpenState] = useState(() => { try { return sessionStorage.getItem('chhaya_ask_open') === '1'; } catch { return false; } });
  const setAskOpen = (open: boolean) => { setAskOpenState(open); try { sessionStorage.setItem('chhaya_ask_open', open ? '1' : '0'); } catch { /* private window */ } };
  // A question sent from a resident's case opens the panel; it stays open once the question is taken.
  const showAsk = role === 'regional' && (askOpen || Boolean(workspace.askRequest));
  // Full view: the conversation over the page, for reading long answers, charts and tables.
  const [askExpanded, setAskExpandedState] = useState(() => { try { return sessionStorage.getItem('chhaya_ask_expanded') === '1'; } catch { return false; } });
  const setAskExpanded = (expanded: boolean) => { setAskExpandedState(expanded); try { sessionStorage.setItem('chhaya_ask_expanded', expanded ? '1' : '0'); } catch { /* private window */ } };
  const askButton = useRef<HTMLButtonElement>(null);
  const drawer = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!showAsk) return;
    // Escape leaves the full view first, then closes the panel.
    const keydown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      if (askExpanded) setAskExpanded(false); else setAskOpen(false);
    };
    document.addEventListener('keydown', keydown);
    return () => document.removeEventListener('keydown', keydown);
  }, [showAsk, askExpanded]);
  // The button is not rendered while the drawer is open: give it focus back once it is.
  const wasOpen = useRef(showAsk);
  useEffect(() => {
    if (wasOpen.current && !showAsk) askButton.current?.focus();
    wasOpen.current = showAsk;
  }, [showAsk]);
  const openAsk = () => { setAskOpen(true); requestAnimationFrame(() => drawer.current?.querySelector<HTMLElement>('h2')?.focus()); };
  const focus = { state: workspace.scope.state_id, district_id: workspace.scope.district_id, ward_id: workspace.scope.ward_id };


  return (
    <div lang={lang} className={`app-container workspace-shell ops-app ${role === 'ward' ? 'ops-crew-app' : ''}`}>
      {/* Mobile Top Header */}
      <div className="mobile-header">
        <Link to="/" className="sidebar-brand">
          <ChhayaBrand />
        </Link>
        <button
          type="button"
          onClick={() => setMobileOpen(!mobileOpen)}
          className="btn ghost mobile-menu-btn"
          aria-label={text(lang, 'Toggle navigation menu', 'मेनू खोलें या बंद करें')}
          aria-expanded={mobileOpen}
          aria-controls="workspace-navigation"
          ref={menuButton}
        >
          {mobileOpen ? text(lang, 'Close', 'बंद करें') : text(lang, 'Menu', 'मेनू')}
        </button>
      </div>

      {/* Persistent Left Sidebar */}
      {mobileOpen && <button className="workspace-menu-scrim" aria-label={text(lang, 'Close navigation', 'मेनू बंद करें')} tabIndex={-1} onClick={() => setMobileOpen(false)} />}
      <aside id="workspace-navigation" ref={menu} role={mobileOpen ? 'dialog' : undefined} aria-modal={mobileOpen || undefined} aria-label={text(lang, 'Workspace navigation', 'मुख्य मेनू')} className={`sidebar ${mobileOpen ? 'mobile-open' : ''}`}>
        <div className="sidebar-header">
          {mobileOpen && <button className="btn ghost" onClick={() => setMobileOpen(false)}>{text(lang, 'Close navigation', 'मेनू बंद करें')}</button>}
          <Link to="/" className="sidebar-brand">
            <ChhayaBrand />
          </Link>
          <RoleBadge role={role} lang={lang} />
        </div>

        {/* Navigation Sections */}
        <WorkspaceNav role={role} lang={lang} withScope={withScope} redOpen={churuStats.redOpen} toVisit={churuStats.open} onNavigate={() => setMobileOpen(false)} />

        {/* Sidebar Footer Controls */}
        <div className="sidebar-footer">
          <button
            type="button"
            className="sidebar-switch-btn"
            hidden={!session.demo}
            onClick={() => {
              if (role === 'regional') {
                navigate('/ward/overview');
              } else {
                navigate('/regional/overview');
              }
            }}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="17 1 21 5 17 9"/>
              <path d="M3 11V9a4 4 0 0 1 4-4h14"/>
              <polyline points="7 23 3 19 7 15"/>
              <path d="M21 13v2a4 4 0 0 1-4 4H3"/>
            </svg>
            <span>
              {role === 'regional' ? 'Switch to Field crew' : text(lang, 'Switch to Regional officer', 'अधिकारी का दृश्य खोलें')}
            </span>
          </button>

          <div className="sidebar-controls">
            <div className="lang-switch" role="group" aria-label="Language" hidden={role !== 'ward'}>
              <button
                type="button"
                className={lang === 'hi' ? 'active' : ''}
                onClick={() => handleLangChange('hi')}
              >
                हिन्दी
              </button>
              <button
                type="button"
                className={lang === 'en' ? 'active' : ''}
                onClick={() => handleLangChange('en')}
              >
                EN
              </button>
            </div>
            <Link to="/" className="exit-home-btn">
              {text(lang, 'Back to home', 'मुख्य पेज पर जाएँ')}
            </Link>
          </div>

          <div className="pilot-tag">
            {scopeLabel} · {data.counts.registered} {text(lang, 'residents in this view', 'निवासी')}
          </div>
        </div>
      </aside>

      {/* Main Content Pane */}
      <div className={`main-wrapper ${showAsk ? 'with-drawer' : ''}`} inert={mobileOpen}>
        {/* Sticky app bar: current warning and data freshness, with the demo-data line pinned under it */}
        <div className="workspace-sticky">
        <header className="workspace-topbar">
          <div className="topbar-context">
            <span className="topbar-scope">{scopeLabel}</span>
            <span className="topbar-updated">{text(lang, 'Updated', 'आख़िरी अपडेट')} {localTime(data.as_of, lang)}<button type="button" className="linkbtn" onClick={workspace.refresh}>{text(lang, 'Refresh', 'ताज़ा करें')}</button></span>
          </div>
          {role === 'regional' && (
            <div className="topbar-actions">
              <ModeSwitch />
              {!showAsk && (
                <button ref={askButton} type="button" className="ask-ai-btn" aria-expanded={false} aria-controls="assistant-drawer" onClick={openAsk}>
                  <Sparkle /><span>Ask Chhaya</span>
                </button>
              )}
            </div>
          )}
        </header>
        <DemoBand />
        </div>

        <main className={`content-pane ${narrow ? 'narrow' : ''}`}>
          {role === 'ward' && <AssignedWard />}
          {workspace.error && <p className="ops-error" role="alert">{lang === 'hi' ? 'जानकारी अपडेट नहीं हो सकी। ये आंकड़े पुराने हो सकते हैं।' : `${workspace.error} These figures may be out of date.`}</p>}
          <div className="workspace-page">
            {breadcrumbs && <nav className="crumbs" aria-label="Breadcrumbs">{breadcrumbs}</nav>}
            {header}
            {role === 'regional' && filters && <WorkspaceFilters />}
            {showEmpty ? <WorkspaceEmpty /> : children}
          </div>
        </main>
      </div>

      {role === 'regional' && <>
        <button type="button" className={`assistant-scrim ${showAsk ? 'open' : ''} ${askExpanded ? 'expanded' : ''}`} aria-label={askExpanded ? 'Leave full view' : 'Close Ask Chhaya'} tabIndex={-1} aria-hidden={!showAsk} onClick={() => askExpanded ? setAskExpanded(false) : setAskOpen(false)} />
        <div id="assistant-drawer" ref={drawer} className={`assistant-drawer ${showAsk ? 'open' : ''} ${askExpanded ? 'expanded' : ''}`} inert={!showAsk} aria-hidden={!showAsk} role={showAsk && askExpanded ? 'dialog' : undefined} aria-modal={showAsk && askExpanded ? true : undefined} aria-label={showAsk && askExpanded ? 'Ask Chhaya, full view' : undefined}>
          {showAsk && <Suspense fallback={<p className="muted" role="status">Opening Ask Chhaya…</p>}><AskChhaya focus={focus} focusLabel={scopeLabel} onCase={workspace.openCase} onHide={() => { setAskExpanded(false); setAskOpen(false); }} expanded={askExpanded} onToggleExpand={() => setAskExpanded(!askExpanded)} pending={workspace.askRequest} onPendingTaken={() => { setAskOpen(true); workspace.takeAskRequest(); }} /></Suspense>}
        </div>
      </>}
    </div>
  );
};

export const RoleBadge: React.FC<{ role: 'regional' | 'ward'; lang: UiLanguage }> = ({ role, lang }) => (
  <div className={`role-badge ${role}`}>
    {role === 'regional' ? (
      <>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
          <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
          <polyline points="9 22 9 12 15 12 15 22"/>
        </svg>
        <span>Regional Officer</span>
      </>
    ) : (
      <>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
          <path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>
          <circle cx="8.5" cy="7" r="4"/>
          <polyline points="17 11 19 13 23 9"/>
        </svg>
        <span>{text(lang, 'Field crew', 'फ़ील्ड टीम')}</span>
      </>
    )}
  </div>
);

/** The pages of a view, as the sidebar lists them; also drawn while the workspace loads (no counters then). */
export const WorkspaceNav: React.FC<{
  role: 'regional' | 'ward'; lang: UiLanguage; withScope: (path: string) => string;
  redOpen?: number; toVisit?: number; onNavigate?: () => void;
}> = ({ role, lang, withScope, redOpen = 0, toVisit = 0, onNavigate }) => (
  <nav className="sidebar-nav" aria-label="Main Navigation" onClick={onNavigate}>
    {role === 'regional' ? (
      <>
        <div className="nav-section-title">Regional team</div>
        <NavLink
          to={withScope('/regional/overview')}
          className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
        >
          <svg className="nav-icon" viewBox="0 0 24 24">
            <rect x="3" y="3" width="7" height="7"/>
            <rect x="14" y="3" width="7" height="7"/>
            <rect x="14" y="14" width="7" height="7"/>
            <rect x="3" y="14" width="7" height="7"/>
          </svg>
          <span>Overview</span>
        </NavLink>

        <NavLink
          to={withScope('/regional/weather')}
          className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
        >
          <svg className="nav-icon" viewBox="0 0 24 24">
            <circle cx="12" cy="12" r="4"/>
            <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/>
          </svg>
          <span>Weather</span>
        </NavLink>

        <NavLink
          to={withScope('/regional/districts')}
          className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
        >
          <svg className="nav-icon" viewBox="0 0 24 24">
            <polygon points="1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6"/>
            <line x1="8" y1="2" x2="8" y2="18"/>
            <line x1="16" y1="6" x2="16" y2="22"/>
          </svg>
          <span>Coverage</span>
        </NavLink>

        <NavLink
          to={withScope('/regional/critical')}
          className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
        >
          <svg className="nav-icon" viewBox="0 0 24 24">
            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
            <line x1="12" y1="9" x2="12" y2="13"/>
            <line x1="12" y1="17" x2="12.01" y2="17"/>
          </svg>
          <span>Urgent cases</span>
          {redOpen > 0 && (
            <span className="nav-counter alert">{redOpen}</span>
          )}
        </NavLink>

        <div className="nav-section-title mt-2">
          Calls and visits
        </div>

        <NavLink
          to={withScope('/regional/agent-tester')}
          className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
          onClick={e => { if (openInDemo(withScope('/regional/agent-tester'))) e.preventDefault(); }}
        >
          <svg className="nav-icon" viewBox="0 0 24 24">
            <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>
          </svg>
          <span>Try a call</span>
        </NavLink>

        <NavLink
          to={withScope('/regional/report')}
          className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
        >
          <svg className="nav-icon" viewBox="0 0 24 24">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
            <polyline points="14 2 14 8 20 8"/>
            <line x1="16" y1="13" x2="8" y2="13"/>
            <line x1="16" y1="17" x2="8" y2="17"/>
            <polyline points="10 9 9 9 8 9"/>
          </svg>
          <span>Situation report</span>
        </NavLink>

        <NavLink
          to={withScope('/regional/team')}
          className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
        >
          <svg className="nav-icon" viewBox="0 0 24 24">
            <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>
            <circle cx="9" cy="7" r="4"/>
            <path d="M23 21v-2a4 4 0 0 0-3-3.87"/>
            <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
          </svg>
          <span>Health workers</span>
        </NavLink>
      </>
    ) : (
      <>
        <div className="nav-section-title">{text(lang, 'Your work area', 'आपका कार्य क्षेत्र')}</div>
        <NavLink
          to={withScope('/ward/overview')}
          className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
        >
          <svg className="nav-icon" viewBox="0 0 24 24">
            <rect x="3" y="3" width="7" height="7"/>
            <rect x="14" y="3" width="7" height="7"/>
            <rect x="14" y="14" width="7" height="7"/>
            <rect x="3" y="14" width="7" height="7"/>
          </svg>
          <span>{lang === 'hi' ? 'मेरे क्षेत्र की स्थिति' : 'Ward overview'}</span>
        </NavLink>

        <NavLink
          to={withScope('/ward/visits')}
          className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
        >
          <svg className="nav-icon" viewBox="0 0 24 24">
            <path d="M9 11l3 3L22 4"/>
            <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>
          </svg>
          <span>{lang === 'hi' ? 'आज की मुलाकातें' : "Today's visits"}</span>
          {toVisit > 0 && <span className="nav-counter alert">{toVisit}</span>}
        </NavLink>

        <NavLink
          to={withScope('/ward/map')}
          className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
        >
          <svg className="nav-icon" viewBox="0 0 24 24">
            <circle cx="12" cy="10" r="3"/>
            <path d="M12 21.7C17.3 17 20 13 20 10a8 8 0 1 0-16 0c0 3 2.7 7 8 11.7z"/>
          </svg>
          <span>{lang === 'hi' ? 'नक्शा और केंद्र' : 'Ward map and centres'}</span>
        </NavLink>
      </>
    )}
  </nav>
);

export default SidebarLayout;

import React, { Suspense, lazy, useEffect } from 'react';
import { BrowserRouter, Route, Routes, Navigate, Outlet, useLocation } from 'react-router-dom';

// Landing & General Pages: the landing page comes with the app; every other page is read when first opened.
import { LandingPage } from './pages/Landing';
import { WorkspaceProvider } from './components/WorkspaceContext';
import { WorkspaceSkeleton } from './components/WorkspaceSkeleton';
import { ChhayaBrand } from './components/ChhayaBrand';
import { useUiLanguage } from './lib/wardLocale';

/** A page read on first use; `load` may be called early, so the page is fetched while the workspace's data is. */
function page<K extends string>(load: () => Promise<Record<K, React.ComponentType>>, name: K) {
  return { load, Page: lazy(() => load().then(m => ({ default: m[name] }))) };
}

const Evidence = page(() => import('./pages/Evidence'), 'EvidencePage');
const Call = page(() => import('./pages/Call'), 'CallPage');
const Alert = page(() => import('./pages/Alert'), 'AlertPage');
const Team = page(() => import('./pages/Team'), 'TeamPage');

// Regional Officer pages
const WORKSPACE_PAGES = {
  '/regional/overview': page(() => import('./pages/regional/RegionalOverview'), 'RegionalOverview'),
  '/regional/weather': page(() => import('./pages/regional/RegionalWeather'), 'RegionalWeather'),
  '/regional/districts': page(() => import('./pages/regional/RegionalDistricts'), 'RegionalDistricts'),
  '/regional/critical': page(() => import('./pages/regional/RegionalCritical'), 'RegionalCritical'),
  '/regional/agent-tester': page(() => import('./pages/regional/RegionalAgentTester'), 'RegionalAgentTester'),
  '/regional/report': page(() => import('./pages/regional/RegionalReport'), 'RegionalReport'),
  '/regional/team': page(() => import('./pages/regional/RegionalTeam'), 'RegionalTeam'),
  // Field crew pages
  '/ward/overview': page(() => import('./pages/ward/WardOverview'), 'WardOverview'),
  '/ward/visits': page(() => import('./pages/ward/WardVisits'), 'WardVisits'),
  '/ward/map': page(() => import('./pages/ward/WardMap'), 'WardMap'),
};
type WorkspacePath = keyof typeof WORKSPACE_PAGES;
const workspacePage = (path: WorkspacePath) => { const { Page } = WORKSPACE_PAGES[path]; return <Page />; };

/** Outside the workspace, while a page is fetched. */
const PageLoading = () => <div className="workspace-loading"><ChhayaBrand /></div>;

function RoleWorkspace() {
  const { pathname } = useLocation();
  const role = pathname.startsWith('/ward') ? 'crew' : 'admin';
  const language = useUiLanguage(role === 'crew' ? 'hi' : 'en');
  // The page's code is fetched alongside the workspace's first read, not after it.
  useEffect(() => { WORKSPACE_PAGES[pathname as WorkspacePath]?.load(); }, [pathname]);
  return <WorkspaceProvider key={role} role={role}>
    <Suspense fallback={<WorkspaceSkeleton role={role} language={role === 'crew' ? language : 'en'} error="" />}><Outlet /></Suspense>
  </WorkspaceProvider>;
}

function Redirect({ to }: { to: string }) {
  const { search } = useLocation();
  return <Navigate to={`${to}${search}`} replace />;
}

export const App: React.FC = () => {
  return (
    <BrowserRouter>
      <Suspense fallback={<PageLoading />}>
      <Routes>
        {/* Landing Page (Two-column role gateway) */}
        <Route path="/" element={<LandingPage />} />

        {/* Regional Officer Routes */}
        <Route element={<RoleWorkspace />}>
          <Route path="/regional" element={<Redirect to="/regional/overview" />} />
          <Route path="/regional/overview" element={workspacePage('/regional/overview')} />
          <Route path="/regional/weather" element={workspacePage('/regional/weather')} />
          <Route path="/regional/districts" element={workspacePage('/regional/districts')} />
          <Route path="/regional/critical" element={workspacePage('/regional/critical')} />
          <Route path="/regional/agent-tester" element={workspacePage('/regional/agent-tester')} />
          <Route path="/regional/report" element={workspacePage('/regional/report')} />
          <Route path="/regional/team" element={workspacePage('/regional/team')} />
          <Route path="/regional/dispatch" element={<Redirect to="/regional/overview" />} />

          {/* Field crew Routes */}
          <Route path="/ward" element={<Redirect to="/ward/overview" />} />
          <Route path="/ward/overview" element={workspacePage('/ward/overview')} />
          <Route path="/ward/visits" element={workspacePage('/ward/visits')} />
          <Route path="/ward/map" element={workspacePage('/ward/map')} />
        </Route>

        {/* Safety & Clinical Evidence */}
        <Route path="/evidence" element={<Evidence.Page />} />

        {/* Compatibility Redirects */}
        <Route path="/alert" element={<Alert.Page />} />
        <Route path="/call" element={<Call.Page />} />
        <Route path="/visit" element={<Redirect to="/ward/visits" />} />
        <Route path="/crew" element={<Redirect to="/ward/overview" />} />
        <Route path="/welcome" element={<Redirect to="/" />} />
        <Route path="/report" element={<Redirect to="/regional/report" />} />
        <Route path="/team" element={<Team.Page />} />

        {/* Fallback to landing */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      </Suspense>
    </BrowserRouter>
  );
};

export default App;

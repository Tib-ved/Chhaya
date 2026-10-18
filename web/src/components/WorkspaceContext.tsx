import { createContext, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import type { URLSearchParamsInit } from 'react-router-dom';
import type { Geography, Overview, Role, Scope, Session } from '../operations';
import { geographyOf, loadRegion, loadState, loadTeam, overviewOf, sessionOf, stateFor, withState, withTeam, type Loaded } from '../lib/roleWorkspace';
import { ResidentPanel } from './ResidentPanel';
import { prefetchWeather } from '../lib/roleApi';
import { WorkspaceSkeleton } from './WorkspaceSkeleton';
import { areaName, text, useUiLanguage, type UiLanguage } from '../lib/wardLocale';
import '../styles/operations.css';
import '../styles/workspace.css';
import '../styles/system.css';
import '../styles/pages.css';

type Workspace = {
  role: Role; language: UiLanguage; session: Session; geography: Geography; data: Overview; loaded: Loaded;
  scope: Scope; scopeLabel: string; worker: string; revision: number; error: string; stateLoading: boolean;
  /** The health workers of the state in view are still being read (the officer's pages are drawn meanwhile). */
  teamLoading: boolean;
  choose: (level: string, value: string) => void; withScope: (path: string) => string;
  openResident: (id: string) => void; openCase: (id: string) => void;
  refresh: () => void;
  /** A question for Ask Chhaya from elsewhere on the page (a resident's case); the panel opens and sends it once. */
  askRequest: AskRequest | null; askChhaya: (text: string) => void; takeAskRequest: () => void;
};
export type AskRequest = { id: number; text: string };
const WorkspaceContext = createContext<Workspace | null>(null);
export function useWorkspace() {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error('Workspace provider required');
  return value;
}

export function WorkspaceProvider({ role, children }: { role: Role; children: ReactNode }) {
  const preferredLanguage = useUiLanguage(role === 'crew' ? 'hi' : 'en');
  const language = role === 'crew' ? preferredLanguage : 'en';
  const [params] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const updateQuery = (next: URLSearchParams, replace = false) => navigate({ pathname: window.location.pathname, search: next.toString() ? `?${next}` : '', hash: window.location.hash }, { replace });
  // Everything shown comes from the role-view API: one load of the region, cut to the area or worker in view.
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [askRequest, setAskRequest] = useState<AskRequest | null>(null);
  const session = useMemo(() => loaded && sessionOf(loaded, role), [loaded, role]);
  const geography = useMemo(() => loaded && geographyOf(loaded), [loaded]);
  const worker = params.get('worker') || session?.workers[0]?.id || '';
  const districtId = params.get('district') || '';
  const wanted = { state: params.get('state') || '', district: districtId, worker: role === 'crew' ? params.get('worker') || '' : '' };
  // The state being shown: the one asked for if it is known, else the district's or worker's, else the first warned.
  const wantedState = loaded ? stateFor(loaded.region, wanted) : '';
  const teamLoading = Boolean(loaded && wantedState && !loaded.teamStates.includes(wantedState));
  // A crew member's residents come with the workers, so the Field crew view also waits for them; the officer's does not.
  const stateLoading = Boolean(loaded && wantedState && !loaded.states.includes(wantedState)) || (role === 'crew' && teamLoading);
  const scope = useMemo<Scope>(() => role === 'crew' ? { state_id: '', district_id: '', ward_id: '', round_id: '' } : { state_id: wantedState, district_id: districtId, ward_id: params.get('ward') || '', round_id: '' }, [role, params, districtId, wantedState]);
  const data = useMemo(() => loaded && overviewOf(loaded, role, worker, scope), [loaded, role, worker, scope]);
  const state = geography?.states.find(s => s.id === scope.state_id);
  const district = state?.districts.find(d => d.id === scope.district_id);
  const ward = district?.wards.find(w => w.id === scope.ward_id);
  const assignedDistricts = geography?.states.flatMap(s => s.districts).filter(d => data?.wards.some(w => w.district_id === d.id)) || [];
  const scopeLabel = role === 'crew'
    ? [...assignedDistricts.map(d => areaName(d.name, language)), ...(data?.wards.map(w => areaName(w.name, language)) || [])].join(' / ') || text(language, 'No assigned area', 'कार्य क्षेत्र तय नहीं है')
    : [state?.name, district?.name, ward?.name].filter(Boolean).join(' / ') || 'All states';
  const refresh = () => setRevision(r => r + 1);

  useEffect(() => {
    if (role !== 'crew') return;
    const next = new URLSearchParams(location.search);
    const keys = ['state', 'district', 'ward', 'round'];
    if (!keys.some(key => next.has(key))) return;
    keys.forEach(key => next.delete(key));
    navigate({ pathname: location.pathname, search: next.toString() ? `?${next}` : '' }, { replace: true });
  }, [role, location.pathname, location.search, navigate]);

  // One read when the workspace opens, on Refresh and after an action; never on a timer.
  useEffect(() => {
    let active = true;
    // The Overview and Weather pages show the area's weather: its read starts now, beside the region's,
    // with the state as soon as it is known (from the address, else once the region says which).
    const showsWeather = /^\/regional\/(overview|weather)/.test(location.pathname);
    const weatherOf = (state: string) => showsWeather && state && prefetchWeather({ state, district_id: districtId, ward_id: params.get('ward') || '' });
    if (wanted.state) weatherOf(wanted.state);
    loadRegion(region => { const state = stateFor(region, wanted); weatherOf(state); return state; }, loaded).then(
      result => { if (active) { setLoaded(result); setError(''); } },
      e => { if (active) setError((e as Error).message); },
    );
    return () => { active = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- a change of area is read by the effect below
  }, [revision]);

  // Moving to a state not read yet reads that state alone.
  useEffect(() => {
    if (!loaded || !wantedState || loaded.states.includes(wantedState)) return;
    let active = true;
    loadState(loaded.region, wantedState).then(
      part => { if (active) { setLoaded(current => current && withState(current, wantedState, part)); setError(''); } },
      e => { if (active) setError((e as Error).message); },
    );
    return () => { active = false; };
  }, [loaded, wantedState]);

  // A state's health workers are read only where they are shown: the Field crew view (a worker's own residents)
  // and the Health workers page. Once read they are kept for the visit.
  const needsTeam = role === 'crew' || location.pathname.startsWith('/regional/team');
  const teamToRead = needsTeam && loaded && wantedState && loaded.states.includes(wantedState) && !loaded.teamStates.includes(wantedState) ? wantedState : '';
  useEffect(() => {
    if (!loaded || !teamToRead) return;
    let active = true;
    loadTeam(loaded, teamToRead).then(
      team => { if (active) { setLoaded(current => current && withTeam(current, teamToRead, team)); setError(''); } },
      e => { if (active) setError((e as Error).message); },
    );
    return () => { active = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- read once per state and load, not on every merge
  }, [loaded?.region, teamToRead]);

  function choose(level: string, value: string) {
    const next = new URLSearchParams(window.location.search);
    ['case', 'resident', 'tier', 'reason', 'call', 'open', 'visited', 'risk', 'called'].forEach(key => next.delete(key));
    if (role === 'crew') ['state', 'district', 'ward', 'round'].forEach(key => next.delete(key));
    if (level !== 'round') next.delete('round');
    if (level === 'state') { next.delete('district'); next.delete('ward'); }
    if (level === 'district') next.delete('ward');
    if (value) next.set(level, value); else next.delete(level);
    updateQuery(next);
  }

  function withScope(path: string) {
    const [pathname, search] = path.split('?');
    const next = new URLSearchParams();
    for (const [key, value] of Object.entries(scope)) if (value) next.set(key.replace('_id', ''), value);
    if (worker && role === 'crew') next.set('worker', worker);
    const incoming = new URLSearchParams(search);
    if (incoming.has('district') && incoming.get('district') !== scope.district_id) { next.delete('ward'); next.delete('round'); next.delete('state'); }
    incoming.forEach((value, key) => next.set(key, value));
    return `${pathname}${next.size ? '?' + next : ''}`;
  }

  // The panel is keyed by resident; a ?case= link is followed to its resident.
  function openCase(id: string) {
    const resident = data?.residents.find(r => r.case_id === id);
    if (resident) openResident(resident.id); else setError('This case is not in the selected area.');
  }
  function openResident(id: string) {
    if (!data?.residents.some(r => r.id === id)) { setError('This resident is not in the selected area.'); return; }
    const next = new URLSearchParams(params); next.delete('case'); next.set('resident', id); updateQuery(next);
  }
  function closeResident() {
    const next = new URLSearchParams(params); next.delete('case'); next.delete('resident'); updateQuery(next, true);
  }
  // The resident's case closes so the answer is read beside the page, in the Ask Chhaya panel.
  function askChhaya(question: string) {
    setAskRequest({ id: Date.now(), text: question });
    closeResident();
  }
  const takeAskRequest = () => setAskRequest(null);

  if (!session || !geography || !data || (role === 'crew' && !loaded?.teamStates.length)) return <WorkspaceSkeleton role={role} language={language} error={error} />;
  const residentId = params.get('resident') || data.residents.find(r => r.case_id && r.case_id === params.get('case'))?.id;
  const value: Workspace = { role, language, session, geography, data, loaded: loaded!, scope, scopeLabel, worker, revision, error, stateLoading, teamLoading, choose, withScope, openResident, openCase, refresh, askRequest, askChhaya, takeAskRequest };
  return <WorkspaceContext.Provider value={value}>
    {children}
    {residentId && <ResidentPanel key={`${location.pathname}:${residentId}`} id={residentId} language={language} admin={role === 'admin'} onClose={closeResident} onUpdate={refresh} onAsk={role === 'admin' ? askChhaya : undefined} />}
  </WorkspaceContext.Provider>;
}

export function useWorkspaceNavigate() {
  const navigate = useNavigate();
  const workspace = useWorkspace();
  return (path: string) => {
    const query = new URLSearchParams(path.split('?')[1]);
    const resident = query.get('resident');
    if (resident) workspace.openResident(resident);
    else navigate(workspace.withScope(path));
  };
}

export function useWorkspaceSearchParams(): [URLSearchParams, (next: URLSearchParamsInit) => void] {
  const [params, setParams] = useSearchParams();
  const { openResident } = useWorkspace();
  const pageParams = new URLSearchParams(params);
  pageParams.delete('resident');
  return [pageParams, next => {
    const value = new URLSearchParams(next as ConstructorParameters<typeof URLSearchParams>[0]);
    const id = value.get('resident');
    if (id) openResident(id); else setParams(value);
  }];
}

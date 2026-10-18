import { useWorkspace } from './WorkspaceContext';
import { text, workerName } from '../lib/wardLocale';
import { SkeletonCards } from './WorkspaceSkeleton';

export function WorkspaceFilters() {
  const { scope, geography, session, role, worker, choose } = useWorkspace();
  const state = geography.states.find(s => s.id === scope.state_id);
  const district = state?.districts.find(d => d.id === scope.district_id);
  return <section className="ops-filters workspace-filters" aria-label="Geographic filters">
    <label>State<select value={scope.state_id} onChange={e => choose('state', e.target.value)}>{geography.states.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
    <label>District<select value={scope.district_id} disabled={!state} onChange={e => choose('district', e.target.value)}><option value="">All districts</option>{state?.districts.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label>
    <label>Ward<select value={scope.ward_id} disabled={!district} onChange={e => choose('ward', e.target.value)}><option value="">All wards</option>{district?.wards.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}</select></label>
    {session.demo && role === 'crew' && <label>Crew member<select value={worker} onChange={e => choose('worker', e.target.value)}>{session.workers.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}</select></label>}
  </section>;
}

export function AssignedWard() {
  const { session, scopeLabel, language, worker, choose } = useWorkspace();
  const selected = session.workers.find(w => w.id === worker);
  return <section className="assigned-ward" aria-label={text(language, 'Assigned area', 'आपका कार्य क्षेत्र')}>
    <div><span className="assigned-ward-label">{text(language, 'Your assigned area', 'आपका कार्य क्षेत्र')}</span><strong>{scopeLabel}</strong><p>{selected ? workerName(selected.name, language) : text(language, 'No worker assigned', 'कार्यकर्ता तय नहीं है')}</p></div>
    {session.demo && <details className="demo-worker-switch"><summary>{text(language, 'Demo: switch worker', 'डेमो के लिए कार्यकर्ता बदलें')}</summary><label htmlFor="demo-worker">{text(language, 'Crew member', 'आशा कार्यकर्ता')}</label><select id="demo-worker" value={worker} onChange={e => choose('worker', e.target.value)}>{session.workers.map(w => <option key={w.id} value={w.id}>{workerName(w.name, language)}</option>)}</select></details>}
  </section>;
}

export function WorkspaceEmpty() {
  const { role, language, stateLoading, error } = useWorkspace();
  if (stateLoading && !error) return <SkeletonCards label={text(language, 'Loading calls and visits…', 'कॉल और मुलाकातों की जानकारी आ रही है…')} />;
  return <section className="card workspace-empty"><h2>{text(language, 'No calls or visits recorded here yet', 'अभी कोई कॉल या मुलाकात दर्ज नहीं है')}</h2><p>{role === 'crew' ? text(language, 'There are no records assigned to you yet. Ask your officer to check your assignment.', 'अभी आपके लिए कोई रिकॉर्ड नहीं है। अपने अधिकारी से कार्य क्षेत्र की जानकारी जाँचने को कहें।') : 'No call round is open in this area.'}</p></section>;
}

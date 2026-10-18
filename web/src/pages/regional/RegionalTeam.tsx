import React from 'react';
import { SidebarLayout } from '../../components/SidebarLayout';
import { useWorkspace } from '../../components/WorkspaceContext';
import { SkeletonCards } from '../../components/WorkspaceSkeleton';
import { dur } from '../../lib/roleData';
import { tierLabel } from '../../lib/labels';
import type { Need, ResidentRow } from '../../lib/roleApi';

const compactDur = (m: number) => { const h = Math.floor(m / 60), r = Math.round(m % 60); return h ? `${h}h ${r}m` : `${r}m`; };
const clock = (value: string) => new Intl.DateTimeFormat('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value));
const minutesBetween = (from: string, to: string) => (Date.parse(to) - Date.parse(from)) / 60000;
const needsVisit = (r: ResidentRow) => r.tier === 'red' || r.tier === 'amber';
const CARRY_PILL: Record<Need, string> = { water_ors: 'amber', medicine_check: 'blue', cooling_centre: 'green' };

export const RegionalTeam: React.FC = () => {
  // The figures are the role-view API's own (/api/workers/{id}/residents), as the workspace loaded them.
  const { scope, openResident, loaded, teamLoading } = useWorkspace();

  // The workers of every state read so far are kept: show those of the state in view.
  const inState = new Set(loaded.region.filter(d => !scope.state_id || (d.state || 'Other') === scope.state_id).map(d => d.id));
  const team = loaded.team.filter(w => inState.has(w.worker.district_id) && (!scope.district_id || w.worker.district_id === scope.district_id) && (!scope.ward_id || w.worker.ward_id === scope.ward_id));
  const districts = [...new Map(team.map(w => [w.worker.district_id, w.worker.district_name])).entries()];

  return (
    <SidebarLayout
      role="regional"
      filters
      header={<div className="head"><div><h1>Health workers</h1><p>Who each worker has visited, who is still waiting, and what to bring.</p></div></div>}
    >
      {!team.length && (teamLoading ? <SkeletonCards label="Loading health workers…" /> : <p className="muted">No health worker is recorded for this area.</p>)}

      {districts.map(([districtId, districtName]) => {
        const workers = team.filter(w => w.worker.district_id === districtId);
        return (
          <section key={districtId} className="card">
            <header>
              <h2>{districtName} district</h2>
              <p>{workers.length} {workers.length === 1 ? 'health worker' : 'health workers'}</p>
            </header>

            <div className="worker-grid">
              {workers.map(({ worker, counts, carry, health_centre, residents }) => {
                const mine = residents.filter(needsVisit);
                const visited = mine.filter(r => r.case_status === 'visited' && r.visited_at).sort((a, b) => Date.parse(b.visited_at!) - Date.parse(a.visited_at!));
                const waitingList = mine.filter(r => r.case_status === 'open').sort((a, b) => (b.tier === 'red' ? 1 : 0) - (a.tier === 'red' ? 1 : 0) || (b.waiting_minutes || 0) - (a.waiting_minutes || 0));
                const times = visited.filter(r => r.opened_at).map(r => minutesBetween(r.opened_at!, r.visited_at!)).filter(t => t >= 0).sort((a, b) => a - b);
                const median = times.length ? times[Math.floor(times.length / 2)] : null;

                return (
                  <article key={worker.id} className="worker-card">
                    <div className="worker-card-head">
                      <b>{worker.name}</b>
                      <span className="muted">{worker.ward_name} · {health_centre?.name || 'No facility recorded'}</span>
                    </div>

                    <div className="worker-stats">
                      <div><b>{mine.length}</b><span>Assigned</span></div>
                      <div><b>{counts.done}</b><span>Visited</span></div>
                      <div><b className={counts.to_visit ? 'red-ink' : undefined}>{counts.to_visit}</b><span>Still to visit</span></div>
                      <div><b title={median == null ? undefined : dur(median)}>{median == null ? '–' : compactDur(median)}</b><span>Median wait</span></div>
                    </div>

                    <div className="worker-list">
                      <h3>Visited</h3>
                      {visited.length ? visited.map(r => (
                        <button key={r.id} type="button" onClick={() => openResident(r.id)}>
                          <span><b>{r.name}</b>, {r.age} {r.tier && <span className={`pill ${r.tier} ml-2`}>{tierLabel(r.tier)}</span>}</span>
                          <span className="muted">{clock(r.visited_at!)}</span>
                          <small>{r.outcome_label || 'Outcome not recorded'}</small>
                        </button>
                      )) : <p className="muted">No visits recorded yet.</p>}
                    </div>

                    <div className="worker-list">
                      <h3>Still to visit</h3>
                      {waitingList.length ? waitingList.map(r => (
                        <button key={r.id} type="button" onClick={() => openResident(r.id)}>
                          <span><b>{r.name}</b>, {r.age} {r.tier && <span className={`pill ${r.tier}`}>{tierLabel(r.tier)}</span>}{r.escalated && <span className="pill blue ml-2 ml-2">Flagged</span>}</span>
                          <span className="muted">waiting {dur(r.waiting_minutes || 0)}</span>
                        </button>
                      )) : <p className="muted">Nobody is waiting.</p>}
                    </div>

                    {carry.length > 0 && (
                      <div>
                        <h3 className="text-small ink-2 mb-2">What to bring</h3>
                        <div className="chips">
                          {carry.map(item => <span key={item.id} className={`pill ${CARRY_PILL[item.id]}`}>{item.label} × {item.count}</span>)}
                        </div>
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
          </section>
        );
      })}
    </SidebarLayout>
  );
};

export default RegionalTeam;

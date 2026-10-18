import React, { useState, useRef, useEffect } from 'react';
import { useWorkspace, useWorkspaceNavigate } from '../../components/WorkspaceContext';
import { SidebarLayout } from '../../components/SidebarLayout';
import { useRoleData } from '../../lib/operationalData';
import { SimulatedPills } from '../../components/HeatPanel';
import { RiskScoreLegend } from '../../components/Legends';
import { HIGH_RISK_SCORE, severityLabel } from '../../lib/labels';

export const RegionalCritical: React.FC = () => {
  const navigate = useWorkspaceNavigate();
  const { openResident } = useWorkspace();
  const { DISTRICTS, WARDS, RESIDENTS, RULES, stats, dur, waiting } = useRoleData();
  const [tab, setTab] = useState<'red_open' | 'red' | 'unreached'>('red_open');
  const [districtsModalOpen, setDistrictsModalOpen] = useState(false);
  const [wardsModalOpen, setWardsModalOpen] = useState(false);
  const districtsDialogRef = useRef<HTMLDialogElement>(null);
  const wardsDialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    if (districtsModalOpen) {
      districtsDialogRef.current?.showModal();
    } else {
      districtsDialogRef.current?.close();
    }
  }, [districtsModalOpen]);

  useEffect(() => {
    if (wardsModalOpen) {
      wardsDialogRef.current?.showModal();
    } else {
      wardsDialogRef.current?.close();
    }
  }, [wardsModalOpen]);

  // Red cases, longest wait first; then people who did not answer three calls
  const redCases = RESIDENTS.filter(r => r.tier === 'red').sort((a, b) => waiting(b) - waiting(a));
  const redOpen = redCases.filter(r => !r.visit);
  const unreached = RESIDENTS.filter(r => r.reasons.includes('A8'));
  const unreachedOpen = unreached.filter(r => !r.visit);
  const longest = Math.max(0, ...redOpen.map(waiting));
  const wardName = (id: string) => WARDS.find(w => w.id === id)?.name;
  const rows = tab === 'red_open' ? redOpen : tab === 'red' ? redCases : unreached;

  // A district or ward narrows the page to it, then shows its urgent residents.
  const drill = (path: string) => {
    navigate(path);
    window.setTimeout(() => document.getElementById('urgent-residents')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 60);
  };
  const score = (risk: number) => <><b>{risk}</b>{risk >= HIGH_RISK_SCORE && <span className="muted"> · high risk</span>}</>;
  const tabs: [typeof tab, string, number][] = [
    ['red_open', 'Red, not visited', redOpen.length],
    ['red', 'All Red cases', redCases.length],
    ['unreached', 'No answer after 3 calls', unreached.length],
  ];

  return (
    <SidebarLayout
      role="regional"
      filters
      header={
        <div className="head head-overview">
          <div className="head-title-group">
            <h1>Urgent cases</h1>
            <p>Immediate field response queue for Red priority residents and unanswered automated calls.</p>
          </div>
          <div className="head-actions">
            <button type="button" className="btn ghost" onClick={() => navigate('/regional/districts?tier=red')}>See all Red cases</button>
          </div>
        </div>
      }
    >
      {/* 1. Four Actionable Metric Cards (Stripe Style) */}
      <div className="ops-metrics-grid" role="region" aria-label="Urgent cases key metrics">
        <button
          type="button"
          className={`ops-metric-card ${tab === 'red_open' ? 'active' : ''} ${redOpen.length ? 'urgent' : ''}`}
          onClick={() => setTab('red_open')}
        >
          <span className="metric-card-label">Red, not visited</span>
          <span className="metric-card-val">{redOpen.length}</span>
          <span className="metric-card-desc">Red tier residents awaiting field visit</span>
          <span className="metric-card-action">Filter queue ›</span>
        </button>

        <div className="ops-metric-card">
          <span className="metric-card-label">Longest Red wait</span>
          <span className="metric-card-val">{redOpen.length ? dur(longest) : '–'}</span>
          <span className="metric-card-desc">Max duration case has been waiting</span>
          <span className="metric-card-action">Oldest case first</span>
        </div>

        <button
          type="button"
          className={`ops-metric-card ${tab === 'red' ? 'active' : ''}`}
          onClick={() => setTab('red')}
        >
          <span className="metric-card-label">Red cases visited</span>
          <span className="metric-card-val">{redCases.length - redOpen.length}</span>
          <span className="metric-card-desc">Completed visits by health workers</span>
          <span className="metric-card-action">View all Red ›</span>
        </button>

        <button
          type="button"
          className={`ops-metric-card ${tab === 'unreached' ? 'active' : ''}`}
          onClick={() => setTab('unreached')}
        >
          <span className="metric-card-label">Unanswered ×3</span>
          <span className="metric-card-val">{unreachedOpen.length}</span>
          <span className="metric-card-desc">Unreached after 3 automated attempts</span>
          <span className="metric-card-action">Review door-knock list ›</span>
        </button>
      </div>

      {/* 2. Priority Queue Card */}
      <section className="card scroll-anchor" id="urgent-residents">
        <header>
          <div>
            <h2>Who to see now</h2>
            <p>{tab === 'unreached' ? 'Someone who does not answer three calls is not treated as safe: knock on the door, and if nobody answers, ask a neighbour.' : 'Residents whose answers matched a Red rule, longest wait first'}</p>
          </div>
        </header>

        <div className="tabs-segmented" role="group" aria-label="Filter urgent queue">
          {tabs.map(([key, label, n]) => (
            <button key={key} type="button" aria-pressed={tab === key} onClick={() => setTab(key)}>
              <span>{label}</span>
              <span className="tab-count-badge">{n}</span>
            </button>
          ))}
        </div>

        <div className="chips mb-3">
          <RiskScoreLegend />
        </div>

        {rows.length === 0 ? (
          <p className="muted pad-y-lg">{tab === 'unreached' ? 'Nobody is unreached after three calls.' : tab === 'red_open' ? 'Every Red case has been visited.' : 'No Red cases in this area.'}</p>
        ) : (
          <div className="ops-table-wrap">
            <table className="ops-table">
              <thead>
                <tr>
                  <th>Resident</th>
                  <th>Ward</th>
                  <th className="num">Age</th>
                  <th className="num">Risk score</th>
                  <th>{tab === 'unreached' ? 'Calls' : 'Reasons'}</th>
                  <th className="num">Waiting</th>
                  <th>Visit</th>
                  <th><span className="ops-sr-only">Action</span></th>
                </tr>
              </thead>
              <tbody>
                {rows.map(r => (
                  <tr key={r.id} className="interactive" onClick={() => openResident(r.id)}>
                    <td>
                      <button
                        type="button"
                        className={`ops-resident-name-btn ${r.simulated ? 'simulated' : ''}`}
                        title={r.name}
                        onClick={(e) => { e.stopPropagation(); openResident(r.id); }}
                      >
                        {r.name}
                      </button>
                      {r.simulated && <>{' '}<SimulatedPills call /></>}
                      {r.alone && <div className="muted cell-note">Lives alone</div>}
                    </td>
                    <td>{wardName(r.ward)}</td>
                    <td className="num">{r.age}</td>
                    <td className="num">{score(r.risk)}</td>
                    <td className="cell-reasons">{tab === 'unreached' ? '3 calls unanswered' : r.reasons.map(x => RULES[x]?.[0]).filter(Boolean).join(' · ')}</td>
                    <td className="num">{r.visit ? '–' : dur(waiting(r))}</td>
                    <td>
                      {r.visit
                        ? <><span className="pill green">Visited</span>{r.visit.simulated && <>{' '}<SimulatedPills visit /></>}</>
                        : <span className={`pill ${tab === 'unreached' ? 'amber' : 'red'}`}>{tab === 'unreached' ? 'Visit today' : 'Visit now'}</span>}
                    </td>
                    <td>
                      <button type="button" className="btn ghost btn-row" onClick={(e) => { e.stopPropagation(); openResident(r.id); }}>Open case</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* 3. Distribution by District & Ward */}
      <div className="grid2">
        <section className="card">
          <header>
            <div>
              <h2>District warnings</h2>
              <p>Choose a district to narrow the urgent queue</p>
            </div>
          </header>
          <div className="attn">
            {DISTRICTS.filter(d => d.onboarded).slice(0, 5).map(d => {
              const s = stats({ district: d.id });
              const isAlert = d.warning !== null;
              const red = d.warning?.level === 'red';
              return (
                <button type="button" key={d.id} className="attn-item drill-card" aria-label={`Show urgent cases in ${d.name}`} onClick={() => drill(`/regional/critical?district=${d.id}`)}>
                  <span className="attn-main">
                    <span className="attn-tags">
                      <b>{d.name}</b>
                      {isAlert && <span className={`pill ${red ? 'red' : 'amber'}`}>{severityLabel(d.warning?.level)} alert</span>}
                    </span>
                    <span className="muted">{s.all.length} registered · {s.high} high risk (score {HIGH_RISK_SCORE}+) · {isAlert ? `warning opened ${d.warning?.opened}` : 'no warning recorded'}</span>
                  </span>
                  <span className="drill-figure">{d.warning?.peak == null ? '–' : `${d.warning.peak} °C`}</span>
                </button>
              );
            })}
          </div>
          {DISTRICTS.filter(d => d.onboarded).length > 5 && (
            <button
              type="button"
              className="linkbtn mt-3"
              onClick={() => setDistrictsModalOpen(true)}
            >
              Show all {DISTRICTS.filter(d => d.onboarded).length} districts ›
            </button>
          )}
        </section>

        <section className="card">
          <header>
            <div>
              <h2>Residents at risk by ward</h2>
              <p>Choose a ward to narrow the urgent queue</p>
            </div>
          </header>
          <div className="attn">
            {WARDS.slice(0, 5).map(w => {
              const ws = stats({ ward: w.id });
              return (
                <button type="button" key={w.id} className="attn-item drill-card" aria-label={`Show urgent cases in ${w.name}`} onClick={() => drill(`/regional/critical?district=${w.district}&ward=${w.id}`)}>
                  <span className="attn-main">
                    <span className="attn-tags"><b>{w.name}</b><span className="muted">{w.workerName}</span></span>
                    <span className="attn-tags">
                      <span className="pill red">{ws.tiers.red} Red</span>
                      <span className="pill amber">{ws.tiers.amber} Amber</span>
                      <span className="muted">{ws.high} high risk · {ws.alone} living alone · {ws.visited} visited</span>
                    </span>
                  </span>
                </button>
              );
            })}
          </div>
          {WARDS.length > 5 && (
            <button
              type="button"
              className="linkbtn mt-3"
              onClick={() => setWardsModalOpen(true)}
            >
              Show all {WARDS.length} wards ›
            </button>
          )}
        </section>
      </div>

      {/* District warnings modal popup */}
      {districtsModalOpen && (
        <dialog
          ref={districtsDialogRef}
          className="ops-dialog ops-drilldown-dialog"
          aria-labelledby="districts-modal-title"
          onCancel={() => setDistrictsModalOpen(false)}
          onClick={e => {
            if (e.target === districtsDialogRef.current) setDistrictsModalOpen(false);
          }}
        >
          <div className="ops-drilldown-header">
            <div className="ops-drilldown-title-group">
              <h2 id="districts-modal-title">
                District warnings ({DISTRICTS.filter(d => d.onboarded).length})
              </h2>
              <p>Choose a district to narrow the urgent queue</p>
            </div>
            <button
              type="button"
              className="ops-close"
              onClick={() => setDistrictsModalOpen(false)}
              aria-label="Close dialog"
            >
              Close
            </button>
          </div>
          <div className="ops-drilldown-body">
            <div className="attn">
              {DISTRICTS.filter(d => d.onboarded).map(d => {
                const s = stats({ district: d.id });
                const isAlert = d.warning !== null;
                const red = d.warning?.level === 'red';
                return (
                  <button
                    type="button"
                    key={d.id}
                    className="attn-item drill-card"
                    aria-label={`Show urgent cases in ${d.name}`}
                    onClick={() => {
                      setDistrictsModalOpen(false);
                      drill(`/regional/critical?district=${d.id}`);
                    }}
                  >
                    <span className="attn-main">
                      <span className="attn-tags">
                        <b>{d.name}</b>
                        {isAlert && (
                          <span className={`pill ${red ? 'red' : 'amber'}`}>
                            {severityLabel(d.warning?.level)} alert
                          </span>
                        )}
                      </span>
                      <span className="muted">
                        {s.all.length} registered · {s.high} high risk (score {HIGH_RISK_SCORE}+) ·{' '}
                        {isAlert ? `warning opened ${d.warning?.opened}` : 'no warning recorded'}
                      </span>
                    </span>
                    <span className="drill-figure">
                      {d.warning?.peak == null ? '–' : `${d.warning.peak} °C`}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        </dialog>
      )}

      {/* Residents at risk by ward modal popup */}
      {wardsModalOpen && (
        <dialog
          ref={wardsDialogRef}
          className="ops-dialog ops-drilldown-dialog"
          aria-labelledby="wards-modal-title"
          onCancel={() => setWardsModalOpen(false)}
          onClick={e => {
            if (e.target === wardsDialogRef.current) setWardsModalOpen(false);
          }}
        >
          <div className="ops-drilldown-header">
            <div className="ops-drilldown-title-group">
              <h2 id="wards-modal-title">
                Residents at risk by ward ({WARDS.length})
              </h2>
              <p>Choose a ward to narrow the urgent queue</p>
            </div>
            <button
              type="button"
              className="ops-close"
              onClick={() => setWardsModalOpen(false)}
              aria-label="Close dialog"
            >
              Close
            </button>
          </div>
          <div className="ops-drilldown-body">
            <div className="attn">
              {WARDS.map(w => {
                const ws = stats({ ward: w.id });
                return (
                  <button
                    type="button"
                    key={w.id}
                    className="attn-item drill-card"
                    aria-label={`Show urgent cases in ${w.name}`}
                    onClick={() => {
                      setWardsModalOpen(false);
                      drill(`/regional/critical?district=${w.district}&ward=${w.id}`);
                    }}
                  >
                    <span className="attn-main">
                      <span className="attn-tags">
                        <b>{w.name}</b>
                        <span className="muted">{w.workerName}</span>
                      </span>
                      <span className="attn-tags">
                        <span className="pill red">{ws.tiers.red} Red</span>
                        <span className="pill amber">{ws.tiers.amber} Amber</span>
                        <span className="muted">
                          {ws.high} high risk · {ws.alone} living alone · {ws.visited} visited
                        </span>
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        </dialog>
      )}
    </SidebarLayout>
  );
};

export default RegionalCritical;

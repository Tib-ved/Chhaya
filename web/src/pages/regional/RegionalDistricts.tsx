import React, { useState, useMemo } from 'react';
import { useWorkspaceSearchParams } from '../../components/WorkspaceContext';
import { SidebarLayout } from '../../components/SidebarLayout';
import { useRoleData, type ResidentFilter } from '../../lib/operationalData';
import { DonutChart } from '../../components/charts/DonutChart';
import { AlertReason, CallPlan, HeatCurve, ScenarioPill, SimulatedPills } from '../../components/HeatPanel';
import { RiskScoreLegend, TierLegend } from '../../components/Legends';
import { HIGH_RISK_SCORE, tierLabel, warningLabel } from '../../lib/labels';
import { CoordinateLink } from '../../components/CoordinateLink';
import { AgentTestDialog } from '../../components/AgentTestDialog';

export const RegionalDistricts: React.FC = () => {
  const [searchParams, setSearchParams] = useWorkspaceSearchParams();
  const { DISTRICTS, WARDS, RESIDENTS, COND, RULES, OUTCOMES, TELL, stats, inScope, applyFilter, callLabel, clock, dur, waiting, riskWhy, ackEscalation, reassignWorker, geography, scope, choose, loaded, openResident } = useRoleData();

  // Active drilldown from URL
  const selectedDistrictId = searchParams.get('district');
  const selectedWardId = searchParams.get('ward');
  const selectedResidentId = searchParams.get('resident');

  // Filter keys from URL
  const filterTier = searchParams.get('tier') || undefined;
  const filterCall = searchParams.get('call') || undefined;
  const filterReason = searchParams.get('reason') || undefined;
  const filterRisk = searchParams.get('risk') ? 1 : undefined;
  const filterOpen = searchParams.get('open') ? 1 : undefined;
  const filterCalled = searchParams.get('called') ? 1 : undefined;
  const filterVisited = searchParams.get('visited') ? 1 : undefined;

  const [ackReply, setAckReply] = useState('');
  const [, setRefreshKey] = useState(0);
  const [testCallResident, setTestCallResident] = useState<(typeof RESIDENTS)[number] | null>(null);

  const activeDistrict = DISTRICTS.find(d => d.id === selectedDistrictId);
  const activeWard = WARDS.find(w => w.id === selectedWardId);
  const activeResident = RESIDENTS.find(r => r.id === selectedResidentId);
  // The district as the API sent it, for the heat curve, why the alert was raised and the call plan.
  const viewOf = (id: string | undefined) => loaded.districts.find(d => d.district.id === id);
  const activeView = viewOf(activeDistrict?.id);

  // The field crew member a resident's visits go to: the case's worker, else the ward's
  const crewName = (r: { worker: string; ward: string }) =>
    WARDS.find(w => w.worker && w.worker === r.worker)?.workerName || WARDS.find(w => w.id === r.ward)?.workerName || 'Unassigned';

  // Filter chips helper
  const clearFilter = (key: string) => {
    const next = new URLSearchParams(searchParams);
    next.delete(key);
    setSearchParams(next);
  };

  // Scope residents
  const scopedResidents = useMemo(() => {
    const scope = {
      district: selectedDistrictId || undefined,
      ward: selectedWardId || undefined
    };
    const f: ResidentFilter = {
      tier: filterTier,
      call: filterCall,
      reason: filterReason,
      risk: filterRisk,
      open: filterOpen,
      called: filterCalled,
      visited: filterVisited
    };
    return applyFilter(inScope(scope), f);
  }, [
    selectedDistrictId,
    selectedWardId,
    filterTier,
    filterCall,
    filterReason,
    filterRisk,
    filterOpen,
    filterCalled,
    filterVisited, inScope, applyFilter
  ]);

  // Breadcrumbs: State › District › Ward
  const activeState = geography.states.find(st => st.id === scope.state_id) || geography.states.find(st => st.districts.some(d => d.id === selectedDistrictId));
  const breadcrumbs = activeState && (
    <div className="row-sm">
      <button type="button" onClick={() => choose('state', activeState.id)}>{activeState.name}</button>
      {activeDistrict && (
        <>
          <span aria-hidden="true">›</span>
          <button type="button" onClick={() => setSearchParams({ district: activeDistrict.id })}>{activeDistrict.name}</button>
        </>
      )}
      {activeWard && activeDistrict && (
        <>
          <span aria-hidden="true">›</span>
          <button type="button" onClick={() => setSearchParams({ district: activeDistrict.id, ward: activeWard.id })}>{activeWard.name}</button>
        </>
      )}
    </div>
  );

  const pageHead = (
    <div className="head head-overview">
      <div className="head-title-group">
        <h1>{activeWard ? `${activeWard.name}, ${activeDistrict?.name}` : activeDistrict ? `${activeDistrict.name} district` : 'Coverage'}</h1>
        <p>
          {activeDistrict?.warning ? (
            <>{warningLabel(activeDistrict.warning.level, activeDistrict.warning.hazard)}{activeDistrict.warning.peak != null ? ` · peak ${activeDistrict.warning.peak} °C` : ''} <ScenarioPill warning={activeView?.warning} /></>
          ) : (
            'Resident registers, vulnerability profiles, and clinical call audits across districts.'
          )}
        </p>
      </div>
      <div className="head-actions">
        {activeDistrict && !activeWard && activeDistrict.warning && (
          <button
            type="button"
            className="btn ghost"
            title="List every resident Chhaya called in this district"
            onClick={() => {
              const next = new URLSearchParams(searchParams);
              next.set('called', '1');
              setSearchParams(next);
            }}
          >
            Show everyone called
          </button>
        )}
      </div>
    </div>
  );

  if (!scope.state_id && !selectedDistrictId) return <SidebarLayout role="regional" filters header={<div className="head"><h1>Coverage</h1></div>}><div className="grid2">{geography.states.map(state => <button className="rowcard" key={state.id} onClick={() => choose('state', state.id)}><div><h2>{state.name}</h2><p>{state.districts.length} districts · {state.districts.filter(d => d.onboarded).length} with a resident register</p></div><span>Explore districts</span></button>)}</div></SidebarLayout>;

  return (
    <SidebarLayout role="regional" filters breadcrumbs={breadcrumbs} header={activeResident ? undefined : pageHead}>
      {/* 1. If Resident Selected: Full Call & Clinical Audit Detail */}
      {activeResident ? (
        <div className="stack-lg">
          <div className="head">
            <div>
              <div className="chips mb-2">
                {activeResident.tier && (
                  <span className={`pill ${activeResident.tier}`}>
                    <i className={`dot ${activeResident.tier}`} />
                    {tierLabel(activeResident.tier)} tier
                  </span>
                )}
                <span className="pill">{callLabel(activeResident)}</span>
                {activeResident.test && <span className="pill tag">Test call</span>}
              </div>
              <h1>
                {activeResident.name}, {activeResident.age}
              </h1>
              <p>
                {activeWard?.name || activeResident.ward}, {activeDistrict?.name} · ASHA Worker:{' '}
                {WARDS.find(w => w.worker === activeResident.worker)?.workerName}
              </p>
            </div>
            <button
              type="button"
              className="btn ghost"
              onClick={() => {
                const next = new URLSearchParams(searchParams);
                next.delete('resident');
                setSearchParams(next);
              }}
            >
              Back to the list
            </button>
          </div>

          {/* Resident profile and risk score */}
          <section className="card">
            <header>
              <h2>Vulnerability profile</h2>
              <span className="pill">Risk score {activeResident.risk}{activeResident.risk >= HIGH_RISK_SCORE ? ' · high risk' : ''}</span>
            </header>
            <div className="chips">
              <span className="pill">Age {activeResident.age}</span>
              <span className="pill">
                {activeResident.alone ? 'Lives alone' : 'Lives with family'}
              </span>
              {activeResident.outdoor && (
                <span className="pill">Works outdoors (Labourer/Farmer)</span>
              )}
              {activeResident.conds.map(c => (
                <span key={c} className="pill">
                  {COND[c] ? COND[c][0] : c}
                </span>
              ))}
            </div>
            <p className="muted text-small mt-1">
              Risk score from: {riskWhy(activeResident) || 'no added risk'}
            </p>
            <RiskScoreLegend />
          </section>

          {/* Columns: Clinical Rules, Recorded Symptoms, and Verbatim Transcript */}
          <div className="cols">
            <div className="stack">
              {/* Triage Justification */}
              <section className="card">
                <header>
                  <h2>Why this tier</h2>
                  <p>Fixed rules set the tier from the recorded answers.</p>
                </header>
                {activeResident.reasons.length > 0 ? (
                  activeResident.reasons.map(ruleId => {
                    const rule = RULES[ruleId];
                    return (
                      <div key={ruleId} className="rule">
                        <span className="mono">{ruleId}</span>
                        <div>
                          <b>{rule[0]}</b>{' '}
                          <span className={`pill ${rule[2]}`}>
                            <i className={`dot ${rule[2]}`} />
                            {tierLabel(rule[2])} rule
                          </span>
                        </div>
                      </div>
                    );
                  })
                ) : (
                  <p className="muted">
                    No rule was matched by the recorded answers.
                  </p>
                )}
              </section>

              {/* Recorded Symptoms */}
              {activeResident.answers && (
                <section className="card">
                  <header>
                    <h2>Answers recorded on the call</h2>
                    <p>
                      Spoken at {clock(activeResident.call?.at || 0)} by{' '}
                      {activeResident.respondent === 'family' ? 'family member' : 'resident'}
                    </p>
                  </header>
                  {Object.keys(activeResident.answers).map(q => {
                    const ans = activeResident.answers![q];
                    return (
                      <div key={q} className="kv kv-col">
                        <div className="kv-row">
                          <span>{q[0].toUpperCase() + q.slice(1)}</span>
                          <b>{ans.v}</b>
                        </div>
                        <div className="quote">“{ans.quote}”</div>
                      </div>
                    );
                  })}
                  {activeResident.note && (
                    <div className="note mt-2">
                      <small>Call summary · written by a model, check it against the answers</small>
                      {activeResident.note}
                    </div>
                  )}
                </section>
              )}

              {/* Action and Worker Response */}
              <section className="card">
                <header>
                  <h2>Visit</h2>
                </header>
                <div className="kv">
                  <span>Case opened</span>
                  <b>{clock(activeResident.opened || 0)}</b>
                </div>
                <div className="kv">
                  <span>Visit status</span>
                  <b>
                    {activeResident.visit
                      ? `Visited at ${clock(activeResident.visit.at)} (${
                          OUTCOMES.find(o => o[0] === activeResident.visit?.outcome)?.[1] ||
                          activeResident.visit.outcome
                        })`
                      : activeResident.tier
                      ? `Waiting ${dur(waiting(activeResident))}`
                      : 'Not needed'}
                  </b>
                </div>

                {/* Worker Escalation Box */}
                {activeResident.esc && (
                  <div className="escbox mt-2">
                    <div className="mono mono-label">
                      Flagged to the regional officer at {clock(activeResident.esc.at)}
                    </div>
                    <b>
                      {TELL.find(t => t[0] === activeResident.esc?.reason)?.[1] ||
                        activeResident.esc.reason}
                    </b>
                    {activeResident.esc.note && <span>“{activeResident.esc.note}”</span>}
                    {activeResident.esc.ack ? (
                      <span className="pill green">
                        <i className="dot green" /> Acknowledged by the officer
                      </span>
                    ) : (
                      <div className="stack-sm">
                        <input
                          type="text"
                          className="field"
                          placeholder="Reply to the worker (optional)"
                          value={ackReply}
                          onChange={e => setAckReply(e.target.value)}
                        />
                        <button
                          type="button"
                          className="btn"
                          onClick={() => {
                            ackEscalation(activeResident.id, ackReply);
                            setRefreshKey(k => k + 1);
                          }}
                        >
                          Acknowledge the flag
                        </button>
                      </div>
                    )}
                  </div>
                )}

                {/* Reassign Worker Dropdown */}
                <div className="mt-3">
                  <label className="lab" htmlFor="reassign-worker">
                    Reassign to another ASHA worker
                  </label>
                  <select
                    id="reassign-worker"
                    className="field"
                    value={activeResident.worker}
                    onChange={e => {
                      reassignWorker(activeResident.id, e.target.value);
                      setRefreshKey(k => k + 1);
                    }}
                  >
                    {WARDS.filter(w => w.district === activeResident.district).map(w => (
                      <option key={w.worker} value={w.worker}>
                        {w.workerName} · {w.name}
                      </option>
                    ))}
                  </select>
                </div>
              </section>
            </div>

            {/* Verbatim Transcript */}
            <section className="card">
              <header>
                <h2>Transcript</h2>
                <p>In the language spoken on the call.</p>
              </header>
              {activeResident.transcript && activeResident.transcript.length > 0 ? (
                <div className="talk">
                  {activeResident.transcript.map((line, idx) => (
                    <div key={idx} className={`say ${line[0]}`}>
                      <small>{line[0] === 'agent' ? 'Chhaya AI voice agent' : 'Resident'}</small>
                      {line[1]}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="muted">
                  No conversation was recorded for this resident.
                </p>
              )}
            </section>
          </div>
        </div>
      ) : (
        /* 2. District & Ward View */
        <div className="stack-lg">
          {/* The heat of the day; before a round, who will be called first */}
          {/* The temperature chart carries the reason; a ward has no chart, so it is said on its own there. */}
          {activeView && activeWard && <AlertReason warning={activeView.warning} />}
          {activeView && !activeWard && <HeatCurve view={activeView} className="card" />}
          {activeView && !activeView.round && <CallPlan view={activeView} wardName={activeWard?.name} onOpen={openResident} className="card" />}

          {/* District Cards Selector if at region root */}
          {!activeDistrict && (
            <>
              <div className="ops-metrics-grid" role="region" aria-label="Coverage overview statistics">
                <div className="ops-metric-card">
                  <span className="metric-card-label">Total registered</span>
                  <span className="metric-card-val">{stats().all.length}</span>
                  <span className="metric-card-desc">Residents in onboarded wards</span>
                </div>
                <div className="ops-metric-card">
                  <span className="metric-card-label">High risk (score 5+)</span>
                  <span className="metric-card-val">{stats().high}</span>
                  <span className="metric-card-desc">Elderly, isolated, or chronic conditions</span>
                </div>
                <div className="ops-metric-card">
                  <span className="metric-card-label">Reached by call</span>
                  <span className="metric-card-val">{stats().reached}</span>
                  <span className="metric-card-desc">Of {stats().called.length} calls attempted ({stats().called.length > 0 ? Math.round((stats().reached / stats().called.length) * 100) : 0}%)</span>
                </div>
                <div className="ops-metric-card">
                  <span className="metric-card-label">Field visits</span>
                  <span className="metric-card-val">{stats().visited}</span>
                  <span className="metric-card-desc">{stats().open} cases still awaiting crew</span>
                </div>
              </div>

              <div className="coverage-district-grid">
                {DISTRICTS.map(d => {
                  const s = stats({ district: d.id });
                  const wardsCount = geography.states.flatMap(item => item.districts).find(item => item.id === d.id)?.wards.length || 0;
                  return (
                    <button
                      key={d.id}
                      type="button"
                      className="coverage-card"
                      onClick={() => setSearchParams({ district: d.id })}
                    >
                      <div className="coverage-card-head">
                        <div>
                          <h2 className="coverage-card-title">{d.name}</h2>
                          <span className="text-small text-muted">{wardsCount} wards · {s.all.length} residents</span>
                        </div>
                        {d.warning ? (
                          <span className={`pill ${d.warning.level === 'red' ? 'red' : 'amber'}`}>
                            <i className={`dot ${d.warning.level === 'red' ? 'red' : 'amber'}`} /> {warningLabel(d.warning.level, d.warning.hazard)}
                          </span>
                        ) : (
                          <span className="pill">{d.onboarded ? 'No alert' : 'Not onboarded'}</span>
                        )}
                      </div>

                      <div className="coverage-card-stats">
                        <span><strong>{s.high}</strong> high risk</span>
                        <span>·</span>
                        <span><strong>{s.old}</strong> age 75+</span>
                        <span>·</span>
                        <span><strong>{s.alone}</strong> living alone</span>
                      </div>

                      {d.warning && (
                        <div className="coverage-card-stats">
                          <span><strong>{s.reached}</strong> of {s.called.length} reached</span>
                          <span>·</span>
                          <span className="text-red"><strong>{s.tiers.red}</strong> Red</span>
                          <span>·</span>
                          <span className="text-amber"><strong>{s.tiers.amber}</strong> Amber</span>
                          <span>·</span>
                          <span><strong>{s.visited}</strong> visited</span>
                        </div>
                      )}

                      <span className="metric-card-action">Explore district <span aria-hidden="true">›</span></span>
                    </button>
                  );
                })}
              </div>
            </>
          )}

          {/* Wards Cards Selector if inside a district */}
          {activeDistrict && !activeWard && (
            <div>
              <h2 className="mb-3">Wards in {activeDistrict.name}</h2>
              {!activeDistrict.onboarded && <p className="muted">No wards or residents have been added for this district.</p>}
              <div className="coverage-district-grid">
                {WARDS.filter(w => w.district === activeDistrict.id).map(w => {
                  const s = stats({ ward: w.id });
                  return (
                    <button
                      key={w.id}
                      type="button"
                      className="coverage-card"
                      onClick={() => setSearchParams({ district: activeDistrict.id, ward: w.id })}
                    >
                      <div className="coverage-card-head">
                        <div>
                          <h3 className="coverage-card-title">{w.name}</h3>
                          <span className="text-small text-muted">ASHA worker: <strong>{w.workerName}</strong></span>
                        </div>
                        {activeDistrict.warning && (
                          <span className="pill red">
                            {s.tiers.red} Red
                          </span>
                        )}
                      </div>

                      <div className="coverage-card-stats">
                        <span><strong>{s.all.length}</strong> residents</span>
                        <span>·</span>
                        <span><strong>{s.high}</strong> high risk</span>
                        <span>·</span>
                        <span><strong>{s.visited}</strong> of {s.need} visited</span>
                      </div>

                      {activeDistrict.warning && (
                        <div className="mini-tier-bar">
                          <span className="red" style={{ flex: s.tiers.red || 1 }} />
                          <span className="amber" style={{ flex: s.tiers.amber || 1 }} />
                          <span className="green" style={{ flex: s.tiers.green || 1 }} />
                        </div>
                      )}

                      <span className="metric-card-action">View ward residents <span aria-hidden="true">›</span></span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {/* Ward Analytics if inside a specific ward */}
          {activeWard && (
            <div className="grid3">
              <section className="card">
                <header>
                  <h2>Tiers in {activeWard.name}</h2>
                </header>
                <DonutChart
                  centre={[stats({ ward: activeWard.id }).cases.length, 'cases']}
                  segments={[
                    {
                      label: 'Red',
                      n: stats({ ward: activeWard.id }).tiers.red,
                      color: 'var(--red)'
                    },
                    {
                      label: 'Amber',
                      n: stats({ ward: activeWard.id }).tiers.amber,
                      color: 'var(--amber)'
                    },
                    {
                      label: 'Green',
                      n: stats({ ward: activeWard.id }).tiers.green,
                      color: 'var(--green)'
                    }
                  ]}
                />
              </section>

              <section className="card">
                <header>
                  <h2>Health worker and centre</h2>
                </header>
                <div className="kv">
                  <span>Health worker</span>
                  <b>{activeWard.workerName}</b>
                </div>
                <div className="kv">
                  <span>Health centre</span>
                  <b>{activeWard.centre}</b>
                </div>
                <div className="kv">
                  <span>Coordinates</span>
                  <b>
                    <CoordinateLink lat={activeWard.lat} lng={activeWard.lng} />
                  </b>
                </div>
              </section>
            </div>
          )}

          {/* Filter Chips Bar */}
          {(filterTier || filterCall || filterReason || filterRisk || filterOpen) && (
            <div className="chips">
              <span className="muted">Filters:</span>
              {filterTier && (
                <button
                  type="button"
                  className="chip"
                  onClick={() => clearFilter('tier')}
                >
                  {tierLabel(filterTier)} tier ✕
                </button>
              )}
              {filterCall && (
                <button
                  type="button"
                  className="chip"
                  onClick={() => clearFilter('call')}
                >
                  Call: {filterCall} ✕
                </button>
              )}
              {filterReason && (
                <button
                  type="button"
                  className="chip"
                  onClick={() => clearFilter('reason')}
                >
                  Rule: {RULES[filterReason]?.[0] || filterReason} ✕
                </button>
              )}
              {filterRisk && (
                <button
                  type="button"
                  className="chip"
                  onClick={() => clearFilter('risk')}
                >
                  High risk (score 5+) ✕
                </button>
              )}
              {filterOpen && (
                <button
                  type="button"
                  className="chip"
                  onClick={() => clearFilter('open')}
                >
                  Not visited yet ✕
                </button>
              )}
              <button
                type="button"
                className="linkbtn"
                onClick={() => {
                  setSearchParams({
                    ...(selectedDistrictId && { district: selectedDistrictId }),
                    ...(selectedWardId && { ward: selectedWardId })
                  });
                }}
              >
                Clear all filters
              </button>
            </div>
          )}

          {/* Filterable Resident Table */}
          <section className="card">
            <header>
              <h2>
                Resident register ({scopedResidents.length} of{' '}
                {inScope({ district: selectedDistrictId || undefined, ward: selectedWardId || undefined }).length})
                {activeWard && <span className="pill inline-pill text-small">Field crew: {activeWard.workerName}</span>}
              </h2>
              <p>Open a resident to read the call or arrange a visit</p>
            </header>
            <div className="chips mb-3">
              <RiskScoreLegend />
              <TierLegend />
            </div>

            {scopedResidents.length === 0 ? (
              <p className="muted pad-y-lg">
                No residents match this filter combination.
              </p>
            ) : (
              <div className="ops-table-wrap">
                <table className="ops-table">
                  <thead>
                    <tr>
                      <th>Resident</th>
                      <th className="num">Age</th>
                      <th>Ward</th>
                      <th>Field crew</th>
                      <th className="num">Risk score</th>
                      <th>Call</th>
                      <th>Tier</th>
                      <th>Rule matched</th>
                      <th>Visit</th>
                      <th className="cell-action">Test call</th>
                    </tr>
                  </thead>
                  <tbody>
                    {scopedResidents.map(r => {
                      const wardName = WARDS.find(w => w.id === r.ward)?.name || '–';
                      const crew = crewName(r);
                      const callText = callLabel(r);
                      const tierText = r.tier ? tierLabel(r.tier) : 'None';
                      const reasonText = r.reasons.map(x => RULES[x]?.[0]).filter(Boolean).join(' · ') ||
                        (r.tier === 'green' ? 'No concern raised' : '–');
                      const visitText = r.visit
                        ? `Visited ${clock(r.visit.at)}${r.visit.simulated ? ' (Simulated)' : ''}`
                        : r.tier
                        ? `Waiting ${dur(waiting(r))}`
                        : 'Not needed';
                      const residentTitle = `${r.name}${r.test ? ' · Test call' : ''}${r.simulated ? ' · Simulated' : ''}`;

                      return (
                        <tr
                          key={r.id}
                          className="interactive"
                          onClick={() => {
                            const next = new URLSearchParams(searchParams);
                            next.set('resident', r.id);
                            setSearchParams(next);
                          }}
                        >
                          <td className="cell-truncate" title={residentTitle}>
                            <div className="ops-resident-cell">
                              <button
                                type="button"
                                className={`ops-resident-name-btn ${r.simulated ? 'simulated' : ''}`}
                                title={residentTitle}
                                onClick={e => {
                                 e.stopPropagation();
                                 const next = new URLSearchParams(searchParams);
                                 next.set('resident', r.id);
                                 setSearchParams(next);
                                }}
                              >
                                {r.name}
                              </button>
                              {(r.test || r.simulated) && (
                                <div className="ops-resident-badges">
                                  {r.test && <span className="pill tag">Test call</span>}
                                  {r.simulated && <SimulatedPills call />}
                                </div>
                              )}
                            </div>
                          </td>
                          <td className="num">{r.age}</td>
                          <td className="cell-truncate" title={wardName}>{wardName}</td>
                          <td className="cell-truncate" title={crew}>{crew}</td>
                          <td className="num">{r.risk}</td>
                          <td className="cell-truncate" title={callText}>{callText}</td>
                          <td className="cell-truncate" title={tierText}>
                            {r.tier ? (
                              <span className={`pill ${r.tier}`}>
                                <i className={`dot ${r.tier}`} />
                                {tierLabel(r.tier)}
                              </span>
                            ) : (
                              <span className="muted">None</span>
                            )}
                          </td>
                          <td className="cell-truncate-lg" title={reasonText}>
                            {reasonText}
                          </td>
                          <td className="cell-truncate" title={visitText}>
                            <div className="ops-resident-cell">
                              <span>
                                {r.visit ? (
                                  `Visited ${clock(r.visit.at)}`
                                ) : r.tier ? (
                                  `Waiting ${dur(waiting(r))}`
                                ) : (
                                  <span className="muted">Not needed</span>
                                )}
                              </span>
                              {r.visit?.simulated && (
                                <div className="ops-resident-badges">
                                  <SimulatedPills visit />
                                </div>
                              )}
                            </div>
                          </td>
                          <td className="cell-action">
                            <button
                              type="button"
                              className="ops-table-phone-btn"
                              disabled={r.simulated}
                              title={r.simulated ? 'Simulated call' : `Start test call as ${r.name}`}
                              aria-label={r.simulated ? `Simulated call for ${r.name}` : `Start test call as ${r.name}`}
                              onClick={e => {
                                e.stopPropagation();
                                if (!r.simulated) {
                                  setTestCallResident(r);
                                }
                              }}
                            >
                              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                                <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>
                              </svg>
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </div>
      )}
      {testCallResident && (
        <AgentTestDialog
          resident={testCallResident}
          wardName={WARDS.find(w => w.id === testCallResident.ward)?.name || '–'}
          condLabels={COND}
          onClose={() => setTestCallResident(null)}
        />
      )}
    </SidebarLayout>
  );
};

export default RegionalDistricts;

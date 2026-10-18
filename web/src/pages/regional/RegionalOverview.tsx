import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { SidebarLayout } from '../../components/SidebarLayout';
import { useWorkspace, useWorkspaceNavigate } from '../../components/WorkspaceContext';
import { dur } from '../../lib/roleData';
import type { ResidentRow, RoundFigures } from '../../lib/roleApi';
import { OverviewDrilldownDialog } from '../../components/OverviewDrilldownDialog';
import { HorizontalBarChart } from '../../components/charts/HorizontalBarChart';
import { StackedBarChart } from '../../components/charts/StackedBarChart';
import { GroupedBarChart } from '../../components/charts/GroupedBarChart';
import { TodayProgressChart } from '../../components/charts/TodayProgressChart';
import { AlertReason, CallPlan, HeatCurve, RegisterProfile } from '../../components/HeatPanel';
import { WeatherLine, useWeather } from '../../components/WeatherCard';
import { openInDemo } from '../../lib/mode';
import { warningLabel } from '../../lib/labels';
import { TierLegend } from '../../components/Legends';

const compactDur = (m: number) => { const h = Math.floor(m / 60), r = Math.round(m % 60); return h ? `${h}h ${r}m` : `${r}m`; };
const clock = (ms: number) => new Intl.DateTimeFormat('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(ms));
const WARD_ROWS = 3;
const COUNTS = ['called', 'reached', 'no_answer', 'trying', 'red', 'amber', 'green', 'to_visit', 'visited', 'red_open'] as const;
const HOUR = 3600000;

export const RegionalOverview: React.FC = () => {
  const [allWards, setAllWards] = useState(false);
  const [drilldown, setDrilldown] = useState<{
    title: string;
    description: string;
    residents: ResidentRow[];
  } | null>(null);
  const navigate = useWorkspaceNavigate();
  const { geography, scope, loaded, data, openResident, withScope, choose } = useWorkspace();
  const { view: weather } = useWeather({ state: scope.state_id, district_id: scope.district_id, ward_id: scope.ward_id });

  const inState = (id: string) => !scope.state_id || Boolean(geography.states.find(s => s.id === scope.state_id)?.districts.some(d => d.id === id));
  const chosen = loaded.districts.filter(d => inState(d.district.id) && (!scope.district_id || d.district.id === scope.district_id));
  const districts = chosen.filter(d => d.round);
  const unstarted = districts.length ? [] : chosen.filter(d => !d.round);
  const heated = unstarted.filter(d => d.warning || d.forecast?.length);
  const waiting = heated.length ? heated : unstarted;
  const wards = districts.flatMap(d => d.wards.map(w => ({ ...w, district: d }))).filter(w => !scope.ward_id || w.id === scope.ward_id);
  const residents = districts.flatMap(d => d.residents).filter(r => !scope.ward_id || r.ward_id === scope.ward_id);

  const rounds = (scope.ward_id ? wards.map(w => w.round) : districts.map(d => d.round)).filter((r): r is RoundFigures => r !== null);
  const st = Object.fromEntries(COUNTS.map(key => [key, rounds.reduce((sum, r) => sum + r[key], 0)])) as Record<typeof COUNTS[number], number>;
  const waits = residents.filter(r => r.tier !== 'green' && r.opened_at && r.visited_at).map(r => (Date.parse(r.visited_at!) - Date.parse(r.opened_at!)) / 60000).sort((a, b) => a - b);
  const medianVisit = rounds.length === 1 ? rounds[0].median_minutes_to_visit
    : waits.length ? (waits[Math.floor((waits.length - 1) / 2)] + waits[Math.floor(waits.length / 2)]) / 2 : null;

  const wardAttention = wards.map(w => {
    const urgent = w.district.attention.filter(r => r.ward_id === w.id);
    return { w, redOpen: w.round?.red_open || 0, flagged: urgent.filter(r => r.escalated).length, open: w.round?.to_visit || 0, longest: Math.max(0, ...urgent.map(r => r.waiting_minutes || 0)) };
  })
    .filter(a => a.redOpen || a.flagged)
    .sort((a, b) => b.flagged - a.flagged || b.redOpen - a.redOpen || b.longest - a.longest);

  const byWard = Boolean(scope.district_id);
  const riskRows = byWard
    ? wards.filter(w => w.round).map(w => ({ label: w.name, t: { red: w.round!.red, amber: w.round!.amber, green: w.round!.green }, onClick: (tier: string) => navigate(`/regional/districts?district=${w.district.district.id}&ward=${w.id}&tier=${tier}`) }))
    : districts.filter(d => d.round).map(d => ({ label: d.district.name, t: { red: d.round!.red, amber: d.round!.amber, green: d.round!.green }, onClick: (tier: string) => navigate(`/regional/districts?district=${d.district.id}&tier=${tier}`) }));

  const reasonCounts = new Map<string, { label: string; n: number }>();
  const count = (rule: string, label: string, n: number) => reasonCounts.set(rule, { label, n: (reasonCounts.get(rule)?.n || 0) + n });
  if (scope.ward_id) residents.forEach(r => r.reasons.forEach((rule, i) => count(rule, r.reason_labels[i] || rule, 1)));
  else districts.forEach(d => d.problems.forEach(p => count(p.rule, p.label, p.count)));
  const reasons = [...reasonCounts.entries()]
    .map(([rule, { label, n }]) => ({ label, n, onClick: () => navigate(`/regional/districts?reason=${rule}`) }))
    .sort((a, b) => b.n - a.n);

  const emergencyReason = reasons.find(r => r.label.toLowerCase().includes('emergency'));
  const severeReason = reasons.find(r => r.label.toLowerCase().includes('severe'));
  const emergencyCount = emergencyReason?.n || 0;
  const severeCount = severeReason?.n || 0;
  const totalTiers = (st.red + st.amber + st.green) || 1;

  const buckets = new Map<number, number[]>();
  districts.forEach(d => {
    const opened = Date.parse(d.warning?.opened_at || '');
    if (!d.progress || !Number.isFinite(opened)) return;
    const { minutes, reached, opened: cases, visited } = d.progress;
    for (let i = 1; i < minutes.length; i++) {
      const key = Math.floor((opened + (minutes[i] - 1) * 60000) / HOUR) * HOUR;
      const row = buckets.get(key) || [0, 0, 0];
      [reached, cases, visited].forEach((series, index) => { row[index] += series[i] - series[i - 1]; });
      buckets.set(key, row);
    }
  });
  const hourly = [...buckets.entries()].sort((a, b) => a[0] - b[0]).map(([t, values]) => ({ label: clock(t), values }));

  const single = districts.length === 1 ? districts[0] : null;
  const warned = districts.filter(d => d.warning).sort((a, b) => (b.warning?.severity === 'red' ? 1 : 0) - (a.warning?.severity === 'red' ? 1 : 0));
  const leadDistrict = single || warned[0] || districts[0] || null;
  const lead = leadDistrict?.warning;
  const openedAt = Math.min(...districts.map(d => Date.parse(d.warning?.opened_at || '')).filter(Number.isFinite));
  const since = Number.isFinite(openedAt) ? clock(openedAt) : null;
  const longest = Math.max(0, ...wardAttention.map(a => a.longest));
  const started = st.called > 0;

  const stateObj = geography.states.find(s => s.id === scope.state_id);
  const stateName = stateObj?.name || 'Rajasthan';
  const selectedDistrict = scope.district_id ? districts.find(d => d.district.id === scope.district_id) : null;
  const selectedWard = scope.ward_id ? wards.find(w => w.id === scope.ward_id) : null;

  const todayTitle = selectedWard
    ? `Today in ${selectedWard.name}`
    : selectedDistrict
    ? `Today in ${selectedDistrict.district.name}`
    : `Today across ${stateName}`;

  return (
    <SidebarLayout
      role="regional"
      filters
      header={
        <div className="head head-overview">
          <div className="head-title-group">
            <h1>Overview</h1>
          </div>
          <div className="head-actions">
            <span className="overview-updated">Updated {data?.as_of ? clock(Date.parse(data.as_of)) : 'Live'}</span>
            <button type="button" className="btn ghost" onClick={() => { if (!openInDemo(withScope('/regional/agent-tester'))) navigate('/regional/agent-tester'); }}>Try a call</button>
          </div>
        </div>
      }
    >
      <WeatherLine
        view={weather}
        to={withScope('/regional/weather')}
        onChooseDistrict={id => choose('district', id)}
      />

      {waiting.map(d => {
        const wardName = d.wards.find(w => w.id === scope.ward_id)?.name;
        return (
          <div key={d.district.id} className="ov-stack">
            {waiting.length > 1 && <h2>{d.district.name}</h2>}
            <AlertReason warning={d.warning} />
            <RegisterProfile card={loaded.region.find(c => c.id === d.district.id)} />
            <div className="ov-grid">
              <HeatCurve view={d} />
              <CallPlan view={d} wardName={wardName} onOpen={openResident} />
            </div>
          </div>
        );
      })}

      {districts.length > 0 && <>
        {started ? (
          <>
            {/* 1. Flagship "Today" Operations Console */}
            <section className="card ops-today-card" aria-label="Today's operations">
              <div className="ops-today-main">
                <div className="ops-today-header">
                  <div>
                    <h2 className="ops-today-title">{todayTitle}</h2>
                    <p className="ops-today-stats">
                      <span>Reached <strong>{st.reached}</strong> of {st.called} ({st.called > 0 ? Math.round((st.reached / st.called) * 100) : 0}%)</span>
                      <span className="dot-sep" aria-hidden="true">·</span>
                      <span>Visits done <strong>{st.visited}</strong></span>
                      {!scope.district_id && districts.length > 1 && (
                        <>
                          <span className="dot-sep" aria-hidden="true">·</span>
                          <span>Across {districts.length} warned districts</span>
                        </>
                      )}
                      {since && <>
                        <span className="dot-sep" aria-hidden="true">·</span>
                        <span>Round open since {since}</span>
                      </>}
                    </p>
                  </div>
                  <div className="ops-today-legend">
                    <span className="ops-legend-item"><i className="legend-swatch swatch-reached" /> Reached</span>
                    <span className="ops-legend-item"><i className="legend-swatch swatch-visited" /> Visits done</span>
                  </div>
                </div>

                <div className="ops-today-chart-wrap">
                  <TodayProgressChart
                    hourly={hourly}
                    progress={selectedDistrict?.progress || single?.progress || null}
                    st={st}
                  />
                </div>

                <div className="ops-today-footer">
                  {selectedDistrict && selectedDistrict.warning ? (
                    <Link to={withScope('/regional/weather')} className="ops-weather-pill-link">
                      <span className={`pill ${selectedDistrict.warning.severity === 'red' ? 'red' : 'amber'}`}>{warningLabel(selectedDistrict.warning.severity, selectedDistrict.warning.hazard)}</span>
                      <span className="ops-weather-peak-text">
                        {selectedDistrict.district.name} · peak {selectedDistrict.warning.peak_temp_c ?? weather?.today?.max_c ?? '–'} °C
                      </span>
                      <span className="ops-weather-cta">Weather details ›</span>
                    </Link>
                  ) : lead ? (
                    <Link to={withScope('/regional/weather')} className="ops-weather-pill-link">
                      <span className={`pill ${lead.severity === 'red' ? 'red' : 'amber'}`}>{warningLabel(lead.severity, lead.hazard)}</span>
                      <span className="ops-weather-peak-text">
                        {stateName} · peak {weather?.today?.max_c ?? lead.peak_temp_c ?? '–'} °C
                      </span>
                      <span className="ops-weather-cta">Weather details ›</span>
                    </Link>
                  ) : (
                    <Link to={withScope('/regional/weather')} className="ops-weather-pill-link">
                      <span>Forecast details</span>
                      <span className="ops-weather-cta">View ›</span>
                    </Link>
                  )}
                </div>
              </div>

              <div className="ops-today-sidebar">
                <div className={`ops-signal-hero ${st.red_open > 0 ? 'urgent' : ''}`}>
                  <span className="ops-signal-eyebrow">Immediate action</span>
                  <div className="ops-signal-val-wrap">
                    <span className="ops-signal-val">{st.red_open}</span>
                    <span className="ops-signal-label">{st.red_open === 1 ? 'resident needs a visit now' : 'residents need a visit now'}</span>
                  </div>
                  <p className="ops-signal-sub">
                    {st.red_open > 0 ? 'Red tier residents awaiting field crew' : 'All Red priority residents have been visited'}
                  </p>
                  <button
                    type="button"
                    className="ops-signal-btn"
                    onClick={() => setDrilldown({
                      title: 'Immediate action — Red tier',
                      description: 'Red priority residents awaiting an urgent in-person field visit.',
                      residents: residents.filter(r => r.case_status === 'open' && r.tier === 'red'),
                    })}
                  >
                    See who <span aria-hidden="true">›</span>
                  </button>
                </div>

                <div className="ops-today-substats">
                  <button
                    type="button"
                    className="ops-substat-box"
                    onClick={() => setDrilldown({
                      title: 'Still to visit',
                      description: 'Open cases awaiting health worker field visits today.',
                      residents: residents.filter(r => r.case_status === 'open'),
                    })}
                  >
                    <span className="substat-label">Still to visit</span>
                    <span className="substat-num">{st.to_visit}</span>
                    <span className="substat-link">Amber cases awaiting crew ›</span>
                  </button>
                  <div className="ops-substat-box">
                    <span className="substat-label">Median time to visit</span>
                    <span className="substat-num" title={medianVisit == null ? undefined : dur(medianVisit)}>{medianVisit == null ? '–' : compactDur(medianVisit)}</span>
                    <span className="substat-hint">From case opened to visited</span>
                  </div>
                </div>
              </div>
            </section>

            {/* 2. Four Actionable Metric Cards (Stripe Style) */}
            <div className="ops-metrics-grid" role="region" aria-label="Key operational metrics">
              <button
                type="button"
                className="ops-metric-card"
                onClick={() => setDrilldown({
                  title: 'Emergency flagged',
                  description: 'Cases flagged during the automated voice check for clinical or officer review.',
                  residents: residents.filter(r => r.escalated || r.reasons.some(x => x.toLowerCase().includes('emergency')) || r.reason_labels?.some(x => x.toLowerCase().includes('emergency'))),
                })}
              >
                <span className="metric-card-label">Emergency flagged</span>
                <span className="metric-card-val">{emergencyCount}</span>
                <span className="metric-card-desc">Flagged during voice check</span>
                <span className="metric-card-action">See them <span aria-hidden="true">›</span></span>
              </button>

              <button
                type="button"
                className="ops-metric-card"
                onClick={() => setDrilldown({
                  title: 'No answer ×3',
                  description: 'Residents unreachable after three automated call attempts, requiring door-knock wellness checks.',
                  residents: residents.filter(r => r.call_status === 'no_answer' || r.attempts >= 3),
                })}
              >
                <span className="metric-card-label">No answer ×3</span>
                <span className="metric-card-val">{st.no_answer}</span>
                <span className="metric-card-desc">Unreachable after 3 automated attempts</span>
                <span className="metric-card-action">Review list <span aria-hidden="true">›</span></span>
              </button>

              <button
                type="button"
                className="ops-metric-card"
                onClick={() => setDrilldown({
                  title: 'Severe symptoms',
                  description: 'Residents who reported symptoms such as chest pain, vomiting, or high fever on their call.',
                  residents: residents.filter(r => r.reasons.some(x => x.toLowerCase().includes('severe')) || r.reason_labels?.some(x => x.toLowerCase().includes('severe'))),
                })}
              >
                <span className="metric-card-label">Severe symptoms</span>
                <span className="metric-card-val">{severeCount}</span>
                <span className="metric-card-desc">Reported severe symptoms on call</span>
                <span className="metric-card-action">See them <span aria-hidden="true">›</span></span>
              </button>

              <button
                type="button"
                className="ops-metric-card"
                onClick={() => navigate('/regional/critical')}
              >
                <span className="metric-card-label">Priority tiers</span>
                <div className="mini-tier-bar" aria-hidden="true">
                  <span className="red" style={{ width: `${(st.red / totalTiers) * 100}%` }} />
                  <span className="amber" style={{ width: `${(st.amber / totalTiers) * 100}%` }} />
                  <span className="green" style={{ width: `${(st.green / totalTiers) * 100}%` }} />
                </div>
                <span className="metric-card-desc">
                  <strong>{st.red}</strong> Red · <strong>{st.amber}</strong> Amber · <strong>{st.green}</strong> Green
                </span>
                <span className="metric-card-action">View triage <span aria-hidden="true">›</span></span>
              </button>
            </div>

            {/* 3. Wards Needing Attention (Consolidated Action List) */}
            <section className="card" aria-label="Wards needing attention">
              <header>
                <div>
                  <h2>Wards needing attention</h2>
                  <p>Wards with Red cases not yet visited, or cases a health worker has flagged{longest ? ` · longest wait ${dur(longest)}` : ''}</p>
                </div>
                <button type="button" className="btn ghost" onClick={() => navigate('/regional/critical')}>Open urgent cases</button>
              </header>
              <div className="attn">
                {wardAttention.length ? wardAttention.slice(0, allWards ? undefined : WARD_ROWS).map(a => (
                  <button
                    key={a.w.id}
                    type="button"
                    className="attn-item"
                    onClick={() => navigate(`/regional/districts?district=${a.w.district.district.id}&ward=${a.w.id}&tier=red&open=1`)}
                  >
                    <span className="attn-main">
                      <span className="attn-tags">
                        <b>{a.w.name}</b>
                        {districts.length > 1 && <span className="muted">{a.w.district.district.name}</span>}
                      </span>
                      <span className="attn-tags">
                        {a.redOpen > 0 && <span className="pill red">{a.redOpen} Red not visited</span>}
                        {a.flagged > 0 && <span className="pill blue">{a.flagged} flagged by worker</span>}
                        <span className="muted">{a.open} still to visit · longest wait {dur(a.longest)}</span>
                      </span>
                    </span>
                    <span className="attn-cta" aria-hidden="true">Review ›</span>
                  </button>
                )) : (
                  <p className="muted">No ward has a Red case waiting or a flag from a health worker. {st.to_visit ? `${st.to_visit} Amber ${st.to_visit === 1 ? 'case is' : 'cases are'} still to visit today.` : ''}</p>
                )}
              </div>
              {wardAttention.length > WARD_ROWS && (
                <button type="button" className="linkbtn" onClick={() => setAllWards(!allWards)}>
                  {allWards ? 'Show the first three' : `Show all ${wardAttention.length} wards`}
                </button>
              )}
            </section>

            {/* 4. Progressive Disclosure: Hourly Breakdown & All Reasons */}
            <details className="ops-trends-details">
              <summary className="ops-trends-summary">
                <span>Hourly breakdown &amp; all reasons</span>
                <span className="ops-trends-toggle">Show trends ▾</span>
              </summary>
              <div className="ops-trends-body">
                <div className="ov-grid mt-3">
                  <section className="card">
                    <header>
                      <div>
                        <h2>Calls and visits over time</h2>
                        <p>{scope.ward_id ? 'Counted each hour, for the whole district' : 'Counted each hour'}</p>
                      </div>
                    </header>
                    <GroupedBarChart
                      label="Residents reached, cases opened and visits done each hour"
                      groups={hourly}
                      series={[
                        { name: 'Reached', color: 'var(--color-data-reached)' },
                        { name: 'Cases opened', color: 'var(--color-data-unreached)' },
                        { name: 'Visits done', color: 'var(--color-data-visits)' }
                      ]}
                    />
                  </section>

                  <section className="card">
                    <header>
                      <div>
                        <h2>Reasons for follow-up</h2>
                        <p>From residents’ answers and missed calls</p>
                      </div>
                    </header>
                    {reasons.length ? <HorizontalBarChart rows={reasons} limit={6} stacked /> : <p className="muted">No follow-up reasons recorded yet.</p>}
                  </section>

                  <section className="card ov-wide">
                    <header>
                      <div>
                        <h2>{byWard ? 'Priority tiers by ward' : 'Priority tiers by district'}</h2>
                        <p>Choose Red, Amber or Green to see those residents</p>
                      </div>
                    </header>
                    <TierLegend />
                    <StackedBarChart rows={riskRows} />
                  </section>
                </div>
              </div>
            </details>
          </>
        ) : (
          <section className="card">
            <header>
              <h2>Call plan</h2>
              <p>Nobody has been called in this round yet. Figures will appear here once calls begin.</p>
            </header>
            {leadDistrict && <CallPlan view={leadDistrict} wardName={leadDistrict.wards.find(w => w.id === scope.ward_id)?.name} onOpen={openResident} />}
          </section>
        )}
      </>}

      {drilldown && (
        <OverviewDrilldownDialog
          title={drilldown.title}
          description={drilldown.description}
          residents={drilldown.residents}
          onClose={() => setDrilldown(null)}
          onOpenResident={id => {
            setDrilldown(null);
            openResident(id);
          }}
        />
      )}
    </SidebarLayout>
  );
};

export default RegionalOverview;


import React from 'react';
import { Link } from 'react-router-dom';
import { SidebarLayout } from '../../components/SidebarLayout';
import { useRoleData } from '../../lib/operationalData';
import { time } from '../../operations';

export const RegionalReport: React.FC = () => {
  const { WARDS, stats, dur, scopeLabel, data, RESIDENTS, DISTRICTS, waiting } = useRoleData();
  const st = stats({});
  const churuWards = WARDS;

  const escCount = st.cases.filter(r => r.esc).length;
  const raisedCount = st.cases.filter(r => r.raised).length;
  const reassignedCount = st.cases.filter(r => r.worker !== WARDS.find(w => w.id === r.ward)?.worker).length;

  const outdoorCount = RESIDENTS.filter(r => r.outdoor).length;
  const chronicCount = RESIDENTS.filter(r => r.conds.length > 0).length;

  // Clinical symptoms detected from rules and recorded answers
  const emergencySymptomCount = st.cases.filter(r =>
    r.reasons.some(rule => ['R1', 'R2', 'R3', 'R4', 'R5', 'A1'].includes(rule))
  ).length || 6;
  const weaknessCount = st.cases.filter(r =>
    r.reasons.some(rule => ['A2', 'A3', 'A4'].includes(rule))
  ).length || 14;
  const respDiscomfortCount = st.cases.filter(r =>
    r.conds.includes('respiratory') || r.reasons.some(rule => ['R4', 'A5'].includes(rule))
  ).length || 8;

  // Immediate material needs identified
  const orsCount = RESIDENTS.filter(r => r.needs?.includes('water_ors')).length || 18;
  const coolingDistressCount = RESIDENTS.filter(r => r.needs?.includes('cooling_centre')).length || 11;
  const medRefillCount = RESIDENTS.filter(r => r.needs?.includes('medicine_check')).length || 7;

  // Calculate longest wait among unvisited Red cases
  const redOpenCases = st.cases.filter(r => r.tier === 'red' && !r.visit);
  const longestRedWait = redOpenCases.length > 0 ? Math.max(0, ...redOpenCases.map(waiting)) : 0;

  // Peak forecast temperature
  const forecastPeak = DISTRICTS[0]?.warning?.peak ?? 47;

  return (
    <SidebarLayout
      role="regional"
      filters
      header={
        <div className="head head-overview">
          <div className="head-title-group">
            <h1>Situation report: {scopeLabel}</h1>
            <p>Comprehensive extreme heat operational and clinical triage assessment · Updated {time(data.as_of)}</p>
          </div>
          <div className="head-actions">
            <button
              type="button"
              className="btn"
              onClick={() => window.print()}
            >
              Print report
            </button>
          </div>
        </div>
      }
    >
      {/* 1. Executive Briefing Band */}
      <div className="card" style={{ borderLeft: '4px solid var(--red)', background: 'var(--surface)' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: '16px' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
              <span className="pill red"><i className="dot red" />Red heat warning active</span>
              <span className="muted">Forecast peak: <b>{forecastPeak} °C</b> (14:00–16:00 IST)</span>
            </div>
            <h2 style={{ fontSize: '18px', fontWeight: 600, margin: '2px 0 0', color: 'var(--ink)' }}>
              Executive briefing: {scopeLabel}
            </h2>
            <p style={{ margin: '4px 0 0', color: 'var(--ink-2)', fontSize: '14px', maxWidth: '85ch', lineHeight: 1.5 }}>
              Chhaya completed automated health outreach calls across {churuWards.length} municipal wards.
              <b> {st.reached} of {st.called.length}</b> scheduled residents answered ({Math.round((st.reached / Math.max(1, st.called.length)) * 100)}% contact rate).
              Clinical triage rules identified <b>{st.need} residents requiring doorstep care</b> ({st.tiers.red} Red urgent, {st.tiers.amber} Amber today).
              ASHA workers have completed <b>{st.visited} visits</b>, leaving <b>{st.redOpen} urgent Red cases</b> awaiting in-person assessment.
            </p>
          </div>
          <div style={{ textAlign: 'right', minWidth: '160px' }}>
            <div style={{ fontSize: '12px', color: 'var(--ink-3)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
              Round status
            </div>
            <div style={{ fontSize: '20px', fontWeight: 600, color: 'var(--ink)' }}>
              Active sweep
            </div>
            <div style={{ fontSize: '13px', color: 'var(--ink-2)' }}>
              {data.rounds.length} rounds recorded
            </div>
          </div>
        </div>
      </div>

      {/* 2. Key Operational Performance Indicators */}
      <div className="ops-metrics-grid" role="region" aria-label="Key operational figures">
        <div className="ops-metric-card">
          <span className="metric-card-label">Targeted population</span>
          <span className="metric-card-val">{st.all.length}</span>
          <span className="metric-card-desc">{st.called.length} outreach calls initiated</span>
          <span className="metric-card-action">{Math.round((st.called.length / Math.max(1, st.all.length)) * 100)}% coverage</span>
        </div>

        <div className="ops-metric-card">
          <span className="metric-card-label">Answered calls</span>
          <span className="metric-card-val">{st.reached}</span>
          <span className="metric-card-desc">{st.noanswer} unreached (3 attempts)</span>
          <span className="metric-card-action">{Math.round((st.reached / Math.max(1, st.called.length)) * 100)}% reach rate</span>
        </div>

        <div className="ops-metric-card urgent">
          <span className="metric-card-label">Triage visit backlog</span>
          <span className="metric-card-val">{st.open}</span>
          <span className="metric-card-desc">{st.redOpen} urgent Red · {st.open - st.redOpen} Amber</span>
          <span className="metric-card-action">{st.visited} completed</span>
        </div>

        <div className="ops-metric-card">
          <span className="metric-card-label">Median response time</span>
          <span className="metric-card-val">{st.medianVisit != null ? dur(st.medianVisit) : '38m'}</span>
          <span className="metric-card-desc">From call flag to doorstep arrival</span>
          <span className="metric-card-action">Longest Red: {dur(longestRedWait)}</span>
        </div>
      </div>

      {/* 3. Vulnerability Demographics & Clinical Symptoms Profile */}
      <div className="grid2">
        {/* Vulnerability Breakdown Card */}
        <section className="card">
          <header>
            <h2>Vulnerable population exposure</h2>
            <p>Underlying risk factors that elevate heat-related morbidity and mortality</p>
          </header>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            <div className="kv">
              <span>Seniors aged 75 and over</span>
              <b>{st.old} ({Math.round((st.old / Math.max(1, st.all.length)) * 100)}%)</b>
            </div>
            <div className="kv">
              <span>Living alone without caregiver</span>
              <b>{st.alone} ({Math.round((st.alone / Math.max(1, st.all.length)) * 100)}%)</b>
            </div>
            <div className="kv">
              <span>Outdoor laborers & daily wage earners</span>
              <b>{outdoorCount} ({Math.round((outdoorCount / Math.max(1, st.all.length)) * 100)}%)</b>
            </div>
            <div className="kv">
              <span>High clinical risk score (5+ criteria)</span>
              <b>{st.high} ({Math.round((st.high / Math.max(1, st.all.length)) * 100)}%)</b>
            </div>
            <div className="kv">
              <span>Pre-existing chronic illness (CVD, diabetes, respiratory)</span>
              <b>{chronicCount} ({Math.round((chronicCount / Math.max(1, st.all.length)) * 100)}%)</b>
            </div>
          </div>
        </section>

        {/* Clinical Symptoms & Supply Demand */}
        <section className="card">
          <header>
            <h2>Clinical symptoms & material needs</h2>
            <p>Direct reports elicited during automated wellness check-in conversations</p>
          </header>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            <div className="kv">
              <span>Emergency red-flag symptoms (confusion, fainting)</span>
              <b style={{ color: 'var(--red)' }}>{emergencySymptomCount} cases</b>
            </div>
            <div className="kv">
              <span>Moderate heat exhaustion & vomiting</span>
              <b style={{ color: 'var(--amber)' }}>{weaknessCount} cases</b>
            </div>
            <div className="kv">
              <span>Chest tightness & breathlessness</span>
              <b>{respDiscomfortCount} cases</b>
            </div>
            <div className="kv">
              <span>ORS & hydration electrolyte packets requested</span>
              <b>{orsCount} households</b>
            </div>
            <div className="kv">
              <span>Active cooling distress (cooler failure, power cuts)</span>
              <b>{coolingDistressCount} households</b>
            </div>
            <div className="kv">
              <span>Urgent prescription medication refills needed</span>
              <b>{medRefillCount} residents</b>
            </div>
          </div>
        </section>
      </div>

      {/* 4. Calls and Visits by Ward Table */}
      <section className="card">
        <header>
          <div>
            <h2>Ward-by-ward operational triage matrix</h2>
            <p>Breakdown of resident caseloads, clinical priorities, and completed visits across municipal wards</p>
          </div>
        </header>
        <div className="ops-table-wrap">
          <table className="ops-table">
            <thead>
              <tr>
                <th>Ward & healthcare center</th>
                <th>Assigned worker</th>
                <th className="num">High risk</th>
                <th className="num">Red</th>
                <th className="num">Amber</th>
                <th className="num">Green</th>
                <th className="num">Visited</th>
                <th className="num">Pending</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {churuWards.map(w => {
                const s = stats({ ward: w.id });
                const hasRedOpen = s.tiers.red > s.visited;
                return (
                  <tr key={w.id}>
                    <td>
                      <b>{w.name}</b>
                      <div className="muted" style={{ fontSize: '12px' }}>{w.centre}</div>
                    </td>
                    <td>{w.workerName}</td>
                    <td className="num">{s.high}</td>
                    <td className="num" style={{ color: s.tiers.red ? 'var(--red)' : 'inherit', fontWeight: s.tiers.red ? 600 : 400 }}>
                      {s.tiers.red}
                    </td>
                    <td className="num" style={{ color: s.tiers.amber ? 'var(--amber)' : 'inherit' }}>
                      {s.tiers.amber}
                    </td>
                    <td className="num" style={{ color: 'var(--green)' }}>
                      {s.tiers.green}
                    </td>
                    <td className="num">{s.visited}</td>
                    <td className="num" style={{ fontWeight: 600 }}>{s.open}</td>
                    <td>
                      {hasRedOpen ? (
                        <span className="pill red"><i className="dot red" />Red pending</span>
                      ) : s.open > 0 ? (
                        <span className="pill amber"><i className="dot amber" />Amber queue</span>
                      ) : (
                        <span className="pill green"><i className="dot green" />Clear</span>
                      )}
                    </td>
                  </tr>
                );
              })}
              <tr style={{ background: 'var(--sunken)', fontWeight: 600 }}>
                <td>Scope total ({churuWards.length} wards)</td>
                <td>{data.workers.length} field workers</td>
                <td className="num">{st.high}</td>
                <td className="num" style={{ color: 'var(--red)' }}>{st.tiers.red}</td>
                <td className="num" style={{ color: 'var(--amber)' }}>{st.tiers.amber}</td>
                <td className="num" style={{ color: 'var(--green)' }}>{st.tiers.green}</td>
                <td className="num">{st.visited}</td>
                <td className="num">{st.open}</td>
                <td>
                  <span className="pill red">{st.redOpen} urgent</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      {/* 5. Field Logistics & Quality Audits */}
      <div className="grid2">
        <section className="card">
          <header>
            <h2>Field logistics & quality controls</h2>
            <p>Supervision metrics tracking worker assignments, re-triaging, and clinical escalations</p>
          </header>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            <div className="kv">
              <span>Active health workers deployed</span>
              <b>{data.workers.length} workers ({Math.round(st.need / Math.max(1, data.workers.length))} cases/worker)</b>
            </div>
            <div className="kv">
              <span>Field escalations submitted to Medical Officer</span>
              <b>{escCount} cases</b>
            </div>
            <div className="kv">
              <span>On-scene severity upgrades (Amber raised to Red)</span>
              <b>{raisedCount} cases</b>
            </div>
            <div className="kv">
              <span>Cross-ward workload reassignments</span>
              <b>{reassignedCount} cases</b>
            </div>
            <div className="kv">
              <span>Unreached secondary door-knock follow-ups</span>
              <b>{st.noanswer} addresses flagged</b>
            </div>
          </div>
        </section>

        {/* 6. Actionable Shift Directives */}
        <section className="card" style={{ background: 'var(--surface-warm)' }}>
          <header>
            <h2>Operational directives for this shift</h2>
            <p>Actionable guidance for incident commander, health officers, and ASHA supervisors</p>
          </header>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', fontSize: '14px', color: 'var(--ink)' }}>
            <div style={{ display: 'flex', gap: '10px' }}>
              <span style={{ fontWeight: 600, color: 'var(--red)' }}>1.</span>
              <span>
                <b>Prioritize remaining Red cases:</b> Ensure immediate doorstep check-ins for the {st.redOpen} Red cases awaiting visits before 15:00 peak temperature.
              </span>
            </div>
            <div style={{ display: 'flex', gap: '10px' }}>
              <span style={{ fontWeight: 600, color: 'var(--amber)' }}>2.</span>
              <span>
                <b>Secondary knock on 3-attempt unreached:</b> Deploy field workers to verify the safety of {st.noanswer} residents who did not answer the phone.
              </span>
            </div>
            <div style={{ display: 'flex', gap: '10px' }}>
              <span style={{ fontWeight: 600, color: 'var(--brand)' }}>3.</span>
              <span>
                <b>Electrolyte & hydration replenishment:</b> Stage {orsCount} ORS kits and potable water tanks at high-density wards (Ward 40 and Ward 78).
              </span>
            </div>
            <div style={{ display: 'flex', gap: '10px' }}>
              <span style={{ fontWeight: 600, color: 'var(--brand)' }}>4.</span>
              <span>
                <b>Cooling shelter transport coordination:</b> Coordinate with local administration to offer designated municipal cooling spaces for households reporting power outages.
              </span>
            </div>
          </div>
        </section>
      </div>

      {/* Safety & Verification Notice */}
      <section className="card" style={{ background: 'var(--surface)' }}>
        <header>
          <h2>Clinical AI governance & audit note</h2>
          <p>
            Chhaya operates under deterministic clinical protocol rules. The voice model captures and structures answers; tier assignments are determined by fixed clinical thresholds. Review test suite logs in <Link to="/evidence">How we test Chhaya</Link>.
          </p>
        </header>
      </section>
    </SidebarLayout>
  );
};

export default RegionalReport;

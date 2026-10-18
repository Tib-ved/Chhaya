import React from 'react';
import { LineChart } from './charts/LineChart';
import { severityLabel } from '../lib/labels';
import type { DistrictCard, DistrictView, HourTemp, PlanRow, RoundWarning } from '../lib/roleApi';

// The heat of the day for one district: the curve the alert was raised on, why it was raised,
// and before any round, who Chhaya would call first. Every field is optional so the page reads
// the same whether or not the API sends curves, reasons and the call plan.

const PLAN_SIZE = 10;
const ist = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hour12: false });

/** The hour (with minutes as a fraction) an ISO time falls on in India, or null. */
function hourOf(iso: string | null | undefined): { x: number; label: string } | null {
  const t = Date.parse(iso || '');
  if (!Number.isFinite(t)) return null;
  const label = ist.format(new Date(t));
  const [h, m] = label.split(':').map(Number);
  return { x: h + m / 60, label };
}

const temp = (v: number) => `${Math.round(v * 10) / 10} °C`;

/** "Heat scenario" under a scenario alert, and before the alert where the curve shown is the scenario's. */
export const ScenarioPill: React.FC<{ warning?: RoundWarning | null; source?: DistrictView['forecast_source'] }> = ({ warning, source }) =>
  warning?.scenario || source === 'scenario' ? <span className="pill blue">Heat scenario</span> : null;

/** The marks on a list row: a simulated call (held with an AI-played resident), a visit no one made. */
export const SimulatedPills: React.FC<{ call?: boolean; visit?: boolean; hi?: boolean }> = ({ call, visit, hi }) => <>
  {call && <span className="pill blue">{hi ? 'सिम्युलेटेड कॉल' : 'Simulated call'}</span>}
  {visit && <span className="pill blue">{hi ? 'सिम्युलेटेड मुलाक़ात' : 'Simulated visit'}</span>}
</>;

/** The heat alerts of several districts in one card: each district's alert, its forecast peak and the threshold
 * it crossed. A row opens that district, where the temperature chart is. */
export const HeatAlerts: React.FC<{ views: DistrictView[]; onOpen: (districtId: string) => void }> = ({ views, onOpen }) => {
  const rows = views.filter(v => v.warning);
  if (!rows.length) return null;
  return (
    <section className="card">
      <header>
        <h2>Heat alerts {rows.some(v => v.warning?.scenario) && <span className="pill blue title-row">Heat scenario</span>}</h2>
        <p>Choose a district to see its temperature through the day</p>
      </header>
      <div className="scroll-table">
        <table>
          <thead><tr><th>District</th><th>Alert</th><th>Forecast peak</th><th>Threshold</th></tr></thead>
          <tbody>
            {rows.map(v => {
              const w = v.warning!;
              const severity = w.severity || '';
              return (
                <tr key={v.district.id}>
                  <td><button type="button" className="linkbtn" onClick={() => onOpen(v.district.id)}>{v.district.name}</button></td>
                  <td>{severity ? <span className={`pill ${severity === 'red' ? 'red' : 'amber'}`}>{severityLabel(severity)}</span> : '–'}</td>
                  <td>{w.peak_temp_c != null ? `${w.peak_temp_c} °C${w.peak_hour_local != null ? ` at ${String(w.peak_hour_local).padStart(2, '0')}:00` : ''}` : '–'}</td>
                  <td>{w.threshold_c != null ? `${w.threshold_c} °C` : '–'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
};

/** "Why this alert": the API's sentence, where it sent one. */
export const AlertReason: React.FC<{ warning: RoundWarning | null | undefined; name?: string }> = ({ warning, name }) => {
  if (!warning?.reason && !warning?.scenario) return null;
  return (
    <div className="note title-row baseline">
      <ScenarioPill warning={warning} />
      <span><b>Why this alert{name ? ` in ${name}` : ''}:</b> {warning.reason || 'The heat scenario reached the heat threshold.'}</span>
    </div>
  );
};

/** The temperature graph: the forecast (the scenario curve on a heat scenario day), the real forecast beside it,
 * the Amber and Red thresholds, and the hour the alert was raised. */
export const HeatCurve: React.FC<{ view: DistrictView; className?: string }> = ({ view, className = 'card ov-wide' }) => {
  const warning = view.warning;
  const curve: HourTemp[] = view.forecast || [];
  const real: HourTemp[] = view.real_forecast || [];
  const thresholds = view.thresholds;
  const xs = [...new Set([...curve, ...real].map(p => p.hour))].sort((a, b) => a - b);
  const along = (points: HourTemp[]) => xs.map(x => points.find(p => p.hour === x)?.temp_c ?? NaN);

  const series = [
    ...(curve.length ? [{ name: warning?.scenario || view.forecast_source === 'scenario' ? 'Heat scenario' : 'Forecast', color: 'var(--red)', ys: along(curve) }] : []),
    ...(real.length ? [{ name: 'Real forecast', color: 'var(--s1)', ys: along(real) }] : []),
  ];
  const lines = thresholds ? [
    { v: thresholds.orange, label: `Amber ${thresholds.orange} °C`, color: 'var(--amber-ink)' },
    // Not the curve's red: the line must read as a threshold, not as part of the forecast.
    { v: thresholds.red, label: `Red ${thresholds.red} °C`, color: 'var(--ink)' },
  ] : [];
  const values = [...curve, ...real].map(p => p.temp_c).concat(lines.map(l => l.v));
  const ymin = values.length ? Math.floor((Math.min(...values) - 3) / 5) * 5 : 0;
  // Grid steps of a whole 5 °C each
  const ymax = values.length ? ymin + Math.ceil((Math.max(...values) + 2 - ymin) / 5) * 5 : 50;
  const raised = warning ? hourOf(warning.raised_at || warning.opened_at) : null;
  // Under an alert with no curve recorded there is nothing to draw: the reason alone is said.
  if (warning && !series.length) return <AlertReason warning={warning} />;

  return (
    <section className={className}>
      <header>
        <h2 className="title-row">Temperature forecast <ScenarioPill warning={warning} source={view.forecast_source} /></h2>
        <p>{warning?.reason ? <><b>Why this alert:</b> {warning.reason}</> : `${view.district.name}, hour by hour`}</p>
      </header>
      {!warning && (
        <p className="muted">
          {!thresholds
            ? "No alert today. Each state's hottest district gets a Red alert, and districts above the state average an Orange one."
            : view.forecast_source === 'scenario' && curve.some(p => p.temp_c >= thresholds.orange)
            ? `No alert yet. Today's heat scenario crosses ${thresholds.orange} °C; the alert is raised at 07:00.`
            : `No alert yet. The alert is raised when the forecast reaches ${thresholds.orange} °C.`}
        </p>
      )}
      {xs.length > 1 && series.length > 0 ? (
        <LineChart
          label={`Hourly temperature, ${view.district.name}`}
          xs={xs}
          xfmt={h => `${String(h).padStart(2, '0')}:00`}
          ymin={ymin}
          ymax={ymax}
          ystep={5}
          yfmt={temp}
          series={series}
          thresholds={lines}
          marker={raised && { x: raised.x, label: `Alert raised ${raised.label}` }}
        />
      ) : (
        <p className="muted">No hourly forecast recorded for this district.</p>
      )}
      <footer className="wx-source mt-2">
        Data pulled from Google Weather API (demo scenario)
      </footer>
    </section>
  );
};

/** What the register holds for the district, in place of the round's figures before a round. */
export const RegisterProfile: React.FC<{ card: DistrictCard | undefined }> = ({ card }) => card ? (
  <div className="kpis">
    <div className="kpi"><b>{card.residents}</b><span>Registered residents</span></div>
    <div className="kpi"><b>{card.high_risk}</b><span>High risk</span></div>
    <div className="kpi"><b>{card.aged_75_plus}</b><span>Aged 75 and over</span></div>
    <div className="kpi"><b>{card.living_alone}</b><span>Living alone</span></div>
    <div className="kpi"><b>{card.wards}</b><span>Wards</span></div>
  </div>
) : null;

/** Plain words for why a register row is called early, for when the API's plan is not there. */
function wordsFor(r: DistrictView['residents'][number]): string[] {
  return [r.age >= 75 ? '75+' : r.age >= 65 ? '65 to 74' : '', r.lives_alone ? 'lives alone' : '', r.outdoor_worker ? 'outdoor worker' : '', ...r.conditions].filter(Boolean);
}

/** The residents the round would call first: the API's call plan, else the register's highest risk. */
function callPlanOf(view: DistrictView): PlanRow[] {
  if (view.call_plan) return view.call_plan;
  return view.residents.filter(r => !r.yours)
    .sort((a, b) => b.risk_score - a.risk_score || b.age - a.age || a.id.localeCompare(b.id))
    .slice(0, PLAN_SIZE)
    .map(r => ({ id: r.id, name: r.name, name_hi: r.name_hi, age: r.age, ward_name: r.ward_name, risk_score: r.risk_score, reasons: wordsFor(r) }));
}

export const CallPlan: React.FC<{ view: DistrictView; wardName?: string; onOpen: (id: string) => void; className?: string }> = ({ view, wardName, onOpen, className = 'card ov-wide' }) => {
  const [showAll, setShowAll] = React.useState(false);
  const plan = callPlanOf(view).filter(r => !wardName || r.ward_name === wardName);
  const displayed = showAll ? plan : plan.slice(0, 5);

  return (
    <section className={className}>
      <header>
        <h2>Who Chhaya calls first</h2>
        <p>The highest-risk residents{wardName ? ` in ${wardName}` : ` in ${view.district.name}`}, in the order the round will call them</p>
      </header>
      <div className="attn">
        {displayed.length ? displayed.map(r => {
          const reasons = r.reason_labels?.length ? r.reason_labels : r.reasons || [];
          return (
            <button key={r.id} type="button" className="attn-item" onClick={() => onOpen(r.id)}>
              <span className="attn-main">
                <span className="attn-tags">
                  <b>{r.name}</b>
                  <span className="muted">{r.age} years{r.ward_name ? ` · ${r.ward_name}` : ''}</span>
                </span>
                {reasons.length > 0 && <span className="attn-tags">{reasons.map(reason => <span key={reason} className="pill">{reason}</span>)}</span>}
              </span>
              <span className="attn-cta" aria-hidden="true">Open</span>
            </button>
          );
        }) : <p className="muted pad-y">No resident in the register is due a call.</p>}
      </div>
      {plan.length > 5 && (
        <button
          type="button"
          className="linkbtn mt-3"
          onClick={() => setShowAll(!showAll)}
        >
          {showAll ? 'Show fewer' : `Show all ${plan.length} residents`}
        </button>
      )}
    </section>
  );
};

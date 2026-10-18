import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { LineChart } from './charts/LineChart';
import { getWeather } from '../lib/roleApi';
import { warningLabel } from '../lib/labels';
import type { WeatherView } from '../lib/types';

// The stored weather of one area (GET /api/weather): now, today, the hourly and 5-day strips, the day's curve
// against the state's mean and hottest peaks, and where it came from. Nothing here calls the Weather API.

const TZ = 'Asia/Kolkata';
const hhmm = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, hour: '2-digit', minute: '2-digit', hour12: false });
const dayTime = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
const weekday = new Intl.DateTimeFormat('en-GB', { timeZone: 'UTC', weekday: 'short', day: 'numeric' });

const deg = (v: number | null | undefined) => v == null ? '–' : `${Math.round(v * 10) / 10}°C`;
const pct = (v: number | null | undefined) => v == null ? '–' : `${Math.round(v)}%`;
const hourLabel = (h: number) => `${String(h).padStart(2, '0')}:00`;
const istDay = new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' });
/** "06:00" for a time today in India, "1 Oct, 06:00" for an earlier day. */
const sinceOf = (iso: string | null) => {
  const t = Date.parse(iso || '');
  if (!Number.isFinite(t)) return null;
  return istDay.format(new Date(t)) === istDay.format(new Date()) ? hhmm.format(new Date(t)) : dayTime.format(new Date(t));
};
const dayOnly = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, day: 'numeric', month: 'short' });
/** "15 Oct" when the weather shown was fetched on an earlier day in India than today (overnight, before the
 * morning fetch); null for today's, or when it was never fetched. */
export const earlierDayOf = (view: WeatherView): string | null => {
  const t = Date.parse(view.fetched_at || '');
  if (!Number.isFinite(t) || istDay.format(new Date(t)) === istDay.format(new Date())) return null;
  return dayOnly.format(new Date(t));
};
const timeOf = (iso: string | null, format = hhmm) => { const t = Date.parse(iso || ''); return Number.isFinite(t) ? format.format(new Date(t)) : null; };

/** Google's condition icon (the API gives the address without its extension), or nothing where it sent none. */
const Icon: React.FC<{ uri: string | null; alt?: string; size?: number }> = ({ uri, alt = '', size = 28 }) =>
  uri ? <img className="wx-icon" src={`${uri}.svg`} alt={alt} width={size} height={size} /> : null;

/** The weather of the area in view, read again when the area changes. */
export function useWeather(area: { state: string; district_id: string; ward_id: string }) {
  const [view, setView] = useState<WeatherView | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const { state, district_id, ward_id } = area;
  useEffect(() => {
    let active = true;
    setLoading(true);
    getWeather({ state, district_id, ward_id }).then(
      v => { if (active) { setView(v); setError(''); setLoading(false); } },
      e => { if (active) { setView(null); setError((e as Error).message); setLoading(false); } },
    );
    return () => { active = false; };
  }, [state, district_id, ward_id]);
  return { view, error, loading };
}

const one = (v: number) => `${v.toFixed(1)} °C`;

/** "Red heat warning · Churu — Hottest in Rajasthan today: …"; without an alert,
 * "No alert · Bikaner — 31.8 °C, below the Rajasthan average of 34.4 °C". */
export function alertLine(view: WeatherView): { severity: string; text: string } {
  const severity = (view.alert?.severity || '').toLowerCase();
  if (severity) {
    const head = `${warningLabel(severity, 'heat')} · ${view.area.name}`;
    return { severity, text: view.alert?.reason_text ? `${head} — ${view.alert.reason_text}` : head };
  }
  const head = `No alert · ${view.area.name}`;
  if (view.area.kind === 'state') return { severity, text: head };
  const peak = view.state?.districts.find(d => d.id === view.area.id)?.peak_c ?? view.today?.max_c ?? null;
  const mean = view.state?.mean_peak_c ?? null;
  if (peak == null) return { severity, text: head };
  if (mean == null) return { severity, text: `${head} — ${one(peak)}` };
  const side = peak < mean ? 'below' : peak > mean ? 'above' : 'at';
  return { severity, text: `${head} — ${one(peak)}, ${side} the ${view.state!.name} average of ${one(mean)}` };
}

/** The Overview & Weather alert line: the alert and its reason, and on Overview a link to Weather.
 * When viewing all districts at state level, it gently cycles through each district's forecast. */
export const WeatherLine: React.FC<{
  view: WeatherView | null;
  to?: string;
  onChooseDistrict?: (districtId: string) => void;
}> = ({ view, to, onChooseDistrict }) => {
  const [districtIdx, setDistrictIdx] = useState(0);
  const [isPaused, setIsPaused] = useState(false);

  const isStateView = view?.area.kind === 'state';
  const districts = isStateView && view?.state?.districts ? view.state.districts : [];

  useEffect(() => {
    if (!isStateView || districts.length <= 1 || isPaused) return;
    const timer = setInterval(() => {
      setDistrictIdx(idx => (idx + 1) % districts.length);
    }, 4500);
    return () => clearInterval(timer);
  }, [isStateView, districts.length, isPaused]);

  if (!view) return null;

  // When viewing "All districts", render the ambient smooth cross-fading ticker
  if (isStateView && districts.length > 0) {
    const current = districts[districtIdx];
    const rawSev = (current.alert === 'orange' ? 'amber' : (current.alert || '')).toLowerCase();
    const isWarn = rawSev === 'red' || rawSev === 'amber';
    const head = isWarn ? `${warningLabel(rawSev, 'heat')} · ${current.name}` : `No alert · ${current.name}`;

    let reason = '';
    const stateMean = view.state?.mean_peak_c;
    if (rawSev === 'red' && view.source === 'google_weather') {
      // Live Red is the state rule (the hottest district), not a fixed threshold: say it as the alert does.
      const at = current.peak_hour != null ? ` at ${hourLabel(current.peak_hour)}` : '';
      reason = current.peak_c != null && stateMean != null
        ? `Hottest in ${view.state?.name || 'the state'} today: ${one(current.peak_c)}${at}, ${(current.peak_c - stateMean).toFixed(1)} °C above the state average.`
        : `Hottest in ${view.state?.name || 'the state'} today.`;
    } else if (rawSev === 'red') {
      reason = current.peak_c != null
        ? `Forecast peak ${one(current.peak_c)}${current.peak_hour != null ? ` at ${hourLabel(current.peak_hour)}` : ''} is above the Red threshold (47 °C).`
        : 'Forecast peak is above the Red threshold.';
    } else if (rawSev === 'amber') {
      reason = current.peak_c != null && stateMean != null
        ? `${one(current.peak_c)}, ${current.peak_c > stateMean ? `${(current.peak_c - stateMean).toFixed(1)} °C above` : `${(stateMean - current.peak_c).toFixed(1)} °C below`} the ${view.state?.name || 'state'} average of ${one(stateMean)}.`
        : current.peak_c != null ? `${one(current.peak_c)}.` : '';
    } else {
      reason = current.peak_c != null && stateMean != null
        ? `${one(current.peak_c)}, below the ${view.state?.name || 'state'} average of ${one(stateMean)}.`
        : current.peak_c != null ? `${one(current.peak_c)}.` : '';
    }

    return (
      <div
        className={`wx-line ${rawSev === 'red' ? 'red' : rawSev === 'amber' ? 'amber' : ''}`}
        onMouseEnter={() => setIsPaused(true)}
        onMouseLeave={() => setIsPaused(false)}
        role="region"
        aria-label="District weather alerts"
      >
        <div key={current.id} className="wx-ticker-slide">
          <strong className="wx-ticker-text">
            <span>{head}</span>
            {onChooseDistrict && (
              <button
                type="button"
                className="wx-district-jump-icon"
                title={`Filter to ${current.name}`}
                aria-label={`Filter to ${current.name}`}
                onClick={() => onChooseDistrict(current.id)}
              >
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ marginLeft: '1px' }}>
                  <polyline points="9 18 15 12 9 6" />
                </svg>
              </button>
            )}
            {reason ? <span> — {reason}</span> : null}
          </strong>
        </div>

        {to && <Link to={to}>Weather details</Link>}
      </div>
    );
  }

  // When a specific district or ward is chosen: standard static line
  const { severity, text } = alertLine(view);
  const earlier = earlierDayOf(view);
  const figures = [view.now?.temp_c != null && `${deg(view.now.temp_c)} now`, view.today?.max_c != null && `high ${deg(view.today.max_c)}`].filter(Boolean).join(' · ');
  return (
    <div className={`wx-line ${severity === 'red' ? 'red' : severity ? 'amber' : ''}`}>
      <strong>{text}</strong>
      {figures && <span className="wx-line-figures">{figures}</span>}
      {earlier && <span className="wx-line-figures wx-earlier">Forecast from {earlier}</span>}
      {view.unavailable_since && <span className="wx-line-figures">Live forecast unavailable</span>}
      {to && <Link to={to}>Weather details</Link>}
    </div>
  );
};

export const WeatherCard: React.FC<{ view: WeatherView }> = ({ view }) => {
  const { area, now, today, state } = view;
  const demo = view.source === 'demo_scenario';
  const hours = view.hours.filter(h => h.hour >= 6 && h.hour <= 20);
  const days = view.days.slice(0, 5);
  const curve = view.hours.filter(h => h.temp_c != null);
  const earlier = earlierDayOf(view);
  // Overnight the last day's weather is still shown: its date is said wherever it is.
  const updated = sinceOf(view.fetched_at);

  const lines = [
    ...(state?.mean_peak_c != null ? [{ v: state.mean_peak_c, label: `State mean peak ${deg(state.mean_peak_c)}`, color: 'var(--ink-2)' }] : []),
    ...(state?.max_peak_c != null ? [{ v: state.max_peak_c, label: `Hottest in state ${deg(state.max_peak_c)}`, color: 'var(--red)' }] : []),
  ];
  const values = curve.map(h => h.temp_c as number).concat(lines.map(l => l.v));
  const ymin = values.length ? Math.floor((Math.min(...values) - 3) / 5) * 5 : 0;
  const ymax = values.length ? ymin + Math.ceil((Math.max(...values) + 2 - ymin) / 5) * 5 : 50;

  return (
    <section className="card wx-card" aria-label={`Weather in ${area.name}`}>
      <header>
        <h2>Weather in {area.name}</h2>
        <p>{area.kind === 'state' ? `${area.name}: the hottest district's day` : area.kind === 'ward' ? `Ward forecast, ${area.state}` : area.state}</p>
      </header>

      {view.unavailable_since && (
        <p className="note wx-unavailable" role="status">
          Live forecast unavailable since {sinceOf(view.unavailable_since)}; last update: {timeOf(view.fetched_at, dayTime) || 'none yet'}.
        </p>
      )}

      {earlier && !view.unavailable_since && (
        <p className="note wx-earlier" role="status">Forecast from {earlier}; today's is fetched at 06:00 IST.</p>
      )}

      {(now || today) && (
        <div className="wx-top">
          {now && (
            <div className="wx-now">
              <Icon uri={now.icon_uri} size={56} />
              <div>
                <b className="wx-now-temp">{deg(now.temp_c)}</b>
                <span>{now.condition_text || ''}</span>
                {now.feels_like_c != null && <span className="muted">Feels like {deg(now.feels_like_c)}</span>}
              </div>
            </div>
          )}
          {today && (
            <dl className="wx-today" aria-label="Today">
              <div><dt>High</dt><dd>{deg(today.max_c)}</dd></div>
              <div><dt>Low</dt><dd>{deg(today.min_c)}</dd></div>
              {today.humidity_pct != null && <div><dt>Humidity</dt><dd>{pct(today.humidity_pct)}</dd></div>}
              {today.rain_pct != null && <div><dt>Chance of rain</dt><dd>{pct(today.rain_pct)}</dd></div>}
              {state?.mean_peak_c != null && <div><dt>State mean</dt><dd>{deg(state.mean_peak_c)}</dd></div>}
              {state?.max_peak_c != null && <div><dt>State high</dt><dd>{deg(state.max_peak_c)}</dd></div>}
            </dl>
          )}
        </div>
      )}

      {hours.length > 0 && (
        <ul className="wx-strip" aria-label="Hourly forecast">
          {hours.map(h => (
            <li key={h.hour} className={now?.hour === h.hour ? 'now' : ''}>
              <span className="wx-strip-when">{hourLabel(h.hour)}</span>
              {h.icon_uri ? <Icon uri={h.icon_uri} alt={h.condition_text || ''} /> : <span className="wx-cond">{h.condition_text || ''}</span>}
              <b>{deg(h.temp_c)}</b>
              {h.rain_pct != null && <span className="muted">{pct(h.rain_pct)} rain</span>}
            </li>
          ))}
        </ul>
      )}

      {days.length > 0 && (
        <ul className="wx-strip wx-days" aria-label="Next 5 days">
          {days.map((d, i) => (
            <li key={d.date}>
              <span className="wx-strip-when">{i === 0 ? 'Today' : weekday.format(new Date(`${d.date}T00:00:00Z`))}</span>
              {d.icon_uri ? <Icon uri={d.icon_uri} alt={d.condition_text || ''} /> : <span className="wx-cond">{d.condition_text || ''}</span>}
              <b>{deg(d.max_c)}</b>
              <span className="muted">{deg(d.min_c)}</span>
            </li>
          ))}
        </ul>
      )}

      {curve.length > 1 && (
        <LineChart
          label={`Hourly temperature, ${area.name}`}
          xs={curve.map(h => h.hour)}
          xfmt={hourLabel}
          ymin={ymin}
          ymax={ymax}
          ystep={5}
          yfmt={deg}
          series={[{ name: area.name, color: 'var(--color-sun)', ys: curve.map(h => h.temp_c as number) }]}
          thresholds={lines}
        />
      )}

      <footer className="wx-source">
        {demo
          ? `Data pulled from Google Weather API (demo scenario)${updated ? ` · updated ${updated} IST` : ''}`
          : `Data pulled from Google Weather API${updated ? ` · updated ${updated} IST` : ''}`}
      </footer>
    </section>
  );
};

export default WeatherCard;

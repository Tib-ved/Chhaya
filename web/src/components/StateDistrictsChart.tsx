import React from 'react';
import { warningLabel } from '../lib/labels';
import type { WeatherView } from '../lib/types';

// At state level: each district's forecast peak as a bar, with the state mean as a dashed line across them
// and the districts under an alert marked. The state rule raises its alerts from exactly these figures.

const deg = (v: number | null | undefined) => v == null ? '–' : `${Math.round(v * 10) / 10}°C`;

export const StateDistrictsChart: React.FC<{ state: NonNullable<WeatherView['state']>; onOpen?: (districtId: string) => void }> = ({ state, onOpen }) => {
  const [showAll, setShowAll] = React.useState(false);
  const peaks = state.districts.map(d => d.peak_c).filter((v): v is number => v != null);
  if (!state.districts.length) return null;
  const all = state.mean_peak_c != null ? [...peaks, state.mean_peak_c] : peaks;
  const lo = all.length ? Math.floor(Math.min(...all) - 3) : 0;
  const hi = all.length ? Math.ceil(Math.max(...all) + 1) : 1;
  const at = (v: number) => `${Math.max(0, Math.min(100, ((v - lo) / (hi - lo || 1)) * 100))}%`;
  const rows = [...state.districts].sort((a, b) => (b.peak_c ?? -Infinity) - (a.peak_c ?? -Infinity));
  const displayedRows = showAll ? rows : rows.slice(0, 5);

  return (
    <section className="card">
      <header>
        <h2>Forecast peak by district</h2>
        <p>Today's highest temperature in each district of {state.name}{state.mean_peak_c != null && <> · <span className="sd-mean-key">{state.name} mean {deg(state.mean_peak_c)}</span></>}</p>
      </header>
      <ul className="sd-chart" aria-label="Forecast peak by district">
        {displayedRows.map(d => {
          const severity = (d.alert || '').toLowerCase();
          const tone = severity === 'red' ? 'red' : severity ? 'amber' : '';
          return (
            <li key={d.id} className={tone}>
              {onOpen ? <button type="button" className="linkbtn sd-name" onClick={() => onOpen(d.id)}>{d.name}</button> : <span className="sd-name">{d.name}</span>}
              <span className="sd-track" aria-hidden="true">
                {d.peak_c != null && <i style={{ width: at(d.peak_c) }} />}
                {state.mean_peak_c != null && <em style={{ left: at(state.mean_peak_c) }} />}
              </span>
              <span className="sd-value">
                {d.peak_c == null ? 'Forecast unavailable' : `${deg(d.peak_c)}${d.peak_hour != null ? ` at ${String(d.peak_hour).padStart(2, '0')}:00` : ''}`}
                {tone && <span className={`pill ${tone}`}>{warningLabel(severity, 'heat')}</span>}
              </span>
            </li>
          );
        })}
      </ul>
      {rows.length > 5 && (
        <button
          type="button"
          className="linkbtn mt-3"
          onClick={() => setShowAll(!showAll)}
        >
          {showAll ? 'Show fewer' : `Show all ${rows.length} districts`}
        </button>
      )}
    </section>
  );
};

export default StateDistrictsChart;

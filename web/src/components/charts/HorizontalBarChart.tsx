import React from 'react';

export interface HBarRow {
  label: string;
  n: number;
  color?: string;
  onClick?: () => void;
}

interface HorizontalBarChartProps {
  rows: HBarRow[];
  limit?: number;
  stacked?: boolean;
}

export const HorizontalBarChart: React.FC<HorizontalBarChartProps> = ({ rows, limit, stacked = false }) => {
  const max = Math.max(1, ...rows.map(r => r.n));
  const shown = limit ? rows.slice(0, limit) : rows;

  return (
    <div className="hbar-container">
      <div className={`hb ${stacked ? 'stacked' : ''}`}>
        {shown.map((r, i) => {
          const pct = Math.max(2, (r.n / max) * 100);
          return (
            <div key={i} className="hb-row">
              {r.onClick ? <button type="button" className="hb-label hb-label-button" title={r.label} onClick={r.onClick}>{r.label}</button> : <span className="hb-label" title={r.label}>{r.label}</span>}
              <button
                type="button"
                className="hb-track"
                onClick={r.onClick}
                title={`${r.label}: ${r.n}`}
                aria-label={`${r.label}, ${r.n}`}
              >
                <i
                  style={{
                    width: `${pct}%`,
                    backgroundColor: r.color || 'var(--color-data-visits)'
                  }}
                />
              </button>
              <span className="hb-v">{r.n}</span>
            </div>
          );
        })}
      </div>

    </div>
  );
};

export default HorizontalBarChart;

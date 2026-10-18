import React from 'react';

export interface StackedRow {
  label: string;
  t: {
    red: number;
    amber: number;
    green: number;
  };
  onClick?: (tier: 'red' | 'amber' | 'green') => void;
}

interface StackedBarChartProps {
  rows: StackedRow[];
}

export const StackedBarChart: React.FC<StackedBarChartProps> = ({ rows }) => {
  const max = Math.max(
    1,
    ...rows.map(r => r.t.red + r.t.amber + r.t.green)
  );

  return (
    <div className="stacked-bar-container">
      <div className="legend">
        <span className="st-key">Counts: Red · Amber · Green</span>
      </div>

      <div className="hb" style={{ marginTop: '10px' }}>
        {rows.map((r, i) => {
          const tot = r.t.red + r.t.amber + r.t.green;
          const trackPct = Math.max(4, (tot / max) * 100);

          return (
            <div key={i} className="st-row">
              <span className="st-label">{r.label}</span>
              <div className="st" style={{ width: `${trackPct}%` }}>
                {(['red', 'amber', 'green'] as const).map(k => {
                  const val = r.t[k];
                  if (!val) return null;
                  return (
                    <button
                      key={k}
                      type="button"
                      style={{
                        flex: `${val} 1 0`,
                        backgroundColor:
                          k === 'red'
                            ? 'var(--red)'
                            : k === 'amber'
                            ? 'var(--amber)'
                            : 'var(--green)'
                      }}
                      onClick={() => r.onClick && r.onClick(k)}
                      title={`${r.label} · ${k}: ${val}`}
                      aria-label={`${r.label}, ${k}, ${val}`}
                    />
                  );
                })}
              </div>
              <span className="st-n" title={`Red ${r.t.red} · Amber ${r.t.amber} · Green ${r.t.green}`}>
                <b className="st-red">{r.t.red}</b> · <b className="st-amber">{r.t.amber}</b> · <b className="st-green">{r.t.green}</b>
              </span>
            </div>
          );
        })}
      </div>

    </div>
  );
};

export default StackedBarChart;

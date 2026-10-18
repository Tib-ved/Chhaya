import React from 'react';

export interface DonutSegment {
  label: string;
  n: number;
  color: string;
  onClick?: () => void;
}

interface DonutChartProps {
  segments: DonutSegment[];
  centre: [string | number, string]; // [number, subtitle]
}

export const DonutChart: React.FC<DonutChartProps> = ({ segments, centre }) => {
  const tot = segments.reduce((s, g) => s + g.n, 0) || 1;
  const r = 52;
  const c = 2 * Math.PI * r;
  const live = segments.filter(g => g.n > 0);
  const gap = live.length > 1 ? 2 : 0;

  return (
    <div className="donut-wrapper">
      <div className="donut">
        <svg viewBox="0 0 150 150" role="group" aria-label={centre[1]}>
          {live.map((g, i) => {
            const len = (c * g.n) / tot;
            const d = Math.max(len - gap, 0.5);
            const currentOff = live.slice(0, i).reduce((sum, segment) => sum + c * segment.n / tot, 0);

            return (
              <circle
                key={i}
                className="seg"
                cx="75"
                cy="75"
                r={r}
                fill="none"
                stroke={g.color}
                strokeWidth="20"
                strokeDasharray={`${d.toFixed(2)} ${(c - d).toFixed(2)}`}
                strokeDashoffset={(-currentOff).toFixed(2)}
                transform="rotate(-90 75 75)"
                tabIndex={g.onClick ? 0 : undefined}
                role={g.onClick ? 'button' : undefined}
                onClick={g.onClick}
                onKeyDown={e => { if (g.onClick && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); g.onClick(); } }}
                style={{ cursor: g.onClick ? 'pointer' : 'default' }}
                aria-label={`${g.label}, ${g.n}`}
              >
                <title>{`${g.label}: ${g.n} (${Math.round((g.n / tot) * 100)}%)`}</title>
              </circle>
            );
          })}
          <text
            x="75"
            y="74"
            textAnchor="middle"
            fontSize="26"
            fontWeight="700"
            fill="var(--ink)"
            fontFamily="var(--sans)"
          >
            {centre[0]}
          </text>
          <text
            x="75"
            y="92"
            textAnchor="middle"
            fontSize="12"
            fill="var(--ink-3)"
            fontFamily="var(--sans)"
          >
            {centre[1]}
          </text>
        </svg>

        <div className="dl">
          {segments.map((g, i) => (
            <button
              key={i}
              type="button"
              onClick={g.onClick}
              title={`${g.label}: ${g.n}`}
            >
              <i className="dot" style={{ backgroundColor: g.color }} />
              <span>{g.label}</span>
              <b>{g.n}</b>
            </button>
          ))}
        </div>
      </div>

    </div>
  );
};

export default DonutChart;

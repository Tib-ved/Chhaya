import React from 'react';
import { useElementWidth } from '../../lib/useElementWidth';

export interface GroupedSeries {
  name: string;
  color: string;
}

interface GroupedBarChartProps {
  label: string;
  groups: { label: string; values: number[] }[];
  series: GroupedSeries[];
}

export const GroupedBarChart: React.FC<GroupedBarChartProps> = ({ label, groups, series }) => {
  const { ref: boxRef, width: boxWidth } = useElementWidth<HTMLDivElement>(440);
  const W = Math.max(280, boxWidth), H = 240, L = 34, R = 6, T = 10, B = 30;
  const empty = groups.every(g => g.values.every(v => !v));
  const max = Math.max(1, ...groups.flatMap(g => g.values));
  const top = Math.max(4, Math.ceil(max / 4) * 4);
  const plotW = W - L - R, plotH = H - T - B;
  const slot = plotW / Math.max(1, groups.length);
  const barW = Math.min(26, (slot * 0.78) / series.length);
  const y = (v: number) => T + plotH * (1 - v / top);
  const ticks = [0, 1, 2, 3, 4].map(k => (top * k) / 4);

  return (
    <div className="grouped-bar" ref={boxRef}>
      <div className="legend" style={{ marginBottom: 8 }}>
        {series.map(s => <span key={s.name}><i className="dot" style={{ backgroundColor: s.color }} />{s.name}</span>)}
      </div>
      {groups.length === 0 || empty ? <p className="muted">No calls or visits recorded yet.</p> : (
        <svg className="lc" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}>
          {ticks.map(t => (
            <g key={t}>
              <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="var(--line-soft)" />
              <text x={L - 8} y={y(t) + 4} textAnchor="end" fontSize="12" fill="var(--ink-3)" fontFamily="var(--sans)">{t}</text>
            </g>
          ))}
          {groups.map((g, gi) => {
            const start = L + gi * slot + (slot - barW * series.length) / 2;
            return (
              <g key={g.label}>
                {g.values.map((v, si) => (
                  <rect key={si} x={start + si * barW} y={y(v)} width={Math.max(barW - 2, 2)} height={Math.max(plotH - (y(v) - T), v ? 2 : 0)} rx="2" fill={series[si].color}>
                    <title>{`${g.label} · ${series[si].name}: ${v}`}</title>
                  </rect>
                ))}
                <text x={L + gi * slot + slot / 2} y={H - 10} textAnchor="middle" fontSize="12" fill="var(--ink-3)" fontFamily="var(--sans)">{g.label}</text>
              </g>
            );
          })}
        </svg>
      )}
    </div>
  );
};

export default GroupedBarChart;

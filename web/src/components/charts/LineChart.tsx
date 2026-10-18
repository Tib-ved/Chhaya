import React, { useState, useRef } from 'react';
import { useElementWidth } from '../../lib/useElementWidth';

export interface LineSeries {
  name: string;
  color: string;
  ys: number[];
}

export interface ThresholdLine {
  v: number;
  label: string;
  color?: string;
}

/** A vertical mark at one x, such as the hour an alert was raised. */
export interface MarkerLine {
  x: number;
  label: string;
}

interface LineChartProps {
  label: string;
  xs: number[];
  series: LineSeries[];
  ymin: number;
  ymax: number;
  xfmt: (x: number) => string;
  yfmt: (y: number) => string;
  thresholds?: ThresholdLine[];
  marker?: MarkerLine | null;
  /** Grid lines at every multiple of this (doubled until there are at most six steps); else four even steps. */
  ystep?: number;
}

// A series may leave an x without a value (NaN): the line breaks there and the table shows a dash.
const known = (v: number) => Number.isFinite(v);
const lastKnown = (ys: number[]) => { for (let i = ys.length - 1; i >= 0; i--) if (known(ys[i])) return i; return -1; };

// Helper functions for smooth spline curve generation
function smoothSplinePath(pts: { x: number; y: number }[]): string {
  if (pts.length <= 1) return pts.length === 1 ? `M ${pts[0].x.toFixed(1)} ${pts[0].y.toFixed(1)}` : '';
  if (pts.length === 2) return `M ${pts[0].x.toFixed(1)} ${pts[0].y.toFixed(1)} L ${pts[1].x.toFixed(1)} ${pts[1].y.toFixed(1)}`;

  let d = `M ${pts[0].x.toFixed(1)} ${pts[0].y.toFixed(1)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(i - 1, 0)];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[Math.min(i + 2, pts.length - 1)];

    const cp1x = p1.x + (p2.x - p0.x) / 6;
    const cp1y = p1.y + (p2.y - p0.y) / 6;
    const cp2x = p2.x - (p3.x - p1.x) / 6;
    const cp2y = p2.y - (p3.y - p1.y) / 6;

    d += ` C ${cp1x.toFixed(1)} ${cp1y.toFixed(1)}, ${cp2x.toFixed(1)} ${cp2y.toFixed(1)}, ${p2.x.toFixed(1)} ${p2.y.toFixed(1)}`;
  }
  return d;
}

function smoothAreaPath(pts: { x: number; y: number }[], baseY: number): string {
  if (pts.length < 2) return '';
  const lineD = smoothSplinePath(pts);
  const first = pts[0];
  const last = pts[pts.length - 1];
  return `${lineD} L ${last.x.toFixed(1)} ${baseY.toFixed(1)} L ${first.x.toFixed(1)} ${baseY.toFixed(1)} Z`;
}

export const LineChart: React.FC<LineChartProps> = ({
  label,
  xs,
  series,
  ymin,
  ymax,
  xfmt,
  yfmt,
  thresholds = [],
  marker = null,
  ystep
}) => {
  const [activeIdx, setActiveIdx] = useState<number | null>(null);
  const [tipPos, setTipPos] = useState<{ x: number; y: number } | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  // Drawn at its real width, so labels keep their pixel size on any card
  const { ref: boxRef, width: boxWidth } = useElementWidth<HTMLDivElement>(640);
  const W = Math.max(300, boxWidth);
  const H = W < 520 ? 220 : 260;
  const L = 48;
  const R = W < 520 ? 80 : 120;
  const T = 20;
  const B = 32;
  const n = xs.length;

  const xPos = (i: number) => L + ((W - L - R) * i) / (n - 1);
  const yPos = (v: number) => T + (H - T - B) * (1 - (v - ymin) / (ymax - ymin || 1));

  // Y-axis grid ticks: at round values when a step is given, else five even levels
  let every = ystep && ystep > 0 ? ystep : 0;
  while (every && (ymax - ymin) / every > 6) every *= 2;
  const tickValues = every
    ? Array.from({ length: Math.floor((ymax - Math.ceil(ymin / every) * every) / every + 1e-9) + 1 }, (_, k) => Math.ceil(ymin / every) * every + k * every)
    : [0, 1, 2, 3, 4].map(k => ymin + ((ymax - ymin) * k) / 4);
  const gridTicks = tickValues.map(val => ({ val, y: yPos(val) }));

  // X-axis label step
  const step = Math.ceil(n / 6);

  // Endpoint labels vertically spaced
  const ends = series
    .map(s => ({ s, i: lastKnown(s.ys) }))
    .filter(e => e.i >= 0)
    .map(e => ({ ...e, y: yPos(e.s.ys[e.i]) }))
    .sort((a, b) => a.y - b.y);
  for (let i = 1; i < ends.length; i++) {
    if (ends[i].y - ends[i - 1].y < 16) {
      ends[i].y = ends[i - 1].y + 16;
    }
  }

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const boundedPx = Math.max(L, Math.min(W - R, px));
    const idx = Math.max(
      0,
      Math.min(n - 1, Math.round(((boundedPx - L) / (W - L - R)) * (n - 1)))
    );
    setActiveIdx(idx);
    setTipPos({ x: e.clientX, y: e.clientY });
  };

  const handleMouseLeave = () => {
    setActiveIdx(null);
    setTipPos(null);
  };

  return (
    <div className="line-chart-wrapper" ref={boxRef} style={{ position: 'relative' }}>
      {series.length > 1 && (
        <div className="legend" style={{ marginBottom: '8px' }}>
          {series.map((s, i) => (
            <span key={i}>
              <i className="dot" style={{ backgroundColor: s.color }} />
              {s.name}
            </span>
          ))}
        </div>
      )}

      <svg
        ref={svgRef}
        className="lc"
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={label}
        onMouseMove={handleMouseMove}
        onMouseLeave={handleMouseLeave}
      >
        <defs>
          {series.map((s, i) => (
            <linearGradient key={i} id={`chart-grad-${i}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={s.color} stopOpacity="0.22" />
              <stop offset="70%" stopColor={s.color} stopOpacity="0.04" />
              <stop offset="100%" stopColor={s.color} stopOpacity="0.0" />
            </linearGradient>
          ))}
        </defs>

        {/* Horizontal grid lines */}
        {gridTicks.map((g, i) => (
          <g key={i}>
            <line
              className="grid"
              x1={L}
              x2={W - R}
              y1={g.y}
              y2={g.y}
              stroke="var(--line-soft)"
              strokeWidth="1"
            />
            <text
              className="ax"
              x={L - 8}
              y={g.y + 4}
              textAnchor="end"
              fill="var(--ink-3)"
              fontSize="12"
              fontFamily="var(--sans)"
            >
              {yfmt(g.val)}
            </text>
          </g>
        ))}

        {/* X-axis labels */}
        {xs.map((xv, i) => {
          if (i % step !== 0 && i !== n - 1) return null;
          return (
            <text
              key={i}
              className="ax"
              x={xPos(i)}
              y={H - 8}
              textAnchor="middle"
              fill="var(--ink-3)"
              fontSize="12"
              fontFamily="var(--sans)"
            >
              {xfmt(xv)}
            </text>
          );
        })}

        {/* Threshold lines with smart non-colliding labels */}
        {(() => {
          const sorted = thresholds
            .map((t, idx) => ({ ...t, y: yPos(t.v), idx }))
            .sort((a, b) => a.y - b.y);

          return sorted.map((t, i) => {
            const closeToPrev = i > 0 && Math.abs(t.y - sorted[i - 1].y) < 22;
            const closeToNext = i < sorted.length - 1 && Math.abs(sorted[i + 1].y - t.y) < 22;
            const isClustered = closeToPrev || closeToNext;

            // Stagger sides when labels are close: top on right, bottom on left
            const isRight = isClustered ? i % 2 === 0 : true;
            const labelX = isRight ? W - R - 8 : L + 8;
            const textAnchor = isRight ? 'end' : 'start';

            return (
              <g key={t.idx}>
                <line
                  className="thr"
                  x1={L}
                  x2={W - R}
                  y1={t.y}
                  y2={t.y}
                  stroke={t.color || 'var(--ink-3)'}
                  strokeWidth="1.2"
                  strokeDasharray="4 4"
                />
                <text
                  className="ax"
                  x={labelX}
                  y={t.y - 5}
                  textAnchor={textAnchor}
                  fill={t.color || 'var(--red)'}
                  fontWeight="600"
                  fontSize="11"
                  fontFamily="var(--sans)"
                  style={{
                    paintOrder: 'stroke fill',
                    stroke: 'var(--surface)',
                    strokeWidth: 4,
                    strokeLinejoin: 'round'
                  }}
                >
                  {t.label}
                </text>
              </g>
            );
          });
        })()}

        {/* Marker at one x, between the two nearest points */}
        {marker && n > 1 && marker.x >= xs[0] && marker.x <= xs[n - 1] && (() => {
          const after = xs.findIndex(x => x >= marker.x);
          const at = after === 0 ? 0 : after - 1 + (marker.x - xs[after - 1]) / (xs[after] - xs[after - 1] || 1);
          const x = xPos(at);
          return (
            <g>
              <line x1={x} x2={x} y1={T} y2={H - B} stroke="var(--ink-2)" strokeWidth="1.5" />
              <text className="ax" x={x + 5} y={T + 12} fill="var(--ink-2)" fontWeight="600" fontSize="12" fontFamily="var(--sans)">
                {marker.label}
              </text>
            </g>
          );
        })()}

        {/* Series: smooth curves and area fills */}
        {series.map((s, i) => {
          const runs: { x: number; y: number; val: number }[][] = [[]];
          s.ys.forEach((v, idx) => {
            if (known(v)) {
              runs[runs.length - 1].push({ x: xPos(idx), y: yPos(v), val: v });
            } else if (runs[runs.length - 1].length) {
              runs.push([]);
            }
          });

          const validRuns = runs.filter(run => run.length > 0);
          const lastIdx = lastKnown(s.ys);
          if (lastIdx < 0) return null;

          // Find peak point
          let peakPt: { x: number; y: number; val: number } | null = null;
          validRuns.forEach(run => {
            run.forEach(pt => {
              if (!peakPt || pt.val > peakPt.val) peakPt = pt;
            });
          });

          return (
            <g key={i}>
              {validRuns.map((run, r) => (
                <g key={r}>
                  <path
                    d={smoothAreaPath(run, H - B)}
                    fill={`url(#chart-grad-${i})`}
                  />
                  <path
                    d={smoothSplinePath(run)}
                    fill="none"
                    stroke={s.color}
                    strokeWidth="2.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </g>
              ))}

              {/* Peak indicator dot */}
              {peakPt && (
                <circle
                  cx={(peakPt as { x: number; y: number }).x}
                  cy={(peakPt as { x: number; y: number }).y}
                  r="4.5"
                  fill={s.color}
                  stroke="var(--surface)"
                  strokeWidth="2"
                />
              )}

              {/* Last known point circle */}
              <circle
                cx={xPos(lastIdx)}
                cy={yPos(s.ys[lastIdx])}
                r="4"
                fill={s.color}
                stroke="var(--surface)"
                strokeWidth="2"
              />
            </g>
          );
        })}

        {/* End value labels */}
        {ends.map((e, i) => {
          const text = series.length > 1 ? `${e.s.name} ${yfmt(e.s.ys[e.i])}` : yfmt(e.s.ys[e.i]);
          return (
            <text
              key={i}
              className="endl"
              x={W - R + 8}
              y={e.y + 4}
              fill="var(--ink-2)"
              fontSize="12"
              fontWeight="600"
              fontFamily="var(--sans)"
            >
              {text}
            </text>
          );
        })}

        {/* Active Hover Crosshair Line */}
        {activeIdx !== null && (
          <g>
            <line
              x1={xPos(activeIdx)}
              x2={xPos(activeIdx)}
              y1={T}
              y2={H - B}
              stroke="var(--ink-2)"
              strokeWidth="1"
              strokeDasharray="2 2"
            />
            {series.filter(s => known(s.ys[activeIdx])).map((s, i) => (
              <circle
                key={i}
                cx={xPos(activeIdx)}
                cy={yPos(s.ys[activeIdx])}
                r="4"
                fill={s.color}
                stroke="#fff"
                strokeWidth="1.5"
              />
            ))}
          </g>
        )}
      </svg>

      {/* Floating Tooltip */}
      {activeIdx !== null && tipPos && (
        <div
          style={{
            position: 'fixed',
            left: Math.min(window.innerWidth - 180, tipPos.x + 12),
            top: Math.max(10, tipPos.y - 70),
            backgroundColor: 'var(--ink)',
            color: '#fff',
            fontSize: '14px',
            lineHeight: '1.4',
            padding: '6px 12px',
            borderRadius: '6px',
            pointerEvents: 'none',
            zIndex: 100,
            boxShadow: '0 4px 12px rgba(0,0,0,0.2)'
          }}
        >
          <b>{xfmt(xs[activeIdx])}</b>
          <div style={{ marginTop: '2px' }}>
            {series.map((s, i) => (
              <div key={i} style={{ display: 'flex', gap: '8px', justifyContent: 'space-between' }}>
                <span>{s.name}:</span>
                <b>{known(s.ys[activeIdx]) ? yfmt(s.ys[activeIdx]) : '–'}</b>
              </div>
            ))}
          </div>
        </div>
      )}

    </div>
  );
};

export default LineChart;

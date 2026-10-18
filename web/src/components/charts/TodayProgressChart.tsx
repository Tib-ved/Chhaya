import React, { useState, useRef } from 'react';

interface DataPoint {
  time: string;
  reached: number;
  visited: number;
}

interface TodayProgressChartProps {
  hourly: { label: string; values: number[] }[];
  progress?: { minutes: number[]; reached: number[]; opened: number[]; visited: number[] } | null;
  st: {
    called: number;
    reached: number;
    visited: number;
  };
}

export const TodayProgressChart: React.FC<TodayProgressChartProps> = ({ hourly, st }) => {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  // Compute cumulative series from hourly counts
  const rawPoints: DataPoint[] = [];
  let rCum = 0;
  let vCum = 0;
  for (const h of hourly) {
    rCum += h.values[0] || 0;
    vCum += h.values[2] || 0;
    rawPoints.push({
      time: h.label,
      reached: rCum,
      visited: vCum
    });
  }

  // Ensure current totals match latest API figures if there's drift
  if (rawPoints.length > 0 && (st.reached > rCum || st.visited > vCum)) {
    rawPoints[rawPoints.length - 1].reached = Math.max(rawPoints[rawPoints.length - 1].reached, st.reached);
    rawPoints[rawPoints.length - 1].visited = Math.max(rawPoints[rawPoints.length - 1].visited, st.visited);
  }

  // Fallback if no hourly data points yet
  const points: DataPoint[] = rawPoints.length > 0
    ? rawPoints
    : st.called > 0
    ? [
        { time: 'Start', reached: 0, visited: 0 },
        { time: 'Now', reached: st.reached, visited: st.visited }
      ]
    : [];

  if (points.length === 0) {
    return (
      <div className="ops-chart-empty">
        <p className="text-small text-muted">Round open · Calls will begin shortly</p>
      </div>
    );
  }

  const W = 540;
  const H = 170;
  const padL = 36;
  const padR = 24;
  const padT = 20;
  const padB = 28;

  const chartW = W - padL - padR;
  const chartH = H - padT - padB;

  const maxY = Math.max(st.called, ...points.map(p => p.reached), 10);
  const n = points.length;

  const getX = (idx: number) => n === 1 ? padL + chartW / 2 : padL + (idx / (n - 1)) * chartW;
  const getY = (val: number) => padT + chartH * (1 - Math.min(val, maxY) / maxY);

  // SVG Path generator (smooth cubic bezier)
  const buildSmoothPath = (pts: { x: number; y: number }[]) => {
    if (pts.length === 0) return '';
    if (pts.length === 1) return `M ${pts[0].x} ${pts[0].y}`;
    let d = `M ${pts[0].x} ${pts[0].y}`;
    for (let i = 0; i < pts.length - 1; i++) {
      const p0 = pts[i === 0 ? 0 : i - 1];
      const p1 = pts[i];
      const p2 = pts[i + 1];
      const p3 = pts[i + 2] || p2;

      const cp1x = p1.x + (p2.x - p0.x) / 6;
      const cp1y = p1.y + (p2.y - p0.y) / 6;
      const cp2x = p2.x - (p3.x - p1.x) / 6;
      const cp2y = p2.y - (p3.y - p1.y) / 6;

      d += ` C ${cp1x} ${cp1y}, ${cp2x} ${cp2y}, ${p2.x} ${p2.y}`;
    }
    return d;
  };

  const reachedPoints = points.map((p, i) => ({ x: getX(i), y: getY(p.reached) }));
  const visitedPoints = points.map((p, i) => ({ x: getX(i), y: getY(p.visited) }));

  const reachedLine = buildSmoothPath(reachedPoints);
  const visitedLine = buildSmoothPath(visitedPoints);

  const reachedArea = `${reachedLine} L ${reachedPoints[reachedPoints.length - 1].x} ${padT + chartH} L ${reachedPoints[0].x} ${padT + chartH} Z`;

  // Grid tick levels
  const yTicks = [0, Math.round(maxY / 2), maxY];

  const handleMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    const mouseX = ((e.clientX - rect.left) / rect.width) * W;
    if (mouseX < padL || mouseX > W - padR) {
      setHoverIndex(null);
      return;
    }
    const ratio = (mouseX - padL) / chartW;
    const closestIdx = Math.max(0, Math.min(n - 1, Math.round(ratio * (n - 1))));
    setHoverIndex(closestIdx);
  };

  const activePoint = hoverIndex !== null ? points[hoverIndex] : null;
  const activeX = hoverIndex !== null ? getX(hoverIndex) : 0;

  return (
    <div className="ops-today-chart-container">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className="ops-today-svg"
        onMouseMove={handleMouseMove}
        onMouseLeave={() => setHoverIndex(null)}
        role="img"
        aria-label="Calls and visits progression line chart"
      >
        <defs>
          <linearGradient id="reachedAreaGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--color-data-reached)" stopOpacity="0.16" />
            <stop offset="100%" stopColor="var(--color-data-reached)" stopOpacity="0.01" />
          </linearGradient>
        </defs>

        {/* Horizontal grid lines */}
        {yTicks.map(val => (
          <g key={val} className="ops-chart-gridline">
            <line
              x1={padL}
              y1={getY(val)}
              x2={W - padR}
              y2={getY(val)}
              stroke="var(--line-soft)"
              strokeDasharray="3 3"
              strokeWidth="1"
            />
            <text
              x={padL - 8}
              y={getY(val) + 4}
              textAnchor="end"
              className="ops-chart-tick-label"
            >
              {val}
            </text>
          </g>
        ))}

        {/* Reached Area fill */}
        <path d={reachedArea} fill="url(#reachedAreaGrad)" />

        {/* Lines */}
        <path
          d={reachedLine}
          fill="none"
          stroke="var(--color-data-reached)"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <path
          d={visitedLine}
          fill="none"
          stroke="var(--color-data-visits)"
          strokeWidth="2"
          strokeDasharray="4 3"
          strokeLinecap="round"
          strokeLinejoin="round"
        />

        {/* X-axis time labels */}
        {points.map((p, i) => {
          // Show label if first, last, or spaced evenly
          const step = Math.max(1, Math.floor(n / 5));
          if (i !== 0 && i !== n - 1 && i % step !== 0) return null;
          return (
            <text
              key={p.time}
              x={getX(i)}
              y={H - 8}
              textAnchor={i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}
              className="ops-chart-tick-label"
            >
              {p.time}
            </text>
          );
        })}

        {/* Interactive Hover Guide & Tooltip */}
        {hoverIndex !== null && activePoint && (
          <g className="ops-chart-hover-group">
            <line
              x1={activeX}
              y1={padT}
              x2={activeX}
              y2={padT + chartH}
              stroke="var(--line-strong)"
              strokeWidth="1"
            />
            <circle
              cx={activeX}
              cy={getY(activePoint.reached)}
              r="4.5"
              fill="var(--surface)"
              stroke="var(--color-data-reached)"
              strokeWidth="2.5"
            />
            <circle
              cx={activeX}
              cy={getY(activePoint.visited)}
              r="4"
              fill="var(--surface)"
              stroke="var(--color-data-visits)"
              strokeWidth="2"
            />
          </g>
        )}
      </svg>

      {/* Floating HTML tooltip for high-precision text contrast */}
      {hoverIndex !== null && activePoint && (
        <div
          className="ops-chart-tooltip"
          style={{
            left: `${(activeX / W) * 100}%`,
            top: `${(getY(activePoint.reached) / H) * 100}%`
          }}
        >
          <div className="tooltip-time">{activePoint.time}</div>
          <div className="tooltip-row reached">
            <span>Reached</span>
            <strong>{activePoint.reached}</strong>
          </div>
          <div className="tooltip-row visits">
            <span>Visits</span>
            <strong>{activePoint.visited}</strong>
          </div>
        </div>
      )}
    </div>
  );
};

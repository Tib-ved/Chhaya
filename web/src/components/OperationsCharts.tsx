import { useId } from 'react'

export type Segment = { key: string; label: string; value: number; color: string }

export function Donut({ title, subtitle, segments, onSelect, centerLabel }: { title: string; subtitle: string; segments: Segment[]; onSelect: (key: string) => void; centerLabel: string }) {
  const id = useId()
  const total = segments.reduce((sum, segment) => sum + segment.value, 0)
  const slices = segments.filter(s => s.value > 0)
  return <section className="ops-card ops-chart" aria-labelledby={id}>
    <header><h2 id={id}>{title}</h2><p>{subtitle}</p></header>
    <div className="ops-donut-layout">
      <div className="ops-donut">
        <svg viewBox="0 0 120 120" role="img" aria-label={`${title}: ${segments.map(s => `${s.label} ${s.value}`).join(', ')}`}>
          <circle cx="60" cy="60" r="46" fill="none" stroke="var(--color-line-subtle)" strokeWidth="13" />
          {slices.map((s, index) => {
            const start = slices.slice(0, index).reduce((sum, item) => sum + item.value, 0) / total * 100
            const length = s.value / total * 100
            return <circle key={s.key} cx="60" cy="60" r="46" fill="none" stroke={s.color} strokeWidth="13" pathLength="100" strokeDasharray={`${length} ${100 - length}`} strokeDashoffset={-start} transform="rotate(-90 60 60)" className="ops-slice" onClick={() => onSelect(s.key)}><title>{s.label}: {s.value}</title></circle>
          })}
        </svg>
        <div className="ops-donut-center"><strong>{total}</strong><small>{centerLabel}</small></div>
      </div>
      <div className="ops-legend">{segments.map(s => <button type="button" key={s.key} onClick={() => onSelect(s.key)} aria-label={`Filter ${s.label}, ${s.value}`}><i style={{ background: s.color }} /><span>{s.label}</span><strong>{s.value}</strong><small>{total ? Math.round(s.value / total * 100) : 0}%</small></button>)}</div>
    </div>
    {!total && <p className="ops-muted">No observations in this scope yet.</p>}
  </section>
}

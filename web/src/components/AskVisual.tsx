// Draws a chart or table Ask Chhaya sent, with the app's own chart components.
import type { ChartSpec, TableSpec, Visual } from '../lib/askApi'
import { DonutChart } from './charts/DonutChart'
import { GroupedBarChart } from './charts/GroupedBarChart'
import { HorizontalBarChart } from './charts/HorizontalBarChart'
import { LineChart } from './charts/LineChart'
import { StackedBarChart } from './charts/StackedBarChart'

const TIERS: Record<string, string> = { red: 'var(--color-red)', amber: 'var(--color-amber)', green: 'var(--color-green)' }
const PALETTE = ['#0F6E6E', '#F08A24', '#5B7DB1', '#B04A5A', '#7A9A3A', '#8C6BB1']
// A series or slice named for a tier ("red", "Red cases") takes that tier's colour.
const tierOf = (name: string) => Object.keys(TIERS).find(t => new RegExp(`\\b${t}\\b`, 'i').test(name))
const colour = (name: string, i: number) => { const tier = tierOf(name); return tier ? TIERS[tier] : PALETTE[i % PALETTE.length] }

function labels(spec: ChartSpec): string[] {
  const seen: string[] = []
  for (const s of spec.series) for (const p of s.points) if (!seen.includes(p.label)) seen.push(p.label)
  return seen
}

const valueAt = (spec: ChartSpec, series: number, label: string) => spec.series[series].points.find(p => p.label === label)?.value ?? 0

function Chart({ spec }: { spec: ChartSpec }) {
  const xs = labels(spec)
  const tiered = spec.series.length > 1 && spec.series.every(s => tierOf(s.name))
  if (spec.type === 'donut') {
    const points = spec.series[0].points
    const total = points.reduce((sum, p) => sum + p.value, 0)
    return <DonutChart centre={[Math.round(total * 10) / 10, 'total']}
      segments={points.map((p, i) => ({ label: p.label, n: p.value, color: colour(p.label, i) }))} />
  }
  if (spec.type === 'line') {
    const values = spec.series.flatMap(s => s.points.map(p => p.value))
    const top = Math.max(1, ...values)
    return <LineChart label={spec.title} xs={xs.map((_, i) => i)} xfmt={x => xs[x] ?? ''} yfmt={y => String(Math.round(y * 10) / 10)}
      ymin={Math.min(0, ...values)} ymax={top} series={spec.series.map((s, i) => ({ name: s.name, color: colour(s.name, i), ys: xs.map(x => valueAt(spec, i, x)) }))} />
  }
  if (tiered && spec.type === 'stacked_bar') {
    const index = (tier: string) => spec.series.findIndex(s => tierOf(s.name) === tier)
    const pick = (tier: string, x: string) => index(tier) < 0 ? 0 : valueAt(spec, index(tier), x)
    return <StackedBarChart
      rows={xs.map(x => ({ label: x, t: { red: pick('red', x), amber: pick('amber', x), green: pick('green', x) } }))} />
  }
  if (spec.series.length === 1) {
    const single = spec.series[0]
    return <HorizontalBarChart rows={single.points.map((p, i) => ({ label: p.label, n: p.value, color: tierOf(single.name) ? colour(single.name, 0) : tierOf(p.label) ? colour(p.label, i) : undefined }))} />
  }
  return <GroupedBarChart label={spec.title}
    series={spec.series.map((s, i) => ({ name: s.name, color: colour(s.name, i) }))}
    groups={xs.map(x => ({ label: x, values: spec.series.map((_, i) => valueAt(spec, i, x)) }))} />
}

function Table({ spec }: { spec: TableSpec }) {
  return <div className="scroll-table ask-table"><table>
    <thead><tr>{spec.columns.map(c => <th key={c} scope="col">{c}</th>)}</tr></thead>
    <tbody>{spec.rows.map((row, r) => <tr key={r}>{row.map((cell, c) => <td key={c} className={typeof cell === 'number' ? 'num' : undefined}>{cell ?? '–'}</td>)}</tr>)}</tbody>
  </table></div>
}

export function AskVisual({ visual }: { visual: Visual }) {
  return <figure className="ask-visual" aria-label={visual.spec.title}>
    <figcaption>{visual.spec.title}</figcaption>
    {visual.kind === 'chart' ? <Chart spec={visual.spec} /> : <Table spec={visual.spec} />}
  </figure>
}

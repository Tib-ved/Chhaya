import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { AppHeader } from '../components/AppHeader'
import { request } from '../lib/api'
import type { RoundOverview } from '../lib/types'
import { useLoad } from '../lib/useLoad'
import { RiskScoreLegend, TierLegend } from '../components/Legends'
import { capitalise, tierLabel, warningLabel } from '../lib/labels'

export const AlertPage: React.FC = () => {
  const [showAll, setShowAll] = useState(false)

  const { data, isStale } = useLoad<RoundOverview>(() => request<RoundOverview>('/api/rounds/current'))

  const round = data?.round
  const alert = data?.alert
  const counts = data?.counts
  const calls = data?.calls

  const rows = calls?.length
    ? calls.map((c, i) => {
        let result = 'Still trying'
        if (c.tier) {
          result = tierLabel(c.tier)
        } else if (c.status === 'busy' || c.status === 'no_answer') {
          result = `No answer ×${c.attempt}`
        } else if (c.status === 'in_progress') {
          result = 'In progress'
        } else if (c.status === 'queued') {
          result = 'Queued'
        }
        return {
          n: i + 1,
          name: c.resident_name,
          age: c.age,
          ward: c.ward_name || c.ward_id,
          why: c.risk_factors.join(', '),
          score: c.risk_score,
          result,
        }
      })
    : []

  const displayedRows = showAll ? rows : rows.slice(0, 10)
  const peak = round?.peak_temp_c != null
    ? `${round.peak_temp_c} °C${round.peak_hour_local != null ? ` at ${String(round.peak_hour_local).padStart(2, '0')}:00` : ''}`
    : ''

  // Tiers in the tier pill; call states (no answer, in progress) in the neutral pill, since they are not tiers
  const renderBadge = (res: string) => {
    const tier = ['red', 'amber', 'green'].includes(res.toLowerCase()) ? res.toLowerCase() : ''
    return <span className={`pill ${tier || 'neutral'}`}>{res}</span>
  }

  return (
    <div className="page">
      <AppHeader />

      <main className="page-main">
        <div className="page-intro-row">
          <h1>A warning opened a round of calls</h1>
          {isStale && <span className="cell-muted">Reconnecting to demo service…</span>}
        </div>

        <section aria-label="Alert" className="warning-banner">
          <div className="warning-place">
            <span>{warningLabel(round?.severity || 'red', round?.hazard || 'heat')}</span>
            <b>{round?.district ? `${capitalise(round.district)}, Rajasthan` : 'Churu, Rajasthan'}</b>
          </div>
          {peak && <div className="warning-fact"><span>Forecast peak</span><b>{peak}</b></div>}
          <div className="warning-fact"><span>Round opened</span><b>{round?.opened_at ? new Date(round.opened_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '08:02'}</b></div>
          <div className="warning-fact"><span>Source</span><b>{capitalise(alert?.source) || 'Forecast threshold'}</b></div>
        </section>

        <section aria-label="Round progress" className="figure-grid">
          <div className="figure-card"><span className="figure-label">Targeted</span><span className="figure-value">{counts?.targeted ?? 30}</span></div>
          <div className="figure-card"><span className="figure-label">Reached</span><span className="figure-value">{counts?.reached ?? 24}</span></div>
          <div className="figure-card"><span className="figure-label">Unreachable</span><span className="figure-value">{counts?.unreachable ?? 4}</span></div>
          <div className="figure-card"><span className="figure-label">Still trying</span><span className="figure-value">{counts ? (counts.queued + counts.in_call) : 2}</span></div>
          <div className="figure-card red"><span className="figure-label">Red</span><span className="figure-value">{counts?.red ?? 3}</span></div>
          <div className="figure-card amber"><span className="figure-label">Amber</span><span className="figure-value">{counts?.amber ?? 15}</span></div>
          <div className="figure-card green"><span className="figure-label">Green</span><span className="figure-value">{counts?.green ?? 10}</span></div>
        </section>

        <section aria-label="Call order" className="table-card">
          <header>
            <h2>Called in order of risk</h2>
            <p>Age, living alone, outdoor work and health conditions add points. Highest score is called first.</p>
          </header>
          <div className="table-legend">
            <RiskScoreLegend />
            <TierLegend />
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col" className="num">#</th>
                  <th scope="col">Name</th>
                  <th scope="col" className="num">Age</th>
                  <th scope="col">Ward</th>
                  <th scope="col">Why first</th>
                  <th scope="col" className="num">Risk score</th>
                  <th scope="col">Result</th>
                </tr>
              </thead>
              <tbody>
                {displayedRows.length === 0 && (
                  <tr><td colSpan={7} className="cell-muted">No calls in this round yet.</td></tr>
                )}
                {displayedRows.map((row) => (
                  <tr key={row.n}>
                    <td className="num cell-mono">{row.n}</td>
                    <td className="cell-strong">{row.name}</td>
                    <td className="num">{row.age}</td>
                    <td>{row.ward}</td>
                    <td className="cell-muted">{row.why}</td>
                    <td className="num">{row.score}</td>
                    <td>{renderBadge(row.result)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {rows.length > 0 && (
            <div className="table-foot">
              <button type="button" className="linkbtn" onClick={() => setShowAll(!showAll)}>
                {showAll ? 'Show top 10 residents' : `Show all ${rows.length} residents`}
              </button>
            </div>
          )}
        </section>

        <div className="page-footer">
          <p>Someone who does not answer after three attempts is marked Amber. Nobody unreached is ever treated as fine.</p>
          <Link to="/call" className="btn">Take one of these calls yourself</Link>
        </div>
      </main>
    </div>
  )
}

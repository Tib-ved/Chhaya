import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { ChhayaBrand } from '../components/ChhayaBrand'
import { request } from '../lib/api'
import { getTeamSecret, setTeamSecret, clearTeamSecret } from '../lib/visitor'

const DEFAULT_CALLS = [
  { name: 'Bhanwari Devi, 81', attempt: 1, status: 'Red', queued: false },
  { name: 'Hanuman Ram, 79', attempt: 1, status: 'Amber', queued: false },
  { name: 'Mohan Lal, 77', attempt: 1, status: 'Green', queued: false },
  { name: 'Sharda Devi, 76', attempt: 1, status: 'In call 01:20', queued: false },
  { name: 'Gopal Ram, 71', attempt: 1, status: 'Queued', queued: true },
  { name: 'Rukma Devi, 74', attempt: 1, status: 'Queued', queued: true },
  { name: 'Santosh Kanwar, 68', attempt: 1, status: 'Queued', queued: true },
  { name: 'Prem Singh, 66', attempt: 1, status: 'Queued', queued: true },
]

export const TeamPage: React.FC = () => {
  const [unlocked, setUnlocked] = useState<boolean>(Boolean(getTeamSecret()))
  const [secretInput, setSecretInput] = useState<string>('')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [rebuilding, setRebuilding] = useState<boolean>(false)

  const handleUnlock = (e: React.FormEvent) => {
    e.preventDefault()
    if (!secretInput.trim()) return
    setTeamSecret(secretInput.trim())
    setUnlocked(true)
    setErrorMsg(null)
  }

  const handleLock = () => {
    clearTeamSecret()
    setUnlocked(false)
  }

  const handleRebuild = async () => {
    if (!window.confirm('Open today’s rounds in the districts under the alert? This deletes nothing.')) return
    setRebuilding(true)
    try {
      await request('/admin/demo/rebuild', { method: 'POST' })
      alert('Today’s rounds are open.')
    } catch (err: any) {
      alert(`Opening today’s rounds: ${err.message || 'failed'}`)
    } finally {
      setRebuilding(false)
    }
  }

  if (!unlocked) {
    return (
      <div className="page team-lock">
        <form onSubmit={handleUnlock} className="card team-lock-form">
          <header><Link to="/" aria-label="Chhaya operations home"><ChhayaBrand /></Link></header>
          <div className="action-row">
            <span className="pill">Team mode</span>
            <h1 className="section-title">Enter the internal secret</h1>
          </div>
          <p className="cell-muted">Team mode allows manual dispatch, alert replays, and opening today’s rounds.</p>
          <label htmlFor="team-secret" className="field-label">
            Internal secret
            <input id="team-secret" className="field" type="password" placeholder="Internal secret" value={secretInput} onChange={(e) => setSecretInput(e.target.value)} />
          </label>
          {errorMsg && <p className="call-problem">{errorMsg}</p>}
          <button type="submit" className="btn">Unlock team mode</button>
          <Link to="/" className="linkbtn team-return">Return to public app</Link>
        </form>
      </div>
    )
  }

  // Tiers in the tier pill; call states (in call, queued) in the neutral pill
  const statusPill = (status: string) => {
    const tier = ['red', 'amber', 'green'].includes(status.toLowerCase()) ? status.toLowerCase() : status.startsWith('In call') ? 'tag' : 'neutral'
    return <span className={`pill ${tier}`}>{status}</span>
  }

  return (
    <div className="page">
      <header className="team-bar">
        <div className="team-bar-inner">
          <Link to="/" className="team-brand">
            <ChhayaBrand className="chhaya-brand--inverse" />
            <span className="team-badge">Team view</span>
          </Link>
          <div className="action-row">
            <span>Unlocked for this tab</span>
            <button type="button" className="team-lock-btn" onClick={handleLock}>Lock</button>
          </div>
        </div>
      </header>

      <main className="page-main">
        <div className="split">
          <div className="split-aside section-stack">
            <section aria-label="Round" className="card">
              <h2>Round you are viewing</h2>
              <label htmlFor="round" className="field-label">
                Round
                <select id="round" className="field">
                  <option>Cold wave · Amber · opened 14:10</option>
                  <option>Heatwave · Red · opened 06:00 (today’s round)</option>
                </select>
              </label>
              <p className="cell-muted">Visitors see today’s round. What you do here does not change it.</p>
              <div className="action-row">
                <Link to="/alert" className="linkbtn">Alert</Link>
                <Link to="/visit" className="linkbtn">Visit list</Link>
                <Link to="/report" className="linkbtn">Report</Link>
              </div>
            </section>

            <section aria-label="Replay an alert" className="card">
              <h2>Replay an alert</h2>
              <div className="phone-fields">
                <label className="field-label grow">
                  Hazard
                  <select className="field"><option>Heatwave</option><option>Cold wave</option></select>
                </label>
                <label className="field-label grow">
                  Severity
                  <select className="field"><option value="red">Red</option><option value="orange">Amber</option><option value="yellow">Yellow</option></select>
                </label>
              </div>
              <div className="action-row">
                <button type="button" className="btn">Replay alert</button>
                <button type="button" className="btn ghost">Dispatch the round</button>
              </div>
            </section>

            <section aria-label="Today’s rounds" className="card">
              <h2>Today’s rounds</h2>
              <p className="cell-muted">A round opens every morning at 06:00 IST in each district under the alert. Opening them now deletes nothing.</p>
              <button type="button" className="btn danger team-open" onClick={handleRebuild} disabled={rebuilding}>
                {rebuilding ? 'Opening…' : 'Open today’s rounds'}
              </button>
            </section>
          </div>

          <section aria-label="Live round" className="split-main section-stack">
            <div className="warning-banner amber">
              <div className="warning-place">
                <span>Amber cold warning</span>
                <b>Churu, Rajasthan</b>
              </div>
              <div className="warning-fact"><span>Forecast low</span><b>3 °C at 06:00</b></div>
              <div className="warning-fact"><span>Source</span><b>Replay</b></div>
            </div>

            <div className="figure-grid">
              <div className="figure-card"><span className="figure-label">Queued</span><span className="figure-value">26</span></div>
              <div className="figure-card"><span className="figure-label">In call now</span><span className="figure-value">1</span></div>
              <div className="figure-card"><span className="figure-label">Reached</span><span className="figure-value">3</span></div>
              <div className="figure-card"><span className="figure-label">Unreachable</span><span className="figure-value">0</span></div>
            </div>

            <div className="table-card">
              <header><h2>Calls</h2></header>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th scope="col">Resident</th>
                      <th scope="col">Attempt</th>
                      <th scope="col">Status</th>
                      <th scope="col">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {DEFAULT_CALLS.map((c, i) => (
                      <tr key={i}>
                        <td className="cell-strong">{c.name}</td>
                        <td className="cell-mono">{c.attempt} of 3</td>
                        <td>{statusPill(c.status)}</td>
                        <td>
                          {c.queued && (
                            <div className="action-row">
                              <Link to="/call" className="linkbtn">Start in browser</Link>
                              <button type="button" className="linkbtn">Dial phone</button>
                              <button type="button" className="linkbtn">Simulate</button>
                            </div>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        </div>
      </main>
    </div>
  )
}

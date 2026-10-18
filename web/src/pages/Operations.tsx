import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ProposalCard } from '../components/OperationsAssistant'
import { OperationsCase } from '../components/OperationsCase'
import { Donut } from '../components/OperationsCharts'
import { ChhayaBrand } from '../components/ChhayaBrand'
import type { Geography, Overview, Proposal, Role, Scope, Session, Tier } from '../operations'
import { actionLabel, makeOpsApi, scopeQuery, time, words } from '../operations'
import '../styles/operations.css'

const colors = { red: 'var(--color-red)', amber: 'var(--color-amber)', green: 'var(--color-green)', reached: 'var(--color-chart-reached)', pending: 'var(--color-chart-pending)', unreachable: 'var(--color-chart-unreachable)' }

function Approval({ proposal, busy, error, onClose, onConfirm }: { proposal: Proposal; busy: boolean; error: string; onClose: () => void; onConfirm: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => { dialog.current?.showModal() }, [])
  return <dialog ref={dialog} className="ops-dialog ops-approval" onCancel={e => { if (busy) e.preventDefault(); else onClose() }} aria-labelledby="approval-title"><span className="ops-eyebrow">HUMAN APPROVAL</span><h2 id="approval-title">{actionLabel[proposal.action]}</h2><p>{proposal.resident_name} · {proposal.worker_name}</p><p className="ops-proposal-content">{proposal.content}</p>{proposal.action !== 'assign_crew' && <p>Recipient: {proposal.recipient}</p>}<p className="ops-notice">{proposal.demo ? 'This is a simulated demo action. No real call or SMS will be sent.' : proposal.action === 'assign_crew' ? 'This will create a crew assignment visible in the Field crew view.' : 'This sends a real SMS or places a real Twilio call. Trial restrictions and charges may apply.'}</p><p>No action has been executed yet.</p>{error && <p role="alert" className="ops-error">{error}</p>}<div className="ops-actions"><button className="ops-secondary" disabled={busy} onClick={onClose}>Cancel</button><button className="ops-primary" disabled={busy} onClick={onConfirm}>{busy ? 'Submitting…' : proposal.demo ? 'Confirm demo action' : 'Confirm and execute'}</button></div></dialog>
}

export function OperationsPage({ crew = false }: { crew?: boolean }) {
  const [params, setParams] = useSearchParams()
  const [session, setSession] = useState<Session | null>(null)
  const [geography, setGeography] = useState<Geography | null>(null)
  const [snapshot, setSnapshot] = useState<{ key: string; data: Overview } | null>(null)
  const [proposals, setProposals] = useState<Proposal[]>([])
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const [tab, setTab] = useState('overview')
  const [filter, setFilter] = useState('all')
  const [search, setSearch] = useState('')
  const [directorySearch, setDirectorySearch] = useState('')
  const [caseId, setCaseId] = useState('')
  const [approval, setApproval] = useState<Proposal | null>(null)
  const [approvalError, setApprovalError] = useState('')
  const [busy, setBusy] = useState(false)
  const [, setPrompt] = useState('') // the old assistant's prompt; Ask Chhaya now lives in the role views
  const [hideAssistant, setHideAssistant] = useState(false)
  const role: Role = session?.demo ? (crew ? 'crew' : 'admin') : session?.role || (crew ? 'crew' : 'admin')
  const worker = params.get('worker') || session?.worker_id || session?.workers[0]?.id || ''
  const api = useMemo(() => makeOpsApi(role, worker), [role, worker])
  const bootstrap = useMemo(() => makeOpsApi('admin', ''), [])
  const scope = useMemo<Scope>(() => ({ state_id: params.get('state') || '', district_id: params.get('district') || '', ward_id: params.get('ward') || '', round_id: params.get('round') || '' }), [params])
  const query = scopeQuery(scope)
  const dataKey = `${query}:${role}:${worker}`
  const data = snapshot?.key === dataKey ? snapshot.data : null
  const state = geography?.states.find(s => s.id === scope.state_id)
  const district = state?.districts.find(d => d.id === scope.district_id)
  const ward = district?.wards.find(w => w.id === scope.ward_id)
  const label = [state?.name, district?.name, ward?.name].filter(Boolean).join(' / ') || 'All four states'

  useEffect(() => {
    let active = true
    bootstrap<Session>('/session').then(async s => {
      if (!active) return
      setSession(s)
      const geo = await bootstrap<Geography>('/geography')
      if (active) setGeography(geo)
    }).catch(e => { if (active) setError(e.message) })
    return () => { active = false }
  }, [bootstrap])

  useEffect(() => {
    if (!session) return
    let active = true
    let loading = false
    const load = async () => {
      if (loading) return
      loading = true
      try {
        const result = await api<Overview>(`/overview?${query}`)
        const actions = role === 'admin' ? await api<Proposal[]>('/proposals') : []
        if (active) { setSnapshot({ key: dataKey, data: result }); setProposals(actions); setError('') }
      } catch (e) { if (active) setError((e as Error).message) }
      finally { loading = false }
    }
    void load()
    const timer = setInterval(() => void load(), 8000)
    return () => { active = false; clearInterval(timer) }
  }, [api, query, dataKey, role, session, revision])

  function choose(level: string, id: string) {
    const next = new URLSearchParams(params)
    next.delete('round')
    if (level === 'state') { next.delete('district'); next.delete('ward') }
    if (level === 'district') next.delete('ward')
    if (id) next.set(level, id); else next.delete(level)
    setParams(next); setFilter('all'); setSearch('')
  }

  function chooseGroup(level: string, id: string, tier: Tier) {
    const next = new URLSearchParams(params)
    if (level === 'district') {
      const parent = geography?.states.find(s => s.districts.some(d => d.id === id))
      if (parent) next.set('state', parent.id)
      next.delete('ward')
    }
    next.set(level, id); next.delete('round'); setParams(next); setFilter(tier)
  }

  async function decide(p: Proposal, approve: boolean) {
    if (approve) { setApproval(p); setApprovalError(''); return }
    setBusy(true)
    try { await api(`/proposals/${p.id}/reject`, {}); setRevision(r => r + 1) }
    catch (e) { setError((e as Error).message) }
    finally { setBusy(false) }
  }

  async function confirm() {
    if (!approval) return
    setBusy(true); setApprovalError('')
    try { await api(`/proposals/${approval.id}/approve`, {}); setApproval(null); setRevision(r => r + 1) }
    catch (e) { setApprovalError((e as Error).message) }
    finally { setBusy(false) }
  }

  const visibleCases = data?.cases.filter(c => (filter === 'all' || filter === c.tier || (filter === 'open' && c.status === 'open' && c.tier !== 'green') || (filter === 'visited' && c.status !== 'open')) && `${c.resident_name} ${c.ward_name} ${c.reason_labels.join(' ')}`.toLowerCase().includes(search.toLowerCase())) || []
  const pending = proposals.filter(p => p.status === 'pending')
  const topLabel = role === 'crew' ? 'Your next visit matters.' : 'A clearer picture. A faster response.'

  return <div className={`ops-app ${role === 'crew' ? 'ops-crew-app' : ''}`}>
    <header className="ops-header"><Link className="ops-brand" to="/"><ChhayaBrand /></Link><nav aria-label="Operations navigation"><button aria-current={tab === 'overview' ? 'page' : undefined} onClick={() => setTab('overview')}>Overview</button>{role === 'admin' && <button aria-current={tab === 'dispatch' ? 'page' : undefined} onClick={() => setTab('dispatch')}>Dispatch {pending.length > 0 && <span className="ops-count">{pending.length}</span>}</button>}<button aria-current={tab === 'activity' ? 'page' : undefined} onClick={() => setTab('activity')}>Activity</button><Link to="/alert">Test voice agent</Link></nav><div className="ops-header-right"><span className="ops-pill">{session?.demo ? 'DEMO ' : ''}{role.toUpperCase()}</span>{session?.demo ? <Link to={role === 'admin' ? '/crew?state=RJ&district=churu' : '/?state=RJ&district=churu'}>{role === 'admin' ? 'Field crew view' : 'Regional Officer view'}</Link> : <a href="/login">Account</a>}</div></header>
    <div className="ops-data-banner">{session?.demo ? 'Isolated demo workspace · Synthetic Churu residents and wards · Notifications are simulated' : 'Operations workspace · Review recipients before approving calls or messages'}{session && !session.persistent && <span> · Memory store: history resets when the backend restarts</span>}</div>
    <main className="ops-main"><div className="ops-page-heading"><div><span className="ops-eyebrow">{role === 'admin' ? 'HEAT & COLD WAVE RESPONSE' : 'Field crew workspace'}</span><h1>{topLabel}</h1><p>{role === 'admin' ? 'Understand the risk. Reach the resident. Coordinate care.' : 'Call evidence, clear assignments, and a record of every visit.'}</p></div><div className="ops-heading-actions"><small>{data ? `Updated ${time(data.as_of)}` : 'Connecting to backend…'}</small><button className="ops-secondary" onClick={() => setRevision(r => r + 1)}>Refresh</button>{role === 'admin' && <button className="ops-secondary" onClick={() => setHideAssistant(!hideAssistant)}>{hideAssistant ? 'Show assistant' : 'Focus dashboard'}</button>}</div></div>
      {error && <div role="alert" className="ops-error">{error} {data ? 'Displayed data may be stale.' : ''} <a href="/login">Sign in</a></div>}
      <section className="ops-filters" aria-label="Geographic filters"><label>State<select value={scope.state_id} onChange={e => choose('state', e.target.value)}><option value="">All four states</option>{geography?.states.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label><label>District<select value={scope.district_id} disabled={!state} onChange={e => choose('district', e.target.value)}><option value="">All districts</option>{state?.districts.map(d => <option key={d.id} value={d.id}>{d.name}{d.onboarded ? ' · Onboarded' : ''}</option>)}</select></label><label>Ward<select value={scope.ward_id} disabled={!district} onChange={e => choose('ward', e.target.value)}><option value="">All wards</option>{district?.wards.map(w => <option key={w.id} value={w.id}>{w.name}{w.synthetic ? ' · Demo' : ''}</option>)}</select></label>{session?.demo && role === 'crew' && <label>Crew member<select value={worker} onChange={e => choose('worker', e.target.value)}>{session.workers.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}</select></label>}{district && <label>Round<select value={scope.round_id} onChange={e => choose('round', e.target.value)}><option value="">Latest available</option>{data?.available_rounds.map(r => <option key={r.id} value={r.id}>{r.hazard} · {time(r.opened_at)}</option>)}</select></label>}<div className="ops-filter-meta"><strong>{label}</strong><small>{scope.round_id || 'Latest available round per district'}</small></div></section>
      <div className={`ops-workspace ${role === 'crew' || hideAssistant ? 'ops-full-width' : ''}`}><div className="ops-dashboard">
        {!data && !error && <div className="ops-empty" role="status">Loading scoped records…</div>}
        {data && <>
          <section className="ops-kpis" aria-label="Key metrics">{[['registered', 'Registered', 'all'], ['reached', 'Residents reached', 'reached'], ['priority', 'Priority cases open', 'open'], ['visited', 'Visits recorded', 'visited']].map(([key, title, target]) => <button key={key} className={key === 'priority' && data.counts[key] > 0 ? 'ops-kpi urgent' : 'ops-kpi'} onClick={() => { setFilter(target); setTab(key === 'registered' || target === 'reached' ? 'outreach' : 'overview') }}><span>{title}</span><strong>{data.has_data ? data.counts[key] : '—'}</strong><small>{data.has_data ? 'View matching records' : 'No operational data'}</small></button>)}</section>
          {tab === 'overview' && <>
            {data.rounds.map(r => <div className="ops-alert-band" key={r.id}><strong>{r.severity?.toUpperCase() || 'RECORDED'} {r.hazard.toUpperCase()} WARNING</strong><span>{r.district_id} · Round opened {time(r.opened_at)}{r.peak_temp_c != null ? ` · Forecast peak ${r.peak_temp_c}°C` : ''}</span></div>)}
            {role === 'admin' && <section className="ops-card ops-directory"><header><div><h2>{district ? 'Ward directory' : state ? `${state.name} districts` : 'State directory'}</h2><p>Official state/district names. Operational coverage is shown separately.</p></div><input type="search" aria-label="Search geographic directory" placeholder="Find an area…" value={directorySearch} onChange={e => setDirectorySearch(e.target.value)} /></header><div className="ops-directory-list">{district ? (district.wards.length ? district.wards.filter(w => w.name.toLowerCase().includes(directorySearch.toLowerCase())).map(w => <button key={w.id} onClick={() => choose('ward', w.id)}><strong>{w.name}</strong><small>{w.synthetic ? 'Synthetic pilot ward' : 'Onboarded ward'}</small><span>Open ward →</span></button>) : <p className="ops-muted">No verified wards onboarded for this district. No resident data is implied.</p>) : state ? state.districts.filter(d => d.name.toLowerCase().includes(directorySearch.toLowerCase())).map(d => <button key={d.id} onClick={() => choose('district', d.id)}><strong>{d.name}</strong><small>{d.onboarded ? `${d.wards.length} onboarded wards` : 'Not onboarded'}</small><span>Open district →</span></button>) : geography?.states.filter(s => s.name.toLowerCase().includes(directorySearch.toLowerCase())).map(s => <button key={s.id} onClick={() => choose('state', s.id)}><strong>{s.name}</strong><small>{s.districts.length} districts · {s.districts.filter(d => d.onboarded).length} onboarded</small><span>Explore districts →</span></button>)}</div><footer><small>{geography?.source} · Retrieved {geography?.retrieved_at}</small>{state && <a href={state.source_url} target="_blank" rel="noreferrer">View source</a>}</footer></section>}
            {!data.has_data ? <section className="ops-empty"><h2>No operational data for this area yet</h2><p>The directory entry is real; no health data, calls, or ward coverage have been invented.</p><button className="ops-primary" onClick={() => setParams({ state: 'RJ', district: 'churu' })}>Explore the Churu pilot</button></section> : <>
              {role === 'admin' && <><div className="ops-chart-grid"><Donut title="Risk distribution" subtitle={`${data.counts.unassessed} targeted residents not yet assessed`} centerLabel="assessed cases" segments={(['red', 'amber', 'green'] as Tier[]).map(t => ({ key: t, label: words(t), value: data.tiers[t], color: colors[t] }))} onSelect={setFilter} /><Donut title="Outreach coverage" subtitle="Unique residents, not call attempts" centerLabel="targeted" segments={['reached', 'pending', 'unreachable'].map(t => ({ key: t, label: words(t), value: data.coverage[t], color: colors[t as keyof typeof colors] }))} onSelect={key => { setFilter(key); setTab('outreach') }} /></div>
                <section className="ops-card"><header><div><h2>Risk by {scope.district_id ? 'ward' : 'district'}</h2><p>Choose a segment to explore the underlying cases.</p></div><span className="ops-pill">Rules-based triage</span></header><div className="ops-bars">{data.groups.map(g => { const max = Math.max(1, ...data.groups.map(row => row.red + row.amber + row.green)); return <div key={g.id} className="ops-bar-row"><strong>{g.name}</strong><div className="ops-stack">{(['red', 'amber', 'green'] as Tier[]).filter(t => g[t] > 0).map(t => <button key={t} style={{ width: `${g[t] / max * 100}%`, background: colors[t] }} aria-label={`${g.name}: ${g[t]} ${t} cases`} title={`${g[t]} ${t}`} onClick={() => chooseGroup(g.level, g.id, t)}>{g[t]}</button>)}</div><span>{g.red + g.amber + g.green}</span></div> })}</div></section>
              </>}
              <section className="ops-card"><header><div><h2>{role === 'crew' ? 'Your assigned residents' : 'Residents & response'}</h2><p>{visibleCases.length} matching cases · most urgent first</p></div><input type="search" aria-label="Search residents" placeholder="Search residents or concerns…" value={search} onChange={e => setSearch(e.target.value)} /></header><div className="ops-tabs" role="group" aria-label="Filter cases">{['all', 'red', 'amber', 'green', 'open', 'visited'].map(f => <button key={f} aria-pressed={filter === f} onClick={() => setFilter(f)}>{f === 'open' ? 'Awaiting visit' : words(f)}</button>)}</div><div className="ops-table-scroll"><table className="ops-table"><thead><tr><th>Resident</th><th>Risk / concern</th><th>Response</th><th>Action</th></tr></thead><tbody>{visibleCases.map(c => <tr key={c.id}><td><button className="ops-text-button" onClick={() => setCaseId(c.id)}>{c.resident_name}</button><small>{c.age} years · {c.ward_name}</small></td><td><span className={`ops-pill ${c.tier}`}>{c.tier}</span><small>{c.reason_labels.slice(0, 2).join(' · ')}</small></td><td>{words(c.task_status)}<small>{c.status === 'open' ? `Opened ${time(c.opened_at)}` : `Visited ${time(c.visited_at)}`}</small></td><td><button className="ops-secondary" onClick={() => setCaseId(c.id)}>Open case</button></td></tr>)}</tbody></table></div>{!visibleCases.length && <p className="ops-empty">No cases match this filter.</p>}</section>
              <section className="ops-card"><header><div><h2>Conversation feed</h2><p>Resident voice calls, separate from the admin assistant.</p></div></header><div className="ops-feed-grid">{data.cases.slice(0, 3).map(c => <button className="ops-feed-card" key={c.id} onClick={() => setCaseId(c.id)}><div><strong>{c.resident_name}</strong><span className={`ops-pill ${c.tier}`}>{c.tier}</span></div><p>{c.note || c.reason_labels.join(' · ')}</p><small>View summary, transcript, and timestamps →</small></button>)}</div></section>
            </>}
          </>}
          {tab === 'outreach' && <section className="ops-card"><header><h2>Outreach records</h2><button className="ops-secondary" onClick={() => { setTab('overview'); setFilter('all') }}>Back to overview</button></header><div className="ops-tabs">{['all', 'reached', 'pending', 'unreachable', 'not_targeted'].map(f => <button key={f} aria-pressed={filter === f} onClick={() => setFilter(f)}>{words(f)}</button>)}</div><table className="ops-table"><thead><tr><th>Resident</th><th>Ward</th><th>Outreach</th><th>Case</th></tr></thead><tbody>{data.residents.filter(r => filter === 'all' || r.outreach === filter).map(r => <tr key={r.id}><td>{r.name}</td><td>{r.ward_name}</td><td>{words(r.outreach)}</td><td>{r.case_id ? <button className="ops-text-button" onClick={() => setCaseId(r.case_id!)}>View case</button> : 'Not assessed'}</td></tr>)}</tbody></table></section>}
          {tab === 'dispatch' && role === 'admin' && <><section className="ops-card"><header><div><h2>Approval & execution history</h2><p>Every proposal is reviewed before it changes a task or contacts anyone.</p></div></header>{!proposals.length && <p className="ops-empty">Open a resident case or use Agent mode to prepare a dispatch.</p>}<div className="ops-proposal-grid">{proposals.map(p => <ProposalCard key={p.id} proposal={p} busy={busy} onDecision={decide} />)}</div></section><section className="ops-card"><h2>Crew capacity</h2>{data.workers.map(w => <div className="ops-worker" key={w.id}><strong>{w.name}</strong><span>{w.active_tasks} active tasks</span></div>)}</section></>}
          {tab === 'activity' && <section className="ops-card"><header><div><h2>Call & response timeline</h2><p>Provider receipts are not crew acknowledgments. All times are IST.</p></div></header><ol className="ops-timeline">{data.events.map(e => <li key={e.id}><div><strong>{words(e.event)}</strong><span className="ops-pill">{e.actor}</span></div><p>{e.detail}</p><small>{time(e.at)}</small>{e.case_id && <button className="ops-text-button" onClick={() => setCaseId(e.case_id)}>Open case</button>}</li>)}</ol>{!data.events.length && <p className="ops-empty">No recorded events for this scope.</p>}</section>}
        </>}
        <footer className="ops-footer">{geography?.ward_note}<br />A registry is not population-wide surveillance. Counts refer only to onboarded residents.</footer>
      </div></div>
    </main>
    {caseId && <OperationsCase key={caseId} id={caseId} api={api} admin={role === 'admin'} workers={data?.workers || []} onClose={() => setCaseId('')} onProposal={p => { setTab('dispatch'); setRevision(r => r + 1); setProposals(rows => [p, ...rows]) }} onUpdate={() => setRevision(r => r + 1)} onAsk={text => { setPrompt(text); setHideAssistant(false) }} />}
    {approval && <Approval proposal={approval} busy={busy} error={approvalError} onClose={() => setApproval(null)} onConfirm={confirm} />}
  </div>
}

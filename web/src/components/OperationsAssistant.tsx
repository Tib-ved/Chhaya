import type { Proposal } from '../operations'
import { actionLabel, time, words } from '../operations'

export function ProposalCard({ proposal: p, busy, onDecision }: { proposal: Proposal; busy: boolean; onDecision: (p: Proposal, approve: boolean) => void }) {
  return <article className="ops-proposal">
    <header><strong>{actionLabel[p.action]}</strong><span className={`ops-pill ${p.status === 'pending' ? 'amber' : ''}`}>{words(p.status)}</span></header>
    <p>{p.resident_name} · {p.worker_name}</p>
    <p className="ops-proposal-content">{p.content}</p>
    {p.action !== 'assign_crew' && <small>Recipient: {p.recipient}</small>}
    <small>{p.demo ? 'Demo only. No external call or message will be sent.' : p.action === 'assign_crew' ? 'This will update the worker’s assignment.' : 'This will make a real call or send a message through Twilio. Charges may apply.'}</small>
    <small>Proposed {time(p.created_at)}</small>
    {p.approved_at && <small>Approved {time(p.approved_at)}</small>}
    {p.provider_updated_at && <small>Provider update {time(p.provider_updated_at)}</small>}
    {p.error && <p role="alert" className="ops-error">{p.error}</p>}
    {p.status === 'pending' && <><small>Approval expires {time(p.expires_at)}</small><div className="ops-actions"><button className="ops-primary" disabled={busy} onClick={() => onDecision(p, true)}>Review & approve</button><button className="ops-secondary" disabled={busy} onClick={() => onDecision(p, false)}>Reject</button></div></>}
  </article>
}

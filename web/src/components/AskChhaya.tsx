// Ask Chhaya: the regional officer's analytics assistant. It reads; it never changes a record.
import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import type { AskMessage, Focus, SessionRow, StreamEvent } from '../lib/askApi'
import type { AskRequest } from './WorkspaceContext'
import { createSession, getSession, listSessions, sendMessage } from '../lib/askApi'
import { ApiError } from '../lib/api'
import { AskVisual } from './AskVisual'
import { ChhayaBrand } from './ChhayaBrand'
import { Sparkle } from './Sparkle'
import { useStickToBottom } from '../lib/useStickToBottom'
import { ASK_SESSION_KEY } from '../lib/mode'

// The Live/Demo switch forgets this conversation (lib/mode.ts switchMode): a session is not resumed on the other dataset.
const SESSION_KEY = ASK_SESSION_KEY
const remembered = () => { try { return sessionStorage.getItem(SESSION_KEY) } catch { return null } }
const remember = (id: string | null) => { try { if (id) sessionStorage.setItem(SESSION_KEY, id); else sessionStorage.removeItem(SESSION_KEY) } catch { /* private window */ } }

function suggestionsFor(focus: Focus): string[] {
  if (focus.ward_id) return ['Who are the highest-risk residents here?', 'Which Red cases here need a visit first?', 'Summarise the latest Red call in this ward.']
  if (focus.district_id) return ['Compare Red cases across the wards in a chart.', 'How is call coverage in each ward?', 'Which residents are at highest risk here?']
  return ['Which districts are under a heat alert?', 'Show call coverage for each district as a table.', 'Where are the most Red cases?']
}

function Answer({ message, onCase }: { message: AskMessage; onCase: (id: string) => void }) {
  return <article className={`ops-message ${message.role}`} aria-busy={message.streaming || undefined}>
    <header><strong>{message.role === 'user' ? 'You' : 'Chhaya'}</strong>{message.kind === 'refused' && <small>Outside what Chhaya covers</small>}</header>
    {message.role === 'user'
      ? <p>{message.text}</p>
      : message.text && <div className="ask-markdown"><Markdown skipHtml disallowedElements={['img', 'a']} unwrapDisallowed>{message.text}</Markdown></div>}
    {message.visuals.map(v => <AskVisual key={v.id} visual={v} />)}
    {message.streaming && message.status && <p className="ask-status" role="status">{message.status}</p>}
    {message.error && <p className="ops-error" role="alert">{message.error}</p>}
    {!message.streaming && message.role === 'assistant' && message.kind !== 'refused' && message.text && <small>AI answer from Chhaya's records. Check the linked cases before acting.</small>}
    {!!message.case_ids?.length && <div className="ask-sources">{message.case_ids.slice(0, 6).map(id => <button key={id} type="button" className="ops-source" onClick={() => onCase(id)}>Open case {id.split('_').at(-1)}</button>)}</div>}
  </article>
}

/** The mark beside "Ask Chhaya", on the top-bar button and the panel's title. */

const ExpandIcon = ({ expanded }: { expanded: boolean }) => <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
  {expanded
    ? <><polyline points="4 14 10 14 10 20" /><polyline points="20 10 14 10 14 4" /><line x1="14" y1="10" x2="21" y2="3" /><line x1="3" y1="21" x2="10" y2="14" /></>
    : <><polyline points="15 3 21 3 21 9" /><polyline points="9 21 3 21 3 15" /><line x1="21" y1="3" x2="14" y2="10" /><line x1="3" y1="21" x2="10" y2="14" /></>}
</svg>

export function AskChhaya({ focus, focusLabel, onCase, onHide, expanded = false, onToggleExpand, pending, onPendingTaken }: { focus: Focus; focusLabel: string; onCase: (id: string) => void; onHide: () => void; expanded?: boolean; onToggleExpand?: () => void; pending?: AskRequest | null; onPendingTaken?: () => void }) {
  const [sessionId, setSessionId] = useState<string | null>(remembered)
  const [messages, setMessages] = useState<AskMessage[]>([])
  const [history, setHistory] = useState<SessionRow[] | null>(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [offline, setOffline] = useState(false)
  // The conversation follows the answer as it streams in, unless the reader scrolls up
  const { ref: logRef, onScroll: onLogScroll, stick: followLatest } = useStickToBottom<HTMLDivElement>(history ? null : messages)
  const abort = useRef<AbortController | null>(null)
  useEffect(() => () => abort.current?.abort(), [])
  useEffect(() => {
    if (!sessionId || messages.length) return
    let active = true
    // A question asked meanwhile (from a resident's case) is kept: the earlier turns stay in History
    getSession(sessionId).then(view => { if (active) setMessages(all => all.length ? all : view.messages) })
      .catch(() => { if (active) { remember(null); setSessionId(null) } })
    return () => { active = false }
  }, [sessionId, messages.length])

  const update = (change: (m: AskMessage) => AskMessage) =>
    setMessages(all => all.map((m, i) => i === all.length - 1 ? change(m) : m))

  function onEvent(e: StreamEvent) {
    switch (e.event) {
      case 'status': update(m => ({ ...m, status: e.data.label })); break
      case 'delta': update(m => ({ ...m, text: m.text + e.data.text })); break
      case 'chart': update(m => ({ ...m, visuals: [...m.visuals, { kind: 'chart', ...e.data }] })); break
      case 'table': update(m => ({ ...m, visuals: [...m.visuals, { kind: 'table', ...e.data }] })); break
      case 'sources': update(m => ({ ...m, case_ids: e.data.case_ids })); break
      case 'done': update(m => ({ ...m, kind: e.data.kind, streaming: false })); break
      case 'error': update(m => ({ ...m, error: e.data.message, streaming: false })); break
      case 'title': break
    }
  }

  async function newSession(): Promise<string> {
    const made = await createSession(focus)
    setSessionId(made.session_id); remember(made.session_id)
    return made.session_id
  }

  async function send(words = text) {
    const question = words.trim()
    if (!question || busy) return
    setBusy(true); setError(''); setText('')
    followLatest()
    setMessages(all => [...all, { role: 'user', text: question, visuals: [] }, { role: 'assistant', text: '', visuals: [], streaming: true, status: 'Reading the question…' }])
    abort.current = new AbortController()
    try {
      const id = sessionId ?? await newSession()
      await sendMessage(id, question, focus, onEvent, abort.current.signal)
      update(m => ({ ...m, streaming: false }))
    } catch (e) {
      if ((e as Error).name === 'AbortError') return
      if (e instanceof ApiError && e.status === 503) setOffline(true)
      // Gone, or the other mode's (a tab that kept it across the Live/Demo switch): the next send starts a new conversation.
      if (e instanceof ApiError && (e.status === 404 || (e.status === 409 && e.reason === 'other_mode'))) { remember(null); setSessionId(null) }
      setMessages(all => all.slice(0, -1))
      setError((e as Error).message); setText(question)
    } finally { setBusy(false) }
  }

  // A question sent from a resident's case is asked once, as soon as the panel is free
  const askedRequest = useRef<number | null>(null)
  useEffect(() => {
    if (!pending || busy || askedRequest.current === pending.id) return
    askedRequest.current = pending.id
    onPendingTaken?.()
    setHistory(null)
    void send(pending.text)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending, busy])

  function startOver() {
    abort.current?.abort()
    remember(null); setSessionId(null); setMessages([]); setHistory(null); setError('')
  }

  async function showHistory() {
    if (history) { setHistory(null); return }
    try { setHistory(await listSessions()) } catch (e) { setError((e as Error).message) }
  }

  async function open(id: string) {
    try {
      const view = await getSession(id)
      followLatest()
      setSessionId(id); remember(id); setMessages(view.messages); setHistory(null)
    } catch (e) { setError((e as Error).message) }
  }

  return <aside className={`ops-assistant ask-chhaya ${expanded ? 'expanded' : ''}`} aria-label="Ask Chhaya">
    <header className="ops-assistant-head">
      <div className="ask-title">
        {onToggleExpand && <button type="button" className="ops-panel-hide ask-expand" onClick={onToggleExpand} aria-pressed={expanded} aria-label={expanded ? 'Leave full view' : 'Open in full view'} title={expanded ? 'Leave full view' : 'Open in full view'}><ExpandIcon expanded={expanded} /></button>}
        <div><span className="ops-eyebrow">Analytics · read only</span><h2 tabIndex={-1}><Sparkle size={20} />Ask Chhaya</h2></div>
      </div>
      <div className="ops-assistant-head-actions">
        <button type="button" onClick={() => void showHistory()} aria-expanded={!!history}>History</button>
        <button type="button" disabled={busy} onClick={startOver}>New chat</button>
        <button type="button" className="ops-panel-hide" onClick={onHide} aria-label="Close Ask Chhaya" title="Close"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg></button>
      </div>
    </header>
    {offline && <div className="ops-offline" role="status"><strong>Ask Chhaya isn't connected yet</strong><span>It needs its language model; try again later.</span></div>}
    <div className="ops-context"><span>In focus</span><strong>{focusLabel}</strong><small>"Here" means this area; you can ask about any other too.</small></div>
    {history ? <div className="ops-messages"><h3>Conversations</h3>
      {!history.length && <p>No saved conversations yet.</p>}
      {history.map(s => <button type="button" className="ops-history-row" key={s.session_id} onClick={() => void open(s.session_id)}><strong>{s.title}</strong><small>{new Date(s.updated_at).toLocaleString()}</small></button>)}
    </div> : <div className="ops-messages" ref={logRef} onScroll={onLogScroll} role="log" aria-live="polite" aria-label="Ask Chhaya conversation">
      {!messages.length && <div className="ops-assistant-welcome"><ChhayaBrand variant="mark" className="ops-assistant-mark" />
        <h3>Ask about cases, risk and calls</h3><p>Counts, comparisons, charts, tables and call summaries. Chhaya suggests; it never changes a record.</p>
        {suggestionsFor(focus).map(q => <button type="button" key={q} onClick={() => void send(q)}>{q}</button>)}
      </div>}
      {messages.map((m, i) => <Answer key={i} message={m} onCase={onCase} />)}
    </div>}
    {error && <p className="ops-error" role="alert">{error}</p>}
    <form className="ops-composer" onSubmit={e => { e.preventDefault(); void send() }}>
      <label className="ops-sr-only" htmlFor="ask-message">Ask Chhaya</label>
      <textarea id="ask-message" value={text} maxLength={2000} rows={3} disabled={busy} placeholder="Ask about cases, risk or calls…"
        onChange={e => setText(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send() } }} />
      <div><small>Answers only · {text.length}/2000</small><button className="ops-primary" type="submit" disabled={busy || !text.trim()}>{busy ? 'Answering…' : 'Send'}</button></div>
    </form>
  </aside>
}

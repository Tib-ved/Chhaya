import { useEffect, useRef, useState } from 'react'
import type { Action, Detail, OpsApi, Proposal, Worker } from '../operations'
import { answerLabel, areaName, eventLabel, languageName, localTime, noteText, personName, questionLabel, reasonLabel, statusLabel, text, workerName, type UiLanguage } from '../lib/wardLocale'

export function OperationsCase({ id, api, admin, workers, onClose, onProposal, onUpdate, onAsk, language = 'en' }: { id: string; api: OpsApi; admin: boolean; workers: Worker[]; onClose: () => void; onProposal: (p: Proposal) => void; onUpdate: () => void; onAsk: (text: string) => void; language?: UiLanguage }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [data, setData] = useState<Detail | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [worker, setWorker] = useState('')
  const [outcome, setOutcome] = useState('supplies_given')
  const [note, setNote] = useState('')
  const [tab, setTab] = useState('summary')
  const t = (english: string, hindi: string) => text(language, english, hindi)
  const tabs: Record<string, string> = { summary: t('summary', 'सार'), transcript: t('transcript', 'बातचीत'), timeline: t('timeline', 'घटनाक्रम') }
  useEffect(() => { dialog.current?.showModal() }, [])
  useEffect(() => {
    let active = true
    const load = () => api<Detail>(`/cases/${encodeURIComponent(id)}`).then(d => { if (active) { setData(d); setWorker(w => w || d.case.worker_id || ''); setError('') } }).catch(e => { if (active) setError(e.message) })
    void load()
    const timer = setInterval(() => void load(), 7000)
    return () => { active = false; clearInterval(timer) }
  }, [api, id])
  async function propose(action: Action) {
    setBusy(true); setError('')
    try { const p = await api<Proposal>('/proposals', { action, case_id: id, worker_id: worker || undefined }); onProposal(p); onClose() }
    catch (e) { setError((e as Error).message) }
    finally { setBusy(false) }
  }
  async function update(status: string) {
    if (!data?.task) return
    setBusy(true); setError('')
    try { await api(`/tasks/${data.task.id}`, { status, outcome, note }); setData(await api<Detail>(`/cases/${id}`)); onUpdate() }
    catch (e) { setError((e as Error).message) }
    finally { setBusy(false) }
  }
  return <dialog lang={language} ref={dialog} className="ops-dialog ops-case-dialog" aria-labelledby="case-title" onCancel={onClose} onClose={onClose}>
    <div className="ops-dialog-header"><div><span className="ops-eyebrow">{t('Resident case', 'निवासी का विवरण')}</span><h2 id="case-title">{data ? personName(data.resident.name, language) : t('Loading case…', 'जानकारी आ रही है…')}</h2>{data && <p>{data.resident.age} {t('years', 'साल')} · {areaName(data.case.ward_name, language)} · {languageName(data.resident.language, language)}</p>}</div><button className="ops-close" onClick={onClose} aria-label={t('Close case', 'विवरण बंद करें')}>{t('Close', 'बंद करें')}</button></div>
    {error && <div className="ops-error" role="alert">{language === 'hi' ? <>{'जानकारी सहेजी या पढ़ी नहीं जा सकी। कृपया फिर कोशिश करें।'}<details><summary>तकनीकी विवरण</summary><span lang="en">{error}</span></details></> : error}</div>}
    {data && <div className="ops-case-body"><section className="ops-case-facts">
      <div className="ops-actions"><span className={`ops-pill ${data.case.tier}`}>{statusLabel(data.case.tier, language)}</span><span className="ops-pill">{statusLabel(data.case.status, language)}</span></div>
      <h3>{t('Why this priority level', 'यह प्राथमिकता क्यों दी गई')}</h3><ul>{data.case.reason_labels.map((reason, index) => <li key={reason}>{reasonLabel(data.case.reason_ids[index], reason, language)}</li>)}</ul>
      <small>{t('This priority comes from recorded answers and fixed rules. It is not a diagnosis. The demo rules need clinical review.', 'यह प्राथमिकता दर्ज जवाबों और तय नियमों से मिली है। यह बीमारी का निदान नहीं है। डेमो के नियमों की चिकित्सकीय समीक्षा ज़रूरी है।')}</small>
      <p>{t('Opened', 'दर्ज किया गया')} {localTime(data.case.opened_at, language)}</p>
      <h3>{t('Visit status', 'मुलाकात की स्थिति')}</h3><p>{data.task ? statusLabel(data.task.status, language) : t('No visit assigned yet', 'अभी मुलाकात का कार्य नहीं सौंपा गया है')}</p>
      {admin && data.case.status === 'open' && data.case.tier !== 'green' && <>
        <label>{t('Crew member', 'स्वास्थ्य कार्यकर्ता')}<select value={worker} onChange={e => setWorker(e.target.value)} disabled={Boolean(data.task && data.task.status !== 'completed')}><option value="">{t('Choose crew', 'कार्यकर्ता चुनें')}</option>{workers.filter(w => w.ward_ids?.includes(data.case.ward_id)).map(w => <option key={w.id} value={w.id}>{workerName(w.name, language)}</option>)}</select></label>
        {!data.task || data.task.status === 'completed' ? <button className="ops-primary" disabled={busy || !worker} onClick={() => propose('assign_crew')}>{t('Review assignment', 'कार्य सौंपने का विवरण देखें')}</button> : <><button className="ops-secondary" disabled={busy} onClick={() => propose('notify_crew')}>{t('Review worker message', 'कार्यकर्ता को भेजा जाने वाला संदेश देखें')}</button><button className="ops-secondary" disabled={busy} onClick={() => propose('notify_resident')}>{t('Review resident call', 'निवासी को की जाने वाली कॉल देखें')}</button></>}
        <small>{t('You’ll review the details before this is carried out.', 'कार्य शुरू होने से पहले आप विवरण की पुष्टि करेंगे।')}</small>
      </>}
      {data.task && !['completed', 'escalated'].includes(data.task.status) && <div className="ops-task-form">
        <label>{t('Visit note', 'मुलाकात का नोट')}<textarea value={note} onChange={e => setNote(e.target.value)} maxLength={1000} rows={3} /></label>
        {data.task.status === 'assigned' && <button className="ops-primary" disabled={busy} onClick={() => update('acknowledged')}>{t('Accept assignment', 'कार्य स्वीकार करें')}</button>}
        {data.task.status === 'acknowledged' && <button className="ops-primary" disabled={busy} onClick={() => update('arrived')}>{t('Mark arrived', 'पहुंचने की जानकारी दर्ज करें')}</button>}
        {data.task.status === 'arrived' && <><label>{t('Visit outcome', 'मुलाकात का नतीजा')}<select value={outcome} onChange={e => setOutcome(e.target.value)}><option value="supplies_given">{t('Water / supplies given', 'पानी या सामान दिया')}</option><option value="advised_ok">{t('Advice provided', 'सलाह दी')}</option><option value="referred">{t('Referred to health centre', 'स्वास्थ्य केंद्र भेजा')}</option><option value="hospital">{t('Taken to hospital', 'अस्पताल ले गए')}</option><option value="not_home">{t('Resident not home', 'निवासी घर पर नहीं मिले')}</option></select></label><button className="ops-primary" disabled={busy} onClick={() => update('completed')}>{t('Save visit outcome', 'मुलाकात का नतीजा सहेजें')}</button></>}
        <button className="ops-danger" disabled={busy} onClick={() => update('escalated')}>{t('Ask an officer to review', 'अधिकारी से मदद माँगें')}</button><small>{t('Adds a flag for the health officer. It does not call emergency services.', 'यह मामला अधिकारी के ध्यान में लाता है। इससे आपातकालीन सेवा को फ़ोन नहीं किया जाता।')}</small>
      </div>}
      {admin && <button className="ops-secondary" onClick={() => { onAsk(`Explain the risk and response for case ${id}, ${data.resident.name}.`); onClose() }}>{t('Ask Chhaya about this case', 'इस मामले के बारे में छाया से पूछें')}</button>}
    </section><section className="ops-case-evidence"><div className="ops-tabs" role="group" aria-label={t('Case evidence', 'कॉल और मुलाकात का रिकॉर्ड')}>{Object.keys(tabs).map(key => <button key={key} aria-pressed={tab === key} onClick={() => setTab(key)}>{tabs[key]}</button>)}</div>
      {tab === 'summary' && <><h3>{t('Call summary', 'कॉल का सार')}</h3><p className="ops-summary">{noteText(data.case.note || '', language).text || t('There is no summary for this call. You can read the answers and transcript.', 'इस कॉल का सार उपलब्ध नहीं है। जवाब और बातचीत का रिकॉर्ड देख सकते हैं।')}</p><small>{t('A model writes this note from the call. Check it against the resident’s answers.', 'यह सार कॉल से एक मॉडल लिखता है। इसे निवासी के मूल जवाबों से मिलाकर देखें।')}</small>{data.calls.map(c => <article key={c.call_id} className="ops-evidence-call"><h4 className="ops-call-status">{statusLabel(c.status, language)}{c.started_at && ` · ${localTime(c.started_at, language)}`}</h4>{c.questions.map(q => <div className="ops-answer" key={q.id}><strong>{questionLabel(q.id, q.label, language)}</strong><span>{answerLabel(q.id, q.value, q.value_label, language)}</span>{q.quote && <blockquote>“{q.quote}”</blockquote>}<small>{localTime(q.at, language)}</small></div>)}</article>)}</>}
      {tab === 'transcript' && <>{!data.calls.length && <p>{t('No recorded calls.', 'कोई कॉल दर्ज नहीं है।')}</p>}<p className="muted">{t('Original conversation, kept in the language spoken.', 'बातचीत जिस भाषा में हुई, उसी में दिखाई गई है।')}</p>{data.calls.map(c => <article key={c.call_id} className="ops-evidence-call"><h3>{statusLabel(c.status, language)}</h3><small>{t('Started', 'शुरू')} {localTime(c.started_at, language)} · {t('Ended', 'खत्म')} {localTime(c.ended_at, language)}</small>{!c.transcript.length && <p>{t('Transcript not recorded for this call.', 'इस कॉल की बातचीत लिखित रूप में दर्ज नहीं है।')}</p>}{c.transcript.map((entry, index) => <div key={index} className={`ops-message ${entry.role === 'resident' ? 'user' : 'assistant'}`}><header><strong>{entry.role === 'agent' ? t('Chhaya AI voice agent', 'छाया एआई वॉइस एजेंट') : statusLabel(entry.role, language)}</strong></header><p>{entry.text}</p><small>{localTime(entry.at, language)}</small></div>)}</article>)}</>}
      {tab === 'timeline' && <ol className="ops-timeline">{data.events.map(e => <li key={e.id}><strong>{eventLabel(e.event, language)}</strong><p>{e.detail}</p><small>{localTime(e.at, language)} · {statusLabel(e.actor, language)}</small></li>)}</ol>}
    </section></div>}
  </dialog>
}

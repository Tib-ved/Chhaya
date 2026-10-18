import { useEffect, useRef, useState } from 'react'
import { actOnCase, getResident } from '../lib/roleApi'
import type { CaseAction, ResidentView } from '../lib/roleApi'
import { Sparkle } from './Sparkle'
import { CoordinateLink } from './CoordinateLink'
import { answerLabel, areaName, languageName, localTime, noteText, personName, questionLabel, reasonLabel, statusLabel, text, workerName, type UiLanguage } from '../lib/wardLocale'

// The values the API accepts (backend/app/schemas.py), with the words the screens already use.
const OUTCOMES: [string, string, string][] = [
  ['supplies_given', 'Water, ORS or supplies given', 'पानी, ओ.आर.एस. या सामान दिया'],
  ['advised_ok', 'Advised, managing fine', 'समझाया, ठीक हैं'],
  ['referred', 'Referred to the health centre', 'स्वास्थ्य केंद्र भेजा'],
  ['hospital', 'Taken to hospital', 'अस्पताल ले गए'],
  ['not_home', 'Not at home', 'घर पर नहीं मिले'],
]
const TELL: [string, string, string][] = [
  ['transport', 'Needs transport to hospital', 'अस्पताल ले जाने के लिए गाड़ी चाहिए'],
  ['supplies', 'Needs supplies I do not have', 'ज़रूरी सामान मेरे पास नहीं है'],
  ['missing', 'Cannot find the person', 'व्यक्ति नहीं मिल रहा'],
  ['other', 'Something else', 'कुछ और'],
]
/** The day a recorded conversation was made, in the screen's language. */
const recordedDay = (iso: string | null | undefined, language: UiLanguage) => {
  const t = Date.parse(iso || '')
  return Number.isFinite(t) ? new Intl.DateTimeFormat(language === 'hi' ? 'hi-IN' : 'en-IN', { timeZone: 'Asia/Kolkata', day: 'numeric', month: 'short', year: 'numeric' }).format(new Date(t)) : ''
}

const SAW: [string, string, string][] = [
  ['confused', 'Confused or not making sense', 'घबराहट, बात समझ नहीं आ रही'],
  ['fainted', 'Fainted or collapsed', 'बेहोश हुए या गिर पड़े'],
  ['dry_skin', 'Hot, dry skin, not sweating', 'शरीर गर्म और सूखा, पसीना नहीं'],
  ['vomiting', 'Vomiting again and again', 'बार-बार उल्टी'],
  ['chest_breathing', 'Chest pain or trouble breathing', 'सीने में दर्द या साँस की तकलीफ़'],
  ['other', 'Something else that worries me', 'कुछ और जो चिंता की बात है'],
]

/** One resident in full, from /api/residents/{id}, with the actions the API allows this caller. */
export function ResidentPanel({ id, admin, onClose, onUpdate, onAsk, language = 'en' }: { id: string; admin: boolean; onClose: () => void; onUpdate: () => void; onAsk?: (question: string) => void; language?: UiLanguage }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [data, setData] = useState<ResidentView | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [tab, setTab] = useState('summary')
  const [outcome, setOutcome] = useState(OUTCOMES[0][0])
  const [note, setNote] = useState('')
  const [reason, setReason] = useState(TELL[0][0])
  const [observation, setObservation] = useState(SAW[0][0])
  const [worker, setWorker] = useState('')
  const [reply, setReply] = useState('')
  const t = (english: string, hindi: string) => text(language, english, hindi)
  const options = (list: [string, string, string][]) => list.map(([value, english, hindi]) => <option key={value} value={value}>{t(english, hindi)}</option>)
  const tabs: Record<string, string> = { summary: t('summary', 'सार'), transcript: t('transcript', 'बातचीत'), timeline: t('timeline', 'घटनाक्रम') }

  useEffect(() => { dialog.current?.showModal() }, [])
  useEffect(() => {
    let active = true
    const load = () => getResident(id).then(d => { if (active) { setData(d); setError('') } }).catch(e => { if (active) setError(e.message) })
    void load()
    const timer = setInterval(() => void load(), 7000)
    return () => { active = false; clearInterval(timer) }
  }, [id])

  async function act(action: CaseAction) {
    if (!data?.case) return
    setBusy(true); setError('')
    try { await actOnCase(data.case.id, action); setData(await getResident(id)); setNote(''); setReply(''); onUpdate() }
    catch (e) { setError((e as Error).message) }
    finally { setBusy(false) }
  }

  const c = data?.case
  // What the demo plays rather than records: the call was held with an AI-played resident, the visit was not made.
  const aiResident = data && (data.resident.simulated === 'ai_resident' || data.simulated === 'ai_resident')
  const recordedOn = data ? recordedDay(data.call?.recorded_at ?? data.recorded_at, language) : ''
  const aiBadge = aiResident && <span className="ops-pill simulated">{t('Simulated call · recorded with an AI-played resident', 'सिम्युलेटेड कॉल · एआई द्वारा निभाए गए निवासी के साथ रिकॉर्ड की गई')}{recordedOn && ` (${t(`recorded ${recordedOn}`, `रिकॉर्ड ${recordedOn}`)})`}</span>
  const simulatedVisit = Boolean(c?.simulated_visit || data?.simulated_visit || data?.resident.simulated_visit)
  const visitBadge = simulatedVisit && <span className="ops-pill simulated">{t('Simulated visit', 'सिम्युलेटेड मुलाक़ात')}</span>
  const needsVisit = c && c.tier !== 'green'
  const others = data?.workers.filter(w => w.id !== c?.worker_id) || []
  const milestones = c ? [
    data.call?.first_at && { at: data.call.first_at, label: t('First call attempt', 'पहली कॉल'), detail: `${data.call.attempts} ${t(data.call.attempts === 1 ? 'attempt' : 'attempts', 'बार कोशिश')}` },
    c.opened_at && { at: c.opened_at, label: t('Case opened', 'मामला दर्ज हुआ'), detail: data.rules.map(r => reasonLabel(r.id, r.label, language)).join(' · ') },
    c.escalation && { at: c.escalation.at, label: t('Worker flagged it to the officer', 'कार्यकर्ता ने अधिकारी को बताया'), detail: [c.escalation.reason_label, c.escalation.note].filter(Boolean).join(' · ') },
    c.escalation?.acknowledged_at && { at: c.escalation.acknowledged_at, label: t('Officer acknowledged the flag', 'अधिकारी ने देख लिया'), detail: c.escalation.reply },
    c.raised && { at: c.raised.at, label: t('Raised to Red after seeing the person', 'मिलने के बाद लाल श्रेणी में किया'), detail: c.raised.observation_label },
    c.visited_at && { at: c.visited_at, label: t('Visit recorded', 'मुलाकात दर्ज की गई'), detail: [c.simulated_visit && t('Simulated visit', 'सिम्युलेटेड मुलाक़ात'), c.outcome_label, c.visit_note].filter(Boolean).join(' · ') },
  ].filter((m): m is { at: string; label: string; detail: string } => Boolean(m)).sort((a, b) => Date.parse(a.at) - Date.parse(b.at)) : []

  return <dialog lang={language} ref={dialog} className="ops-dialog ops-case-dialog" aria-labelledby="case-title" onCancel={onClose} onClose={onClose}>
    <div className="ops-dialog-header"><div><span className="ops-eyebrow">{t('Resident case', 'निवासी का विवरण')}</span><h2 id="case-title">{data ? (language === 'hi' && data.resident.name_hi) || personName(data.resident.name, language) : t('Loading case…', 'जानकारी आ रही है…')}</h2>{data && <p>{data.resident.age} {t('years', 'साल')} · {areaName(data.resident.ward_name || '', language)}{data.resident.language && ` · ${languageName(data.resident.language, language)}`}{data.resident.lat != null && data.resident.lng != null && <> · <CoordinateLink lat={data.resident.lat} lng={data.resident.lng} /></>}</p>}</div><button className="ops-close" onClick={onClose} aria-label={t('Close case', 'विवरण बंद करें')}>{t('Close', 'बंद करें')}</button></div>
    {error && <div className="ops-error" role="alert">{language === 'hi' ? <>{'जानकारी सहेजी या पढ़ी नहीं जा सकी। कृपया फिर कोशिश करें।'}<details><summary>तकनीकी विवरण</summary><span lang="en">{error}</span></details></> : error}</div>}
    {data && <div className="ops-case-body"><section className="ops-case-facts">
      {aiBadge && <div className="ops-actions">{aiBadge}</div>}
      {c ? <>
        <div className="ops-actions"><span className={`ops-pill ${c.tier}`}>{statusLabel(c.tier, language)}</span><span className="ops-pill">{statusLabel(c.status, language)}</span>{c.escalation && !c.escalation.acknowledged_at && <span className="ops-pill">{t('Flagged by worker', 'कार्यकर्ता ने बताया')}</span>}</div>
        <h3>{t('Why this priority level', 'यह प्राथमिकता क्यों दी गई')}</h3><ul>{data.rules.map(rule => <li key={rule.id}>{reasonLabel(rule.id, rule.label, language)}</li>)}</ul>
        <small>{t('This priority comes from recorded answers and fixed rules. It is not a diagnosis. The demo rules need clinical review.', 'यह प्राथमिकता दर्ज जवाबों और तय नियमों से मिली है। यह बीमारी का निदान नहीं है। डेमो के नियमों की चिकित्सकीय समीक्षा ज़रूरी है।')}</small>
        <p>{t('Opened', 'दर्ज किया गया')} {localTime(c.opened_at, language)}</p>
        <h3>{t('Visit status', 'मुलाकात की स्थिति')}</h3>
        <p>{c.status === 'visited' && visitBadge && <>{visitBadge} </>}{c.status === 'visited' ? `${c.outcome_label || t('Visited', 'मुलाकात दर्ज')} · ${localTime(c.visited_at, language)}` : needsVisit ? `${t('Waiting for', 'मुलाकात बाकी:')} ${data.resident.worker_name ? workerName(data.resident.worker_name, language) : t('a worker', 'कार्यकर्ता')}` : t('No visit needed', 'मुलाकात की ज़रूरत नहीं')}</p>
        {c.visit_note && <blockquote>“{c.visit_note}”</blockquote>}
        {c.escalation && <p><strong>{t('Flag', 'अधिकारी को सूचना')}:</strong> {c.escalation.reason_label}{c.escalation.note && ` · “${c.escalation.note}”`}{c.escalation.reply && <><br />{t('Officer’s reply', 'अधिकारी का जवाब')}: “{c.escalation.reply}”</>}</p>}

        {admin && c.can_acknowledge && <div className="ops-task-form">
          <label>{t('Reply to the worker', 'कार्यकर्ता को जवाब')}<textarea value={reply} onChange={e => setReply(e.target.value)} maxLength={1000} rows={2} /></label>
          <button className="ops-primary" disabled={busy} onClick={() => act({ action: 'acknowledge', reply })}>{t('Acknowledge the flag', 'सूचना देख ली')}</button>
        </div>}
        {admin && c.can_act && <div className="ops-task-form">
          <label>{t('Give to another worker', 'दूसरे कार्यकर्ता को सौंपें')}<select value={worker} onChange={e => setWorker(e.target.value)}><option value="">{t('Choose a worker', 'कार्यकर्ता चुनें')}</option>{others.map(w => <option key={w.id} value={w.id}>{(language === 'hi' && w.name_hi) || w.name} · {w.ward_name} · {w.open} {t('open', 'बाकी')}</option>)}</select></label>
          <button className="ops-secondary" disabled={busy || !worker} onClick={() => act({ action: 'reassign', worker_id: worker })}>{t('Reassign', 'सौंपें')}</button>
        </div>}
        {!admin && c.can_act && <div className="ops-task-form">
          <label>{t('Visit outcome', 'मुलाकात का नतीजा')}<select value={outcome} onChange={e => setOutcome(e.target.value)}>{options(OUTCOMES)}</select></label>
          <label>{t('Visit note', 'मुलाकात का नोट')}<textarea value={note} onChange={e => setNote(e.target.value)} maxLength={1000} rows={3} /></label>
          <button className="ops-primary" disabled={busy} onClick={() => act({ action: 'visit', outcome, note })}>{t('Save visit outcome', 'मुलाकात का नतीजा सहेजें')}</button>
          {c.tier === 'amber' && <><label>{t('What you saw', 'आपने क्या देखा')}<select value={observation} onChange={e => setObservation(e.target.value)}>{options(SAW)}</select></label>
            <button className="ops-secondary" disabled={busy} onClick={() => act({ action: 'raise', observation })}>{t('Raise to Red', 'लाल श्रेणी में करें')}</button></>}
          {!c.escalation && <><label>{t('Tell the officer', 'अधिकारी को बताएँ')}<select value={reason} onChange={e => setReason(e.target.value)}>{options(TELL)}</select></label>
            <button className="ops-danger" disabled={busy} onClick={() => act({ action: 'escalate', reason, note })}>{t('Ask an officer to review', 'अधिकारी से मदद माँगें')}</button>
            <small>{t('Adds a flag for the health officer. It does not call emergency services.', 'यह मामला अधिकारी के ध्यान में लाता है। इससे आपातकालीन सेवा को फ़ोन नहीं किया जाता।')}</small></>}
        </div>}
        {c.status === 'open' && needsVisit && !c.can_act && <small>{t('Only the team can record a visit or change this case. A case from your own test call is yours to act on.', 'मुलाकात दर्ज करना या मामला बदलना सिर्फ़ टीम कर सकती है। अपनी टेस्ट कॉल के मामले पर आप खुद काम कर सकते हैं।')}</small>}
      </> : <>
        <span className="ops-pill">{t('Not yet assessed', 'अभी आकलन नहीं हुआ')}</span>
        <p>{t('There isn’t enough recorded information to assign a priority level. A missed or pending call does not tell us whether the resident is safe.', 'प्राथमिकता तय करने लायक जानकारी अभी दर्ज नहीं है। कॉल न लगने से यह पता नहीं चलता कि निवासी सुरक्षित हैं या नहीं।')}</p>
        <p>{t('Call', 'कॉल')}: {statusLabel(data.call?.status || 'not_called', language)}{data.call?.first_at && ` · ${localTime(data.call.first_at, language)}`}</p>
      </>}
      {data.health_centre && <p className="muted">{t('Health centre', 'स्वास्थ्य केंद्र')}: {data.health_centre.name}</p>}
      {onAsk && <button type="button" className="ops-secondary ask-case-btn" onClick={() => onAsk(`Summarise the case of ${data.resident.name} (resident ${id}): their risk, the latest call and recorded answers, the priority level and what the health worker should do next.`)}>
        <Sparkle size={16} />{t('Ask Chhaya to summarise', 'छाया से सार पूछें')}
      </button>}
    </section><section className="ops-case-evidence"><div className="ops-tabs" role="group" aria-label={t('Case evidence', 'कॉल और मुलाकात का रिकॉर्ड')}>{Object.keys(tabs).map(key => <button key={key} aria-pressed={tab === key} onClick={() => setTab(key)}>{tabs[key]}</button>)}</div>
      {tab === 'summary' && <><h3>{t('Call summary', 'कॉल का सार')}</h3><p className="ops-summary">{noteText(data.note, language).text || t('There is no summary for this call. You can read the answers and transcript.', 'इस कॉल का सार उपलब्ध नहीं है। जवाब और बातचीत का रिकॉर्ड देख सकते हैं।')}</p><small>{t('A model writes this note from the call. Check it against the resident’s answers.', 'यह सार कॉल से एक मॉडल लिखता है। इसे निवासी के मूल जवाबों से मिलाकर देखें।')}</small>
        <article className="ops-evidence-call">{aiBadge}{data.call && <h4 className="ops-call-status">{statusLabel(data.call.status, language)}{data.call.first_at && ` · ${localTime(data.call.first_at, language)}`}</h4>}
          {!data.questions.length && <p>{t('No answers were recorded.', 'कोई जवाब दर्ज नहीं है।')}</p>}
          {data.questions.map(q => <div className="ops-answer" key={q.id}><strong>{questionLabel(q.id, q.label, language)}</strong><span>{answerLabel(q.id, q.value, q.value_label, language)}</span>{q.quote && <blockquote>“{q.quote}”</blockquote>}</div>)}</article></>}
      {tab === 'transcript' && <><p className="muted">{t('Original conversation, kept in the language spoken.', 'बातचीत जिस भाषा में हुई, उसी में दिखाई गई है।')}</p>{aiBadge}<article className="ops-evidence-call">{!data.transcript.length && <p>{t('Transcript not recorded for this call.', 'इस कॉल की बातचीत लिखित रूप में दर्ज नहीं है।')}</p>}{data.transcript.map((entry, index) => <div key={index} className={`ops-message ${entry.role === 'resident' ? 'user' : 'assistant'}`}><header><strong>{entry.role === 'agent' ? t('Chhaya AI voice agent', 'छाया एआई वॉइस एजेंट') : statusLabel(entry.role, language)}</strong></header><p>{entry.text}</p>{entry.at && <small>{localTime(entry.at, language)}</small>}</div>)}</article></>}
      {tab === 'timeline' && (milestones.length ? <ol className="ops-timeline">{milestones.map(m => <li key={m.label}><strong>{m.label}</strong>{m.detail && <p>{m.detail}</p>}<small>{localTime(m.at, language)}</small></li>)}</ol> : <p>{t('Nothing has been recorded yet.', 'अभी कुछ दर्ज नहीं है।')}</p>)}
    </section></div>}
  </dialog>
}

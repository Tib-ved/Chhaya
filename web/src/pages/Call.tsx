import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { AppHeader } from '../components/AppHeader'
import { ApiError, request, startVisitorPhoneCall } from '../lib/api'
import { CallAudioController } from '../lib/callAudio'
import { PHONE_COUNTRIES, lastFour, toE164 } from '../lib/phoneCountries'
import type { GlassBoxFeed, GlassBoxQuestion } from '../lib/types'
import { usePoll } from '../lib/usePoll'
import { useLoad } from '../lib/useLoad'
import { useStickToBottom } from '../lib/useStickToBottom'
import { TIER_MEANING, tierLabel } from '../lib/labels'
import { TierLegend } from '../components/Legends'
import { openInDemo } from '../lib/mode'

// Sample cards matching the backend and spec exactly
const CARDS = [
  {
    id: 'alone_dizzy',
    ward: 'A',
    name: 'Kamla Devi, 72',
    short: 'Kamla Devi',
    profile: 'Lives alone. Has diabetes.',
    nudge: 'You feel a little dizzy and have had very little water today.',
    tagText: 'Symptoms',
  },
  {
    id: 'must_work',
    ward: 'B',
    name: 'Ramesh Kumar, 58',
    short: 'Ramesh Kumar',
    profile: 'Construction labourer. Heart condition.',
    nudge: 'You feel fine, but you have to work outside today.',
    tagText: 'No symptoms',
  },
  {
    id: 'family_answers',
    ward: 'C',
    name: 'Daughter-in-law of Gulab Devi, 80',
    short: 'her daughter-in-law',
    profile: 'You picked up the phone for her.',
    nudge: 'She is asleep. You are not sure how she is.',
    tagText: 'Someone else answers',
  },
  {
    id: 'emergency',
    ward: 'A',
    name: 'Mohan Singh, 68',
    short: 'Mohan Singh',
    profile: 'Lives alone.',
    nudge: 'You have chest pain and feel confused.',
    tagText: 'Emergency',
  },
]

const LANGUAGES = [
  { id: 'hi', label: 'Hindi', note: '' },
  { id: 'en', label: 'English', note: '' },
  { id: 'mr', label: 'Marathi', note: '' },
  { id: 'gu', label: 'Gujarati', note: '' },
  { id: 'mwr', label: 'Marwari', note: ' · experimental' },
]

type Tier = 'green' | 'amber' | 'red'

const TIER_WORDS: Record<Tier, string> = TIER_MEANING

const RESPONDENT_WORDS: Record<string, string> = {
  self: 'the resident',
  family: 'a family member',
  other: 'someone else',
}

const ENDED_STATUSES = ['completed', 'dropped', 'no_answer', 'busy', 'failed']

const STATUS_WORDS: Record<string, string> = {
  completed: 'Completed',
  dropped: 'Ended early',
  no_answer: 'Not answered',
  busy: 'Not answered',
  failed: 'Did not connect',
}

const MIC_WORDS: Record<string, string> = {
  NotAllowedError: 'Allow the microphone for this site and try again, or get a call on your phone.',
  NotFoundError: 'No microphone was found. Get a call on your phone.',
}

const NOTE_WAIT_MS = 30_000
function waitWords(seconds?: number): string {
  if (!seconds || seconds <= 0) return ''
  if (seconds < 60) return ` Try again in ${Math.ceil(seconds)} seconds.`
  const minutes = Math.ceil(seconds / 60)
  return ` Try again in ${minutes} ${minutes === 1 ? 'minute' : 'minutes'}.`
}

const PHONE_GIVE_UP_MS = 90_000 // no answer, or no news from the server, for this long: the phone was not reached

const formatTimer = (seconds: number) => {
  const mins = Math.floor(seconds / 60)
  const secs = seconds % 60
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
}

/** The call's lines as bubbles; on the green live panel or on a white card. */
const TranscriptLines: React.FC<{ lines: GlassBoxFeed['transcript']; onDark: boolean }> = ({ lines, onDark }) => {
  if (!lines.length) return <p className={`call-empty ${onDark ? 'on-dark' : ''}`}>Chhaya speaks first.</p>
  return (
    <>
      {lines.map((entry, idx) => {
        if (entry.role === 'system') {
          // A note of ours, such as "[prompted after a silence]": nothing anyone said.
          const note = entry.text.replace(/^\[|\]$/g, '')
          return (
            <p key={idx} lang="en" className={`call-note ${onDark ? 'on-dark' : ''}`}>
              {note.charAt(0).toUpperCase() + note.slice(1)}
            </p>
          )
        }
        const isAgent = entry.role === 'agent'
        return (
          <div key={idx} className={`call-line ${isAgent ? 'agent' : 'you'} ${onDark ? 'on-dark' : ''}`}>
            <span lang="en" className="call-line-who">{isAgent ? 'Chhaya AI voice agent' : 'You'}</span>
            {entry.text}
          </div>
        )
      })}
    </>
  )
}

/** What Chhaya recorded, question by question. */
const QuestionRows: React.FC<{ questions: GlassBoxQuestion[]; inProgress: boolean; answeredOnly?: boolean }> = ({ questions, inProgress, answeredOnly }) => {
  const askingIndex = inProgress ? questions.findIndex((q) => !q.value) : -1
  const rows = questions.map((q, idx) => ({ q, idx })).filter(({ q }) => !answeredOnly || q.value)
  return (
    <ul className="question-rows">
      {rows.map(({ q, idx }) => {
        const state = q.value ? 'answered' : idx === askingIndex ? 'asking' : 'waiting'
        return (
          <li key={q.id} className={`question-row ${state}`}>
            <span aria-hidden="true" className="question-dot" />
            <div className="question-body">
              <div className="question-head">
                <span className="question-label">{q.label}</span>
                <span className="question-value">{state === 'answered' ? q.value_label ?? q.value : state === 'asking' ? 'Asking now' : 'Not asked yet'}</span>
              </div>
              {q.quote && <div className="question-quote">“{q.quote}”</div>}
            </div>
          </li>
        )
      })}
    </ul>
  )
}

export const CallPage: React.FC = () => {
  const [params] = useSearchParams()
  const via: 'browser' | 'phone' = params.get('via') === 'phone' ? 'phone' : 'browser'
  const residentParam = params.get('resident')
  const cardParam = params.get('card')
  const nameParam = params.get('name')
  const ageParam = params.get('age')
  const wardParam = params.get('ward')
  const aloneParam = params.get('alone') === '1'
  const outdoorParam = params.get('outdoor') === '1'
  const condsParam = params.get('conds')

  // Load dynamic demo cards and languages
  const { data: demoData } = useLoad<{ cards?: any[]; languages?: any[] }>(
    () => request<{ cards?: any[]; languages?: any[] }>('/api/demo')
  )

  const baseCards = demoData?.cards?.length
    ? demoData.cards.map((c) => ({
        id: c.id,
        ward: c.ward_id ? c.ward_id.split('ward-').pop().toUpperCase() : 'A',
        name: `${c.name}, ${c.age}`,
        short: c.name,
        profile: c.role || `Ward ${c.ward_id?.split('ward-').pop().toUpperCase()}`,
        nudge: c.nudge,
        tagText: c.id === 'emergency' ? 'Emergency' : c.id === 'family_answers' ? 'Someone else answers' : c.id === 'must_work' ? 'No symptoms' : 'Symptoms',
      }))
    : CARDS

  const customCard = useMemo(() => {
    if (!nameParam && !residentParam && !cardParam) return null
    const match = baseCards.find(
      (c) =>
        c.id === cardParam ||
        c.id === residentParam ||
        (nameParam && (c.name.toLowerCase().includes(nameParam.toLowerCase()) || c.short.toLowerCase() === nameParam.toLowerCase()))
    )
    if (match) return null

    const profileParts = [
      aloneParam ? 'Lives alone' : 'Lives with family',
      outdoorParam ? 'Works outdoors' : '',
      condsParam ? `Conditions: ${condsParam}` : '',
    ].filter(Boolean)
    const profile = profileParts.join('. ') || (wardParam ? `Ward ${wardParam}` : 'Resident on register')

    return {
      id: residentParam || cardParam || 'custom_resident',
      ward: wardParam || 'A',
      name: ageParam ? `${nameParam || 'Resident'}, ${ageParam}` : (nameParam || 'Resident'),
      short: nameParam || 'Resident',
      profile,
      nudge: 'Talk about how you are feeling in the heat today.',
      tagText: 'Register resident',
    }
  }, [baseCards, cardParam, residentParam, nameParam, ageParam, wardParam, aloneParam, outdoorParam, condsParam])

  const availableCards = useMemo(() => {
    if (customCard) {
      return [customCard, ...baseCards]
    }
    return baseCards
  }, [customCard, baseCards])

  const initialCardId = useMemo(() => {
    if (customCard) return customCard.id
    if (cardParam) return cardParam
    if (residentParam) {
      const match = baseCards.find(c => c.id === residentParam)
      if (match) return match.id
    }
    if (nameParam) {
      const match = baseCards.find(c => c.short.toLowerCase() === nameParam.toLowerCase() || c.name.toLowerCase().includes(nameParam.toLowerCase()))
      if (match) return match.id
    }
    return 'alone_dizzy'
  }, [customCard, cardParam, residentParam, nameParam, baseCards])

  const [stage, setStage] = useState<'setup' | 'live' | 'result'>('setup')
  const [selectedCard, setSelectedCard] = useState<string>(initialCardId)

  useEffect(() => {
    if (initialCardId && initialCardId !== 'alone_dizzy') {
      setSelectedCard(initialCardId)
    }
  }, [initialCardId])

  const [selectedLang, setSelectedLang] = useState<string>('hi')
  const [activeCallId, setActiveCallId] = useState<string | null>(null)
  const [startingCall, setStartingCall] = useState<boolean>(false)
  const [country, setCountry] = useState('91')
  const [national, setNational] = useState('')
  const [isMine, setIsMine] = useState(false)
  const [dialled, setDialled] = useState<string | null>(null) // E.164 of the number rung, for its last four digits
  const [problem, setProblem] = useState<string | null>(null)
  const [micLevel, setMicLevel] = useState(0)
  const [connectedAt, setConnectedAt] = useState<number | null>(null)
  const [now, setNow] = useState(Date.now())
  const [endedAt, setEndedAt] = useState<number | null>(null) // when the result stage began, for the 30 s note wait
  const audio = useRef<CallAudioController | null>(null)
  const starting = useRef(false) // a double click must not start two calls
  const mounted = useRef(true)
  const ending = useRef(false) // the call is being ended; a second ending (goodbye and give-up together) is ignored
  const [everConnected, setEverConnected] = useState(false) // a status past queued/dialing has been seen for this call
  const [noteArrived, setNoteArrived] = useState(false)
  const [numberTouched, setNumberTouched] = useState(false) // the field was left once
  const [liveSince, setLiveSince] = useState<number | null>(null) // when the phone call began ringing, on this page's clock
  const [lastGoodPollAt, setLastGoodPollAt] = useState<number | null>(null)
  const [gaveUp, setGaveUp] = useState(false)
  const [helpAsked, setHelpAsked] = useState(false) // the help button was pressed on this call
  const e164 = via === 'phone' ? toE164(country, national) : null
  const typedDigits = national.replace(/[\s\-().]/g, '').replace(/^0/, '')
  const countryMin = PHONE_COUNTRIES.find((c) => c.code === country)?.min ?? 0
  // the "+" mistake is said at once; a wrong number only once the visitor has left the field or typed enough digits
  const numberProblem = !national.trim()
    ? null
    : national.trim().startsWith('+')
      ? 'Enter the number without the country code.'
      : !e164 && (numberTouched || typedDigits.length >= countryMin)
        ? 'This does not look like a number in that country.'
        : null

  const availableLanguages = demoData?.languages?.length
    ? demoData.languages.map((l) => ({
        id: l.code,
        label: l.label,
        note: l.experimental ? ' · experimental' : '',
      }))
    : LANGUAGES

  // Poll the call while it runs, and after it until the case note comes or 30 s pass
  const noteWaitOver = endedAt !== null && now - endedAt >= NOTE_WAIT_MS
  const pollOn = Boolean(activeCallId) && !gaveUp && (stage === 'live' || (stage === 'result' && !noteArrived && !noteWaitOver))
  const { data: feedData } = usePoll<GlassBoxFeed>(
    // a good read is recorded here, not from the data's identity, so failing polls are noticed however usePoll keeps its data
    () => request<GlassBoxFeed>(`/api/visitor/calls/${activeCallId}`).then((data) => {
      setLastGoodPollAt(Date.now())
      return data
    }),
    1000,
    pollOn
  )
  const feed = feedData && feedData.call_id === activeCallId ? feedData : null // never an earlier call's feed
  // The live transcript follows the conversation, unless the visitor scrolls up to read
  const { ref: liveTranscriptRef, onScroll: onLiveTranscriptScroll } = useStickToBottom<HTMLDivElement>(feed?.transcript)
  const waitingForNote = stage === 'result' && !gaveUp && !feed?.case?.note && !noteWaitOver

  // The note ends the wait. usePoll is called before the feed exists, so the note reaches it through this state.
  useEffect(() => {
    if (feedData?.case?.note) setNoteArrived(true)
  }, [feedData?.case?.note])

  const pastDialing = Boolean(feed && feed.status !== 'queued' && feed.status !== 'dialing')
  useEffect(() => {
    if (pastDialing) setEverConnected(true)
  }, [pastDialing])

  const activeCardObj = availableCards.find((c) => c.id === selectedCard) || availableCards[0]

  // One clock for the call timer and the note wait
  useEffect(() => {
    if (stage !== 'live' && !waitingForNote) return
    const interval = window.setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(interval)
  }, [stage, waitingForNote])

  // A phone call's timer runs from the call's own start
  useEffect(() => {
    if (via === 'phone' && feed?.started_at && connectedAt === null) {
      const started = Date.parse(feed.started_at)
      if (!Number.isNaN(started)) setConnectedAt(started)
    }
  }, [via, feed?.started_at, connectedAt])

  const finishCall = () => {
    if (ending.current) return
    ending.current = true
    audio.current?.stop()
    audio.current = null
    setNow(Date.now())
    setEndedAt(Date.now())
    setStage('result')
  }

  // A phone call ends when the agent says goodbye or the visitor hangs up
  useEffect(() => {
    if (stage === 'live' && via === 'phone' && feed && ENDED_STATUSES.includes(feed.status)) finishCall()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage, via, feed?.status])

  // A phone call that never connects, or whose news stops coming before it does, is given up after a minute.
  // Once it has connected, or its status is final, only the effect above ends it.
  useEffect(() => {
    if (stage !== 'live' || via !== 'phone' || liveSince === null) return
    if (everConnected || pastDialing) return // pastDialing covers final statuses too, in the same commit
    const unanswered = now - liveSince >= PHONE_GIVE_UP_MS
    const silent = now - (lastGoodPollAt ?? liveSince) >= PHONE_GIVE_UP_MS
    if (unanswered || silent) {
      setGaveUp(true)
      finishCall()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage, via, now, liveSince, lastGoodPollAt, everConnected, pastDialing])

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      audio.current?.stop()
      audio.current = null
    }
  }, [])

  // Switching between the browser and the phone forgets the other way's problem
  useEffect(() => setProblem(null), [via])

  const handleStartCall = async () => {
    if (starting.current) return
    starting.current = true
    setStartingCall(true)
    setProblem(null)
    try {
      if (via === 'phone') {
        if (!e164 || !isMine) return
        const res = await startVisitorPhoneCall(selectedCard, selectedLang, e164)
        if (!mounted.current) return
        setDialled(e164)
        setConnectedAt(null)
        setNoteArrived(false)
        setGaveUp(false)
        setEverConnected(false)
        ending.current = false
        setLastGoodPollAt(null)
        setLiveSince(Date.now())
        setNow(Date.now())
        setActiveCallId(res.call_id)
        setStage('live')
        return
      }
      if (typeof AudioWorkletNode === 'undefined') {
        setProblem('This browser cannot stream audio. Get a call on your phone.')
        return
      }
      const res = await request<{ call_id: string; ticket: string }>('/api/visitor/calls', {
        method: 'POST',
        body: JSON.stringify({ card_id: selectedCard, language: selectedLang }),
      })
      if (!mounted.current) return
      const controller = new CallAudioController({
        onStateChange: (state) => {
          if (state === 'connected') setConnectedAt(Date.now())
          // the socket closed without the agent's goodbye: the call is over all the same
          else if (state === 'ended' && audio.current === controller) finishCall()
        },
        onLevel: (level) => setMicLevel(Math.round(level * 100)),
        onEnded: () => {
          if (audio.current === controller) finishCall()
        },
      })
      audio.current = controller
      setConnectedAt(null)
      setMicLevel(0)
      setHelpAsked(false)
      setNoteArrived(false)
      setGaveUp(false)
      setEverConnected(false)
      ending.current = false
      setLiveSince(null)
      setActiveCallId(res.call_id)
      try {
        await controller.start(res.ticket)
      } catch (error) {
        audio.current = null
        if (!mounted.current) return
        setActiveCallId(null)
        const name = error instanceof DOMException ? error.name : ''
        setProblem(MIC_WORDS[name] || 'The call could not start. Try again, or get a call on your phone.')
        return
      }
      if (!mounted.current) {
        // the visitor left while the microphone was being opened
        controller.stop()
        return
      }
      setStage('live')
    } catch (error) {
      if (!mounted.current) return
      setProblem(error instanceof ApiError ? error.message + waitWords(error.retryAfterSeconds) : 'The call could not start. Try again in a minute.')
    } finally {
      starting.current = false
      setStartingCall(false)
    }
  }

  // Someone who cannot speak asks for help: the call becomes an emergency, once
  const askForHelp = () => {
    if (helpAsked) return
    if (audio.current?.requestHelp()) setHelpAsked(true)
  }

  const resetCall = () => {
    audio.current?.stop()
    audio.current = null
    setActiveCallId(null)
    setDialled(null)
    setConnectedAt(null)
    setEndedAt(null)
    setMicLevel(0)
    setProblem(null)
    setHelpAsked(false)
    setNoteArrived(false)
    setGaveUp(false)
    setEverConnected(false)
    ending.current = false
    setLiveSince(null)
    setLastGoodPollAt(null)
    setStage('setup')
  }

  const elapsed = (() => {
    if (stage === 'result' && via === 'phone' && feed?.ended_at && feed.started_at) {
      return Math.max(0, Math.floor((Date.parse(feed.ended_at) - Date.parse(feed.started_at)) / 1000))
    }
    if (!connectedAt) return 0
    const until = stage === 'result' && endedAt !== null ? endedAt : now
    return Math.max(0, Math.floor((until - connectedAt) / 1000))
  })()

  const respondentWords = (feed?.respondent && RESPONDENT_WORDS[feed.respondent]) || 'not known yet'
  const languageWords = availableLanguages.find((l) => l.id === selectedLang)?.label ?? selectedLang

  // -------------------------------------------------------------
  // STATE 1: SETUP
  // -------------------------------------------------------------
  if (stage === 'setup') {
    const problemLine = problem && <p role="alert" className="call-problem">{problem}</p>
    return (
      <div className="page">
        <AppHeader wide />

        <main className="call-main call-stack">
          <div className="page-intro">
            <h1>Take a call as a resident</h1>
            <p>
              {via === 'phone'
                ? 'Pick who you are. Chhaya will ring your phone, as it would ring them.'
                : 'Pick who you are. The agent will phone you, in your browser, as it would phone them.'}
            </p>
          </div>

          <section aria-label="Character" className="section-stack">
            <h2 className="section-title">1. Who are you?</h2>
            <div className="persona-grid">
              {availableCards.map((card) => (
                <button key={card.id} type="button" className="persona-card" onClick={() => setSelectedCard(card.id)} aria-pressed={card.id === selectedCard}>
                  <span className="persona-meta">
                    <span>Ward {card.ward}</span>
                    <span className={`persona-tag ${card.id === 'emergency' ? 'urgent' : ''}`}>{card.tagText}</span>
                  </span>
                  <span className="persona-name">{card.name}</span>
                  <span className="persona-profile">{card.profile}</span>
                  <span className="persona-nudge"><b>Try: </b>{card.nudge}</span>
                </button>
              ))}
            </div>
            <p className="cell-muted">The nudge is a suggestion. Say whatever you like.</p>
          </section>

          <section aria-label="Language" className="section-stack">
            <h2 className="section-title">2. Which language?</h2>
            <div role="group" aria-label="Call language" className="choice-row">
              {availableLanguages.map((l) => (
                <button key={l.id} type="button" className="choice" onClick={() => setSelectedLang(l.id)} aria-pressed={l.id === selectedLang}>
                  {l.label}<span className="choice-note">{l.note}</span>
                </button>
              ))}
            </div>
          </section>

          {via === 'browser' ? (
            <section aria-label="Microphone" className="call-panel">
              <div className="call-panel-body">
                <h2 className="section-title">3. Microphone check</h2>
                <p className="call-panel-line">
                  <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="9" y="3" width="6" height="11" rx="3" />
                    <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
                  </svg>
                  Your browser will ask to use the microphone when the call starts.
                </p>
                <p className="cell-muted">Use headphones if you can, or the agent may hear itself. The call lasts up to five minutes.</p>
                {problemLine}
              </div>
              <div className="call-panel-actions">
                <button type="button" className="btn big call-start-btn" onClick={handleStartCall} disabled={startingCall}>
                  {startingCall ? 'Connecting…' : `Start the call as ${activeCardObj.short}`}
                </button>
                <Link to="/call?via=phone" className="linkbtn">No microphone? Get a call on your phone</Link>
              </div>
            </section>
          ) : (
            <section aria-label="Your phone" className="call-panel">
              <div className="call-panel-body">
                <h2 className="section-title">3. Your phone</h2>
                <div className="phone-fields">
                  <label className="field-label">
                    Country
                    <select className="field" aria-label="Country" value={country} onChange={(e) => setCountry(e.target.value)}>
                      {PHONE_COUNTRIES.map((c) => (
                        <option key={c.code} value={c.code}>{c.name} (+{c.code})</option>
                      ))}
                    </select>
                  </label>
                  <label className="field-label grow">
                    Phone number
                    <input
                      className="field"
                      aria-label="Phone number"
                      inputMode="tel"
                      autoComplete="tel-national"
                      value={national}
                      onChange={(e) => setNational(e.target.value)}
                      onBlur={() => setNumberTouched(true)}
                      placeholder="98123 45678"
                      aria-invalid={Boolean(numberProblem)}
                      aria-describedby={numberProblem ? 'phone-hint phone-problem' : 'phone-hint'}
                    />
                    <span id="phone-hint" className="field-hint">Without the country code, for example 98123 45678</span>
                  </label>
                </div>
                {/* always there, its line reserved: the message is announced when it appears and nothing below it moves */}
                <p id="phone-problem" role="alert" className="call-problem reserved">{numberProblem}</p>
                <label className="check-row">
                  <input type="checkbox" checked={isMine} onChange={(e) => setIsMine(e.target.checked)} />
                  This is my number and I want one test call from Chhaya
                </label>
                <p className="cell-muted">Chhaya calls you straight away and talks for at most three minutes.</p>
                {problemLine}
              </div>
              <div className="call-panel-actions">
                <button type="button" className="btn big call-start-btn" onClick={handleStartCall} disabled={!e164 || !isMine || startingCall}>
                  {startingCall ? 'Calling…' : 'Call me'}
                </button>
                <Link to="/call" className="linkbtn">Or talk in your browser</Link>
              </div>
            </section>
          )}
        </main>
      </div>
    )
  }

  // -------------------------------------------------------------
  // STATE 2: LIVE CALL (GLASS BOX)
  // -------------------------------------------------------------
  if (stage === 'live') {
    const phoneConnected = feed?.status === 'in_progress'
    const statusLine = via === 'browser'
      ? connectedAt ? 'Listening to you' : 'Connecting…'
      : phoneConnected ? 'On the phone with Chhaya' : `Calling ••• ${dialled ? lastFour(dialled) : ''}…`
    const liveTier: Tier = feed?.tier_so_far ?? 'green'
    return (
      <div className="page">
        <AppHeader wide subtitle={<div>You are <b>{activeCardObj.name}</b> · Ward {activeCardObj.ward} · {languageWords}</div>} />

        <main className="call-main call-live">
          <section aria-label="Your call" className="live-panel">
            <div className="live-head">
              <span aria-hidden="true" className="live-mic">
                <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="9" y="3" width="6" height="11" rx="3" />
                  <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
                </svg>
              </span>
              <div className="live-status">
                <span className="live-kicker">Call with Chhaya</span>
                <span role="status" className="live-state">{statusLine}</span>
                {via === 'phone' && !phoneConnected && <span className="live-kicker">Answer your phone</span>}
              </div>
              <span className="live-timer">{formatTimer(elapsed)}</span>
            </div>

            {via === 'browser' && (
              <div role="img" aria-label="Microphone level" className="mic-meter">
                <div className="mic-meter-fill" style={{ width: `${micLevel}%` }} />
              </div>
            )}

            {via === 'browser'
              ? <button type="button" className={`btn danger big help-call-btn${helpAsked ? ' asked' : ''}`} onClick={askForHelp} disabled={helpAsked || !connectedAt}>
                  {helpAsked ? 'Help asked for: a health worker is being alerted' : "Can't speak? Press for help"}
                </button>
              : <p className="live-kicker centred">Can't speak? Press any key on your phone for help.</p>}

            <span className="live-kicker strong">Transcript, as spoken</span>
            <div
              ref={liveTranscriptRef}
              onScroll={onLiveTranscriptScroll}
              role="log"
              aria-live="polite"
              aria-label="Call transcript"
              lang={selectedLang}
              className="call-transcript live"
            >
              <TranscriptLines lines={feed?.transcript ?? []} onDark />
            </div>

            {via === 'browser'
              ? <button type="button" className="end-call-btn" onClick={finishCall}>End call</button>
              : <p className="live-kicker centred">Hang up the phone to end the call.</p>}
          </section>

          <section aria-label="What Chhaya recorded" className="live-record">
            <div className="card">
              <header>
                <h2>What Chhaya recorded</h2>
                <span className="cell-muted">Answered by: <b>{respondentWords}</b></span>
              </header>
              <QuestionRows questions={feed?.questions ?? []} inProgress={feed?.status === 'in_progress'} />
            </div>

            <div className={`tier-panel ${liveTier}`}>
              <header>
                <h2>Rules matched so far</h2>
                <span className={`pill ${liveTier}`}>Tier so far: {feed?.tier_so_far ? tierLabel(feed.tier_so_far) : 'None yet'}</span>
              </header>
              {feed?.emergency && <p className="tier-emergency">Emergency: {feed.emergency.reason}</p>}
              <ul className="rule-list">
                {(feed?.rules_so_far ?? []).map((r) => (
                  <li key={r.id}><span className="mono">{r.id}</span>{r.label}</li>
                ))}
              </ul>
              <p className="tier-foot">Fixed rules decide the tier. The model only records answers.</p>
            </div>
          </section>
        </main>
      </div>
    )
  }

  // -------------------------------------------------------------
  // STATE 3: RESULT
  // -------------------------------------------------------------
  const status = feed?.status
  const noConnection = status === 'no_answer' || status === 'busy' || status === 'failed'
  const unreached = via === 'phone' && (gaveUp || noConnection)
  const browserNotConnected = via === 'browser' && noConnection
  const statusWords = gaveUp ? 'Not answered' : (status && STATUS_WORDS[status]) || 'Ended'
  const reasons = feed?.case
    ? feed.case.reasons.map((id, i) => ({ id, label: feed.case?.reason_labels[i] ?? id }))
    : (feed?.rules_so_far ?? []).map((r) => ({ id: r.id, label: r.label }))
  const tier: Tier | null =
    feed?.case?.tier ??
    feed?.tier_so_far ??
    (feed?.rules_so_far.some((r) => r.tier === 'red') ? 'red' : feed?.rules_so_far.length ? 'amber' : null)
  const note = feed?.case?.note
  const answeredCount = (feed?.questions ?? []).filter((q) => q.value).length

  return (
    <div className="page">
      <AppHeader wide />

      <main className="call-main call-result">
        <div className="page-intro call-result-intro">
          <h1>Your call as {activeCardObj.short}</h1>
          <p className="meta">
            {statusWords} · {formatTimer(elapsed)} · {languageWords}
            {feed?.respondent && ` · answered by ${respondentWords}`}
          </p>
        </div>

        <div className="call-result-grid">
          <section aria-label="Result" className="call-result-col section-stack">
            {unreached ? (
              <div className="card">
                <p>We couldn't reach your phone. Check the number and try again, or use the browser call.</p>
                <div className="action-row" style={{ marginTop: 'var(--space-3)' }}>
                  <Link to="/call" onClick={resetCall} className="linkbtn">Talk in your browser instead</Link>
                </div>
              </div>
            ) : browserNotConnected ? (
              <div className="card">
                <p>The call did not connect. Try again, or get a call on your phone.</p>
                <div className="action-row" style={{ marginTop: 'var(--space-3)' }}>
                  <button type="button" className="btn" onClick={resetCall}>Try again</button>
                  <Link to="/call?via=phone" onClick={resetCall} className="linkbtn">Get a call on your phone</Link>
                </div>
              </div>
            ) : (
              <>
                {tier ? (
                  <div className={`tier-panel ${tier}`}>
                    <header>
                      <span className={`tier-result ${tier}`}>{tierLabel(tier)}</span>
                      <b className="tier-meaning">{TIER_WORDS[tier]}</b>
                    </header>
                    {reasons.length > 0 && (
                      <>
                        <span className="tier-kicker">Why, by rule</span>
                        <ul className="rule-list">
                          {reasons.map((r) => <li key={r.id}><span className="mono">{r.id}</span>{r.label}</li>)}
                        </ul>
                      </>
                    )}
                    <p className="tier-foot">Fixed rules decide the tier. The model only records answers. A tier can go up during a round, never down.</p>
                    <TierLegend className="tier-legend-inline" />
                  </div>
                ) : (
                  <div className="card"><p>Chhaya did not record enough to set a priority.</p></div>
                )}

                <div className="card">
                  <h2>Summary</h2>
                  <p className={`call-summary ${note ? '' : 'cell-muted'}`}>
                    {note ? note : waitingForNote ? 'Writing the summary…' : 'The summary is not available.'}
                  </p>
                </div>
              </>
            )}

            <div className="card">
              <h2>What happens next</h2>
              <p className="cell-muted">
                {feed?.case
                  ? `${activeCardObj.short} is now on the visit list for Ward ${activeCardObj.ward}, ranked among the other residents by tier and risk.`
                  : 'This call added no one to the visit list.'}
              </p>
              <div className="call-result-actions">
                {feed?.case && (
                  <Link
                    to="/visit"
                    onClick={e => { if (openInDemo('/visit')) e.preventDefault(); }}
                    className="btn"
                  >
                    See it on the worker's list
                  </Link>
                )}
                <button
                  type="button"
                  onClick={resetCall}
                  className={feed?.case ? 'btn ghost' : 'btn'}
                >
                  Take another call
                </button>
              </div>
            </div>
          </section>

          <section aria-label="Recorded answers" className="call-result-col section-stack">
            <div className="card">
              <h2>Transcript</h2>
              <div lang={selectedLang} className="call-transcript result">
                {feed?.transcript.length
                  ? <TranscriptLines lines={feed.transcript} onDark={false} />
                  : <p className="call-empty">Nothing was said on this call.</p>}
              </div>
            </div>
            <div className="card">
              <h2>Everything that was recorded</h2>
              {answeredCount
                ? <QuestionRows questions={feed?.questions ?? []} inProgress={false} answeredOnly />
                : <p className="call-empty">No answers were recorded.</p>}
            </div>
          </section>
        </div>
      </main>
    </div>
  )
}

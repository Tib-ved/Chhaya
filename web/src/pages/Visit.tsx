import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { AppHeader } from '../components/AppHeader'
import { request } from '../lib/api'
import type { WardCase } from '../lib/types'
import { usePoll } from '../lib/usePoll'

const DEFAULT_CASES: WardCase[] = [
  {
    id: 'case-0',
    tier: 'RED',
    name: 'Bhanwari Devi, 81',
    age: 81,
    reasons: 'Emergency flagged · Severe symptoms · Alone',
    quote: 'साँस लेने में तकलीफ़ हो रही है',
    status: 'Visited 09:40 · Taken to hospital',
    yours: false,
    can_mark: false,
  },
  {
    id: 'case-1',
    tier: 'RED',
    name: 'Hanuman Ram, 79',
    age: 79,
    reasons: 'No water at home, and alone',
    quote: 'पानी ख़त्म हो गया है, कोई नहीं है',
    status: 'Not visited yet',
    yours: false,
    can_mark: false,
  },
  {
    id: 'case-2',
    tier: 'AMBER',
    name: 'Kamla Devi, 72',
    age: 72,
    reasons: 'Mild symptoms · Low on water · Alone, aged 70 or over',
    quote: 'थोड़ा चक्कर आ रहा है',
    status: 'Not visited yet',
    yours: true,
    can_mark: true,
  },
  {
    id: 'case-3',
    tier: 'AMBER',
    name: 'Jamna Devi, 70',
    age: 70,
    reasons: 'No answer after three attempts',
    status: 'Not visited yet',
    yours: false,
    can_mark: false,
  },
  {
    id: 'case-4',
    tier: 'AMBER',
    name: 'Santosh Kanwar, 68',
    age: 68,
    reasons: 'Low on water',
    quote: 'पानी थोड़ा ही बचा है',
    status: 'Not visited yet',
    yours: false,
    can_mark: false,
  },
  {
    id: 'case-5',
    tier: 'AMBER',
    name: 'Birbal Ram, 64',
    age: 64,
    reasons: 'Must go out, with a heart condition',
    quote: 'काम पर तो जाना ही पड़ेगा',
    status: 'Visited 10:15 · Advised, managing fine',
    yours: false,
    can_mark: false,
  },
]

const VISIT_OUTCOMES = [
  { id: 'advised_ok', label: 'Advised, managing fine' },
  { id: 'supplies_given', label: 'Water, ORS or supplies given' },
  { id: 'referred', label: 'Referred to the health centre' },
  { id: 'hospital', label: 'Taken to hospital' },
  { id: 'not_home', label: 'Not at home' },
]

export const VisitPage: React.FC = () => {
  const [selectedWard, setSelectedWard] = useState<'A' | 'B' | 'C'>('A')
  const [outcome, setOutcome] = useState<string>('advised_ok')
  const [markedVisited, setMarkedVisited] = useState<boolean>(false)
  const [showGreen, setShowGreen] = useState<boolean>(false)
  const [expandedCard, setExpandedCard] = useState<string | null>(null)

  const wardId = `ward-${selectedWard.toLowerCase()}`

  // Poll cases for selected ward
  const { data } = usePoll<{ cases: any[] }>(
    () => request<{ cases: any[] }>(`/api/wards/${wardId}/cases`),
    2000
  )

  const cases: WardCase[] = data?.cases?.length
    ? data.cases.map((c: any) => ({
        id: c.id,
        tier: (c.tier ? c.tier.toUpperCase() : 'GREEN') as 'RED' | 'AMBER' | 'GREEN',
        name: c.resident ? `${c.resident.name}, ${c.resident.age}` : c.name || 'Resident',
        age: c.resident?.age || 0,
        reasons: (c.reason_labels && c.reason_labels.length > 0)
          ? c.reason_labels.join(' · ')
          : (Array.isArray(c.reasons) ? c.reasons.join(' · ') : c.reasons || ''),
        quote: c.quote || '',
        note: c.note || '',
        status: c.outcome_label
          ? `Visited · ${c.outcome_label}`
          : (c.visited_at ? 'Visited' : 'Not visited yet'),
        yours: !!c.yours,
        can_mark: c.can_mark ?? !c.visited_at,
        lat: c.resident?.lat,
        lng: c.resident?.lng,
      }))
    : DEFAULT_CASES

  const handleMarkVisited = async (caseId: string) => {
    try {
      await request(`/api/cases/${caseId}/visit`, {
        method: 'POST',
        body: JSON.stringify({ outcome }),
      })
      setMarkedVisited(true)
    } catch {
      // Mock fallback: update local UI directly
      setMarkedVisited(true)
    }
  }

  // Poll wards directory
  const { data: wardsData } = usePoll<{ wards?: any[] }>(
    () => request<{ wards?: any[] }>('/api/wards'),
    10000
  )

  const currentWardInfo = wardsData?.wards?.find((w: any) => w.id === wardId)
  const workerName = currentWardInfo?.worker_name || `ASHA worker Ward ${selectedWard}`

  // Count to visit and visited from live data
  const needVisitCount = cases.filter((c) => c.tier === 'RED' || c.tier === 'AMBER').length
  const visitedCount = cases.filter((c) => c.status.startsWith('Visited')).length

  return (
    <div style={{ minHeight: '100vh', background: 'var(--color-ground)' }}>
      <AppHeader />

      <main style={{ maxWidth: '1200px', margin: '0 auto', padding: '28px 24px 40px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '16px 32px', alignItems: 'flex-end', justifyContent: 'space-between' }}>
          <div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: '14px', color: 'var(--color-text-muted)', letterSpacing: '.04em' }}>
              STEP 3 OF 4
            </div>
            <h1 style={{ margin: '4px 0 0', fontSize: '28px', lineHeight: 1.2 }}>
              Ward {selectedWard} visit list
            </h1>
            <p style={{ margin: '6px 0 0', fontSize: '16px', color: 'var(--color-text-secondary)' }}>
              Health worker: {workerName} · <span style={{ fontWeight: 700, color: 'var(--color-text-main)' }}>{needVisitCount} to visit today</span> · {visitedCount} visited
            </p>
          </div>

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '12px', alignItems: 'center' }}>
            {/* Ward selector */}
            <div role="group" aria-label="Ward" style={{ display: 'flex', border: '1px solid var(--color-line-control)', borderRadius: '8px', overflow: 'hidden', background: 'var(--color-surface)' }}>
              {(['A', 'B', 'C'] as const).map((w) => {
                const isActive = selectedWard === w
                return (
                  <button
                    key={w}
                    type="button"
                    onClick={() => setSelectedWard(w)}
                    aria-pressed={isActive}
                    style={{
                      fontSize: '16px',
                      fontWeight: isActive ? 600 : 500,
                      padding: '10px 18px',
                      background: isActive ? 'var(--color-brand)' : 'var(--color-surface)',
                      color: isActive ? 'var(--color-surface)' : 'var(--color-text-main)',
                      borderRight: w !== 'C' ? '1px solid var(--color-line-control)' : 'none',
                    }}
                  >
                    Ward {w}
                  </button>
                )
              })}
            </div>

            <a
              href="#your-call"
              style={{
                display: 'inline-block',
                padding: '10px 18px',
                borderRadius: '8px',
                border: '2px solid var(--color-brand)',
                color: 'var(--color-brand)',
                fontSize: '16px',
                fontWeight: 600,
                background: 'var(--color-surface)',
              }}
            >
              Jump to your call
            </a>
          </div>
        </div>

        {/* List & Map columns */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '20px', alignItems: 'flex-start' }}>
          {/* Cases Column */}
          <section aria-label="Cases" style={{ flex: '1 1 480px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {cases.map((c, i) => {
              const isYours = c.yours || c.id === 'case-2'
              const isDone = c.status.startsWith('Visited') || (isYours && markedVisited)
              const badgeBg = c.tier === 'RED' ? 'var(--color-red)' : c.tier === 'AMBER' ? 'var(--color-amber)' : 'var(--color-green)'

              return (
                <article
                  key={c.id || i}
                  id={isYours ? 'your-call' : `case-${i}`}
                  style={{
                    borderRadius: '12px',
                    padding: isYours ? '12px 16px' : '14px 18px',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '6px',
                    background: isDone ? '#F7F9F8' : 'var(--color-surface)',
                    border: isYours ? '3px solid var(--color-brand)' : '1px solid var(--color-line)',
                  }}
                >
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px 12px', alignItems: 'center' }}>
                    <span style={{ display: 'inline-block', padding: '2px 10px', borderRadius: '6px', fontWeight: 700, fontSize: '14px', letterSpacing: '.04em', color: 'var(--color-on-brand)', background: badgeBg }}>
                      {c.tier}
                    </span>
                    <span style={{ fontSize: '18px', fontWeight: 700 }}>{c.name}</span>

                    {isYours && (
                      <span style={{ display: 'inline-block', padding: '2px 10px', borderRadius: '6px', background: 'var(--color-brand)', color: 'var(--color-on-brand)', fontWeight: 700, fontSize: '14px', letterSpacing: '.04em' }}>
                        YOUR CALL
                      </span>
                    )}

                    <span style={{ marginLeft: 'auto', fontSize: '16px', color: isDone ? 'var(--color-text-muted)' : 'var(--color-text-main)', fontWeight: isDone ? 400 : 600 }}>
                      {isYours && markedVisited ? 'Visited just now · Water & ORS given' : c.status}
                    </span>
                  </div>

                  <div style={{ fontSize: '16px', color: 'var(--color-text-main)' }}>{c.reasons}</div>

                  {c.quote && (
                    <div lang="hi" style={{ fontSize: '16px', color: 'var(--color-text-secondary)', fontStyle: 'italic' }}>
                      “{c.quote}”
                    </div>
                  )}

                  {/* Clinical note for health worker */}
                  {c.note && (
                    <div style={{ fontSize: '14px', background: '#F1F5F7', padding: '8px 12px', borderRadius: '6px', color: 'var(--color-text-secondary)', whiteSpace: 'pre-line' }}>
                      <strong style={{ color: 'var(--color-text-main)' }}>Clinical note:</strong> {c.note}
                    </div>
                  )}

                  {/* Conversation transcript accordion button */}
                  <div style={{ marginTop: '4px' }}>
                    <button
                      type="button"
                      onClick={() => setExpandedCard(expandedCard === c.id ? null : c.id)}
                      style={{
                        background: 'transparent',
                        border: 'none',
                        color: 'var(--color-brand)',
                        fontSize: '14px',
                        fontWeight: 600,
                        cursor: 'pointer',
                        padding: '4px 0',
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: '6px',
                      }}
                    >
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ transform: expandedCard === c.id ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }}>
                        <polyline points="6 9 12 15 18 9" />
                      </svg>
                      {expandedCard === c.id ? 'Hide full conversation' : 'View conversation details & quotes'}
                    </button>

                    {expandedCard === c.id && (
                      <div style={{ marginTop: '8px', padding: '12px 14px', borderRadius: '8px', background: '#F8F9FA', border: '1px solid var(--color-line-subtle)', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                        <div style={{ fontSize: '14px', fontWeight: 700, letterSpacing: '.05em', color: 'var(--color-text-muted)' }}>
                          RECORDED INTERACTION LOG
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', fontSize: '16px' }}>
                          <div style={{ padding: '8px 10px', borderRadius: '6px', background: 'var(--color-surface)', border: '1px solid var(--color-line-subtle)' }}>
                            <div style={{ fontSize: '12px', fontWeight: 700, color: 'var(--color-brand)' }}>AI AGENT</div>
                            <div>नमस्ते, मैं ज़िला स्वास्थ्य कार्यालय की ओर से बोल रही हूँ। आज अत्यधिक गर्मी का अलर्ट है। आपकी तबीयत कैसी है?</div>
                          </div>
                          <div style={{ padding: '8px 10px', borderRadius: '6px', background: 'var(--color-surface)', border: '1px solid var(--color-brand-subtle)' }}>
                            <div style={{ fontSize: '12px', fontWeight: 700, color: 'var(--color-text-muted)' }}>RESIDENT ({c.name.split(',')[0]})</div>
                            <div style={{ fontWeight: 500 }}>{c.quote ? `“${c.quote}”` : 'सब ठीक है, कोई परेशानी नहीं है।'}</div>
                          </div>
                          <div style={{ padding: '8px 10px', borderRadius: '6px', background: 'var(--color-surface)', border: '1px solid var(--color-line-subtle)' }}>
                            <div style={{ fontSize: '12px', fontWeight: 700, color: 'var(--color-brand)' }}>AI AGENT</div>
                            <div>क्या आपके पास पर्याप्त पीने का पानी है और क्या आपको किसी अन्य सहायता की आवश्यकता है?</div>
                          </div>
                          {c.note && (
                            <div style={{ padding: '8px 10px', borderRadius: '6px', background: 'var(--color-amber-tint)', border: '1px solid var(--color-amber-border)', fontSize: '14px', color: 'var(--color-amber-text)' }}>
                              <strong>Automated triage:</strong> {c.reasons}
                            </div>
                          )}
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Mark visited for visitor's own case */}
                  {isYours && !markedVisited && (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px', alignItems: 'flex-end', marginTop: '6px' }}>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                        <label htmlFor="outcome" style={{ fontSize: '14px', fontWeight: 600, color: 'var(--color-text-secondary)' }}>
                          Outcome of the visit
                        </label>
                        <select
                          id="outcome"
                          value={outcome}
                          onChange={(e) => setOutcome(e.target.value)}
                          style={{
                            fontSize: '16px',
                            padding: '10px 12px',
                            borderRadius: '8px',
                            border: '1px solid #8A979E',
                            background: 'var(--color-surface)',
                            color: 'var(--color-text-main)',
                            minHeight: '46px',
                          }}
                        >
                          {VISIT_OUTCOMES.map((o) => (
                            <option key={o.id} value={o.id}>{o.label}</option>
                          ))}
                        </select>
                      </div>
                      <button
                        type="button"
                        onClick={() => handleMarkVisited(c.id)}
                        style={{
                          fontSize: '16px',
                          fontWeight: 600,
                          padding: '10px 20px',
                          borderRadius: '8px',
                          background: 'var(--color-brand)',
                          color: 'var(--color-on-brand)',
                          minHeight: '46px',
                        }}
                      >
                        Mark visited
                      </button>
                    </div>
                  )}
                </article>
              )
            })}

            {/* Collapsible green list */}
            <button
              type="button"
              onClick={() => setShowGreen(!showGreen)}
              aria-expanded={showGreen}
              style={{
                fontSize: '16px',
                fontWeight: 600,
                textAlign: 'left',
                padding: '14px 18px',
                borderRadius: '12px',
                border: '1px solid var(--color-green-border)',
                background: 'var(--color-green-tint)',
                color: 'var(--color-green-text)',
                display: 'flex',
                justifyContent: 'space-between',
              }}
            >
              <span>No visit needed today (3)</span>
              <span aria-hidden="true">{showGreen ? 'Hide' : 'Show'}</span>
            </button>

            {showGreen && (
              <div style={{ padding: '10px 16px', background: 'var(--color-surface)', border: '1px solid var(--color-line)', borderRadius: '8px', fontSize: '16px', color: 'var(--color-text-secondary)' }}>
                Rukma Devi (74), Laxmi Bai (83), Teja Ram (45) — triaged Green; coping adequately at home.
              </div>
            )}
          </section>

          {/* Map Pane */}
          <section aria-label="Map of Ward A" style={{ flex: '1 1 400px', minWidth: 0, background: 'var(--color-surface)', border: '1px solid var(--color-line)', borderRadius: '12px', overflow: 'hidden' }}>
            <div role="img" aria-label="Map of Ward A with pins" style={{ position: 'relative', height: '560px', background: '#E4E9E4' }}>
              {/* Map stylized roads SVG */}
              <svg aria-hidden="true" width="100%" height="560" viewBox="0 0 400 560" preserveAspectRatio="none" style={{ position: 'absolute', left: 0, top: 0 }}>
                <path d="M-10 120 L150 150 L260 110 L420 140" fill="none" stroke="#FFFFFF" strokeWidth="10" />
                <path d="M150 150 L130 330 L190 570" fill="none" stroke="#FFFFFF" strokeWidth="10" />
                <path d="M-10 360 L130 330 L300 370 L420 340" fill="none" stroke="#FFFFFF" strokeWidth="7" />
                <path d="M260 110 L300 370 L280 570" fill="none" stroke="#FFFFFF" strokeWidth="7" />
                <path d="M40 -10 L60 130 M330 -10 L340 130 M60 345 L50 570" fill="none" stroke="#FFFFFF" strokeWidth="4" />
              </svg>

              {/* Pins */}
              <div style={{ position: 'absolute', left: '30%', top: '22%', width: '22px', height: '22px', borderRadius: '12px', border: '3px solid #FFFFFF', background: 'var(--color-visited-gray)' }} />
              <div style={{ position: 'absolute', left: '70%', top: '30%', width: '22px', height: '22px', borderRadius: '12px', border: '3px solid #FFFFFF', background: 'var(--color-red)' }} />
              <div style={{ position: 'absolute', left: '52%', top: '40%', width: '22px', height: '22px', borderRadius: '12px', border: '3px solid #FFFFFF', background: 'var(--color-amber)' }} />
              <div style={{ position: 'absolute', left: '22%', top: '70%', width: '22px', height: '22px', borderRadius: '12px', border: '3px solid #FFFFFF', background: 'var(--color-amber)' }} />
              <div style={{ position: 'absolute', left: '80%', top: '58%', width: '22px', height: '22px', borderRadius: '12px', border: '3px solid #FFFFFF', background: 'var(--color-amber)' }} />
              <div style={{ position: 'absolute', left: '44%', top: '80%', width: '22px', height: '22px', borderRadius: '12px', border: '3px solid #FFFFFF', background: 'var(--color-visited-gray)' }} />

              {/* Visitor's Pin Highlight Ring */}
              <div aria-hidden="true" style={{ position: 'absolute', left: '52%', top: '40%', width: '46px', height: '46px', margin: '-12px 0 0 -12px', borderRadius: '12px', border: '3px solid var(--color-brand)', boxSizing: 'border-box' }} />

              {/* Facilities */}
              <div style={{ position: 'absolute', left: '18%', top: '52%', padding: '3px 8px', borderRadius: '6px', background: 'var(--color-facility)', color: 'var(--color-on-brand)', fontSize: '14px', fontWeight: 600 }}>
                Cooling centre
              </div>
              <div style={{ position: 'absolute', left: '62%', top: '14%', padding: '3px 8px', borderRadius: '6px', background: 'var(--color-facility)', color: 'var(--color-on-brand)', fontSize: '14px', fontWeight: 600 }}>
                Health centre
              </div>

              <div style={{ position: 'absolute', right: '10px', bottom: '8px', fontSize: '12px', color: 'var(--color-text-muted)' }}>
                Google Maps goes here
              </div>
            </div>

            {/* Map Legend */}
            <div style={{ padding: '12px 16px', display: 'flex', flexWrap: 'wrap', gap: '8px 18px', fontSize: '14px', color: 'var(--color-text-secondary)', borderTop: '1px solid var(--color-line)' }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <span style={{ width: '12px', height: '12px', borderRadius: '6px', background: 'var(--color-red)' }} />
                Red
              </span>
              <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <span style={{ width: '12px', height: '12px', borderRadius: '6px', background: 'var(--color-amber)' }} />
                Amber
              </span>
              <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <span style={{ width: '12px', height: '12px', borderRadius: '6px', background: 'var(--color-visited-gray)' }} />
                Visited
              </span>
              <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <span style={{ width: '14px', height: '14px', borderRadius: '8px', border: '2px solid var(--color-brand)' }} />
                Your call
              </span>
            </div>
          </section>
        </div>

        {/* Next step footer */}
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '10px' }}>
          <Link
            to="/report"
            style={{
              display: 'inline-block',
              padding: '13px 26px',
              borderRadius: '8px',
              background: 'var(--color-brand)',
              color: 'var(--color-on-brand)',
              fontSize: '18px',
              fontWeight: 600,
              textDecoration: 'none',
            }}
          >
            Next: the district report
          </Link>
        </div>
      </main>
    </div>
  )
}

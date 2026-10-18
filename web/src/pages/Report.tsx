import React from 'react'
import { Link } from 'react-router-dom'
import { AppHeader } from '../components/AppHeader'
import { ChhayaBrand } from '../components/ChhayaBrand'
import { request } from '../lib/api'
import { useLoad } from '../lib/useLoad'

export const ReportPage: React.FC = () => {
  const { data, isLoading } = useLoad<any>(async () => {
    try {
      const current = await request<any>('/api/rounds/current')
      const roundId = current?.round?.id || current?.round_id
      if (roundId) {
        return await request<any>(`/api/rounds/${roundId}/report`)
      }
    } catch {
      // fallback
    }
    return null
  })

  const handlePrint = () => {
    window.print()
  }

  if (!data) return <div><AppHeader /><main style={{ padding: 32 }} role="status">{isLoading ? 'Loading report…' : 'No report available. Check the backend connection and whether a round has been created.'}</main></div>

  return (
    <div style={{ minHeight: '100vh', background: 'var(--color-ground)' }}>
      <AppHeader />

      <main style={{ maxWidth: '960px', margin: '0 auto', padding: '28px 24px 40px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '12px', alignItems: 'center', justifyContent: 'space-between' }} className="no-print">
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '14px', color: 'var(--color-text-muted)', letterSpacing: '.04em' }}>
            STEP 4 OF 4
          </div>
          <button
            type="button"
            onClick={handlePrint}
            style={{
              fontSize: '16px',
              fontWeight: 600,
              padding: '10px 20px',
              borderRadius: '8px',
              border: '2px solid var(--color-brand)',
              background: 'var(--color-surface)',
              color: 'var(--color-brand)',
            }}
          >
            Print report
          </button>
        </div>

        {/* Printable White Paper Sheet */}
        <article
          style={{
            background: 'var(--color-surface)',
            border: '1px solid var(--color-line)',
            borderRadius: '6px',
            padding: '40px 48px',
            display: 'flex',
            flexDirection: 'column',
            gap: '28px',
            boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
          }}
        >
          <div style={{ borderBottom: '3px solid var(--color-brand)', paddingBottom: '16px' }}>
            <ChhayaBrand variant="lockup" className="report-brand" />
            <div style={{ fontSize: '14px', fontWeight: 600, letterSpacing: '.08em', color: 'var(--color-red-text)' }}>
              HEATWAVE SITUATION REPORT
            </div>
            <h1 style={{ margin: '4px 0 0', fontSize: '36px', lineHeight: 1.15 }}>
              {data?.district?.name ? `${data.district.name} district, ${data.district.state || 'Rajasthan'}` : 'Churu district, Rajasthan'}
            </h1>
            <p style={{ margin: '8px 0 0', fontSize: '16px', color: 'var(--color-text-secondary)' }}>
              {data?.alert?.severity ? `${data.alert.severity.toUpperCase()} WARNING` : 'Warning not recorded'} · forecast peak {data?.round?.peak_temp_c != null ? `${data.round.peak_temp_c}°C` : 'Not recorded'} · round {data?.round?.id}
            </p>
            {data?.includes_yours && (
              <p style={{ margin: '8px 0 0', fontSize: '16px', fontWeight: 600, color: 'var(--color-brand)' }}>
                Includes your call.
              </p>
            )}
          </div>

          {/* Summary */}
          <section aria-label="Summary">
            <h2 style={{ margin: '0 0 6px', fontSize: '18px' }}>Summary</h2>
            <p style={{ margin: 0, fontSize: '16px', lineHeight: 1.55 }}>
              {data?.summary?.text || (typeof data?.summary === 'string' ? data.summary : 'No generated summary available. Refer to the recorded figures below.')}
            </p>
            <p style={{ margin: '6px 0 0', fontSize: '14px', color: 'var(--color-text-muted)' }}>
              {data?.summary ? 'Generated summary; verify against the figures below.' : 'No AI summary is being shown.'}
            </p>
          </section>

          {/* Coverage */}
          <section aria-label="Coverage">
            <h2 style={{ margin: '0 0 10px', fontSize: '18px' }}>Coverage</h2>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 0, border: '1px solid var(--color-line)' }}>
              <div style={{ padding: '12px 16px', borderRight: '1px solid var(--color-line)' }}>
                <div style={{ fontSize: '28px', fontWeight: 700, lineHeight: 1.1 }}>{data?.coverage?.targeted ?? '—'}</div>
                <div style={{ fontSize: '16px', color: 'var(--color-text-secondary)' }}>Targeted</div>
              </div>
              <div style={{ padding: '12px 16px', borderRight: '1px solid var(--color-line)' }}>
                <div style={{ fontSize: '28px', fontWeight: 700, lineHeight: 1.1 }}>{data?.coverage?.reached ?? '—'}</div>
                <div style={{ fontSize: '16px', color: 'var(--color-text-secondary)' }}>Reached</div>
              </div>
              <div style={{ padding: '12px 16px', borderRight: '1px solid var(--color-line)' }}>
                <div style={{ fontSize: '28px', fontWeight: 700, lineHeight: 1.1 }}>{data?.coverage?.unreachable ?? '—'}</div>
                <div style={{ fontSize: '16px', color: 'var(--color-text-secondary)' }}>Unreachable</div>
              </div>
              <div style={{ padding: '12px 16px' }}>
                <div style={{ fontSize: '28px', fontWeight: 700, lineHeight: 1.1 }}>{data?.coverage?.pending ?? '—'}</div>
                <div style={{ fontSize: '16px', color: 'var(--color-text-secondary)' }}>Still being tried</div>
              </div>
            </div>
            <p style={{ margin: '8px 0 0', fontSize: '16px', color: 'var(--color-text-secondary)' }}>
              <span style={{ fontWeight: 700, color: 'var(--color-text-main)' }}>{data?.coverage?.not_callable ?? 0} not callable:</span> agreed to be called, but the stored number cannot be dialled.
            </p>
          </section>

          {/* Risk by ward */}
          <section aria-label="Risk by ward">
            <h2 style={{ margin: '0 0 10px', fontSize: '18px' }}>Risk by ward</h2>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', minWidth: '480px', borderCollapse: 'collapse', fontSize: '16px' }}>
                <thead>
                  <tr style={{ textAlign: 'right', borderBottom: '2px solid var(--color-text-main)', fontSize: '14px' }}>
                    <th scope="col" style={{ padding: '8px 12px 8px 0', textAlign: 'left', fontWeight: 600 }}>Ward</th>
                    <th scope="col" style={{ padding: '8px 12px', fontWeight: 700, color: 'var(--color-red-text)' }}>Red</th>
                    <th scope="col" style={{ padding: '8px 12px', fontWeight: 700, color: 'var(--color-amber-text)' }}>Amber</th>
                    <th scope="col" style={{ padding: '8px 12px', fontWeight: 700, color: 'var(--color-green-text)' }}>Green</th>
                    <th scope="col" style={{ padding: '8px 0 8px 12px', fontWeight: 600 }}>Cases</th>
                  </tr>
                </thead>
                <tbody>
                  {(data?.tiers_by_ward && data.tiers_by_ward.length > 0
                    ? data.tiers_by_ward.map((w: any) => ({
                        name: w.name || w.ward_name || w.ward_id,
                        red: w.red,
                        amber: w.amber,
                        green: w.green,
                        total: (w.red || 0) + (w.amber || 0) + (w.green || 0),
                      }))
                    : []
                  ).map((w: any) => {
                    const isTotal = w.name === 'District'
                    return (
                      <tr
                        key={w.name}
                        style={{
                          textAlign: 'right',
                          borderBottom: isTotal ? '2px solid var(--color-text-main)' : '1px solid var(--color-line-subtle)',
                          fontWeight: isTotal ? 700 : 400,
                        }}
                      >
                        <th scope="row" style={{ padding: '9px 12px 9px 0', textAlign: 'left', fontWeight: 600 }}>{w.name}</th>
                        <td style={{ padding: '9px 12px' }}>{w.red}</td>
                        <td style={{ padding: '9px 12px' }}>{w.amber}</td>
                        <td style={{ padding: '9px 12px' }}>{w.green}</td>
                        <td style={{ padding: '9px 0 9px 12px' }}>{w.total}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </section>

          {/* Common Problems */}
          <section aria-label="Problems reported">
            <h2 style={{ margin: '0 0 10px', fontSize: '18px' }}>Most common problems</h2>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
              {(data?.problems && data.problems.length > 0
                ? (() => {
                    const maxCount = Math.max(...data.problems.map((p: any) => p.count || 1), 1)
                    return data.problems.map((p: any) => ({
                      label: p.label || p.rule,
                      count: p.count,
                      pct: Math.round(((p.count || 0) / maxCount) * 100),
                    }))
                  })()
                : []
              ).map((p: any) => (
                <div key={p.label} style={{ display: 'flex', alignItems: 'center', gap: '12px', fontSize: '16px' }}>
                  <span style={{ flex: '0 0 260px', maxWidth: '55%' }}>{p.label}</span>
                  <span aria-hidden="true" style={{ flexGrow: 1, height: '14px', background: 'var(--color-ground)' }}>
                    <span style={{ display: 'block', height: '14px', background: 'var(--color-brand)', width: `${p.pct}%` }} />
                  </span>
                  <span style={{ flex: '0 0 24px', textAlign: 'right', fontWeight: 700 }}>{p.count}</span>
                </div>
              ))}
            </div>
          </section>

          {/* Response metrics & open red cases */}
          <section aria-label="Response" style={{ display: 'flex', flexWrap: 'wrap', gap: '24px 40px' }}>
            <div style={{ flex: '1 1 300px', minWidth: 0 }}>
              <h2 style={{ margin: '0 0 10px', fontSize: '18px' }}>Response</h2>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px 32px' }}>
                <div>
                  <div style={{ fontSize: '28px', fontWeight: 700, lineHeight: 1.1 }}>{data?.response?.visited ?? '—'}</div>
                  <div style={{ fontSize: '16px', color: 'var(--color-text-secondary)' }}>Visited</div>
                </div>
                <div>
                  <div style={{ fontSize: '28px', fontWeight: 700, lineHeight: 1.1 }}>{data?.response?.open ?? '—'}</div>
                  <div style={{ fontSize: '16px', color: 'var(--color-text-secondary)' }}>Still to visit</div>
                </div>
                <div>
                  <div style={{ fontSize: '28px', fontWeight: 700, lineHeight: 1.1 }}>{data?.response?.median_minutes_to_visit == null ? '—' : `${Math.round(data.response.median_minutes_to_visit)} min`}</div>
                  <div style={{ fontSize: '16px', color: 'var(--color-text-secondary)' }}>Median, case opened to visit</div>
                </div>
              </div>
            </div>

            <div style={{ flex: '1 1 300px', minWidth: 0 }}>
              <h2 style={{ margin: '0 0 10px', fontSize: '18px', color: 'var(--color-red-text)' }}>Open red cases ({data?.open_red_cases?.length ?? 0})</h2>
              <ul style={{ listStyle: 'none', margin: 0, padding: 0, fontSize: '16px' }}>
                {(data?.open_red_cases || []).map((c: { case_id: string; resident_name: string; ward_id: string; reason_labels: string[] }) => <li key={c.case_id} style={{ padding: '8px 0', borderTop: '1px solid var(--color-line-subtle)' }}>
                  <span style={{ fontWeight: 700 }}>{c.resident_name}</span> · {c.ward_id} · {c.reason_labels.join(' · ')}
                </li>)}
              </ul>
            </div>
          </section>

          <p style={{ margin: 0, paddingTop: '16px', borderTop: '1px solid var(--color-line)', fontSize: '14px', color: 'var(--color-text-muted)' }}>
            All residents are synthetic. Triage thresholds are illustrative and need clinical review before any real pilot.
          </p>
        </article>

        <div style={{ display: 'flex', justifyContent: 'flex-end' }} className="no-print">
          <Link
            to="/evidence"
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
            How do we know it is safe?
          </Link>
        </div>
      </main>
    </div>
  )
}

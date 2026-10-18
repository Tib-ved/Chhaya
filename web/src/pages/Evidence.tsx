import React from 'react';
import { Link } from 'react-router-dom';
import { ChhayaBrand } from '../components/ChhayaBrand';
import { TierLegend } from '../components/Legends';
import { request } from '../lib/api';
import { tierLabel } from '../lib/labels';
import type { EvalAsk, EvalLatest, EvalShowcase, EvalStats, EvalTier, EvalVoice } from '../lib/types';
import { useLoad } from '../lib/useLoad';

// Everything below the intro comes from the results file (GET /api/eval/latest). A part without a result
// says so; no figure is filled in here.

const NOT_MEASURED = 'Not measured yet';
const TIERS: EvalTier[] = ['red', 'amber', 'green'];
const LANGUAGES: Record<string, string> = { en: 'English', hi: 'Hindi', mwr: 'Marwari' };

/** The six Ask Chhaya checks, in the order the table shows them. */
const CHECKS: Array<[string, string]> = [
  ['tool_trajectory_avg_score', 'Right data used'],
  ['final_response_match_v2', 'Correct answer'],
  ['hallucinations_v1', 'No made-up claims'],
  ['grounded_numbers', 'Numbers from the data'],
  ['safe_response', 'Safe'],
  ['visual_emitted', 'Chart or table'],
];

/** "14 Oct" for a date or a time, as it falls in India. */
const day = (value?: string | null) => {
  if (!value) return null;
  const date = new Date(value.length === 10 ? `${value}T12:00:00+05:30` : value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'Asia/Kolkata' });
};
const of = (count: number, total: number) => `${count} of ${total}`;
const seconds = (value?: number | null) => (value == null ? null : `${value} s`);
const language = (code: string) => LANGUAGES[code] ?? code;
/** "Emergency chest pain" for the persona id emergency_chest_pain. */
/** What an unknown emergency means: the 108 line was still checked, from the resident's last words on. */
const unrecorded = (n: number) =>
  `For ${n} of them the moment of the flag was not recorded, so a 108 line anywhere after the resident's last words counted.`;
const persona = (id: string) => { const words = id.replace(/_/g, ' '); return words[0].toUpperCase() + words.slice(1); };
/** "run 14 Oct with gemini-3.8-flash", leaving out whatever the file does not say. A stand-in model is never named. */
const provenance = (verb: string, date?: string | null, model?: string | null, standIn?: boolean) =>
  [day(date) && `${verb} ${day(date)}`, model && !standIn && `with ${model}`].filter(Boolean).join(', ');

/** The one line that says a stand-in model, not Gemini, answered an Ask Chhaya run. */
const standIn = (ask: EvalAsk) => ask.stand_in
  ? 'Run with a stand-in model while Gemini on Vertex AI was congested; the deployed Ask Chhaya runs on Gemini.'
  : null;

/** Beside Ask Chhaya's times when a stand-in model answered: they are not the deployed assistant's speed. */
const STAND_IN_TIMES = 'Stand-in model timings; not the deployed Gemini assistant.';

/** The one line that says a stand-in text model, not Gemini, held the simulated check-in calls. */
const voiceStandIn = (voice: EvalVoice) => voice.stand_in
  ? "Simulated with a stand-in text model while Gemini on Vertex AI was congested; Chhaya's real calls run on Gemini Live."
  : null;

const Figure: React.FC<{ value: string | null; label: string; note?: React.ReactNode }> = ({ value, label, note }) => (
  <div className="figure-card">
    <span className="figure-label">{label}</span>
    <span className={`figure-value ${value ? '' : 'text evidence-missing'}`}>{value ?? NOT_MEASURED}</span>
    {note && <span className="figure-note">{note}</span>}
  </div>
);

const TierPill: React.FC<{ tier: string | null }> = ({ tier }) => (tier ? (
  <span className={`pill ${tier.toLowerCase()}`}><i className={`dot ${tier.toLowerCase()}`} aria-hidden="true" />{tierLabel(tier)}</span>
) : <span className="pill neutral">No tier</span>);

const Section: React.FC<{ id: string; title: string; lead?: React.ReactNode; children: React.ReactNode }> = ({ id, title, lead, children }) => (
  <section className="card evidence-section" aria-labelledby={id}>
    <header>
      <h2 id={id}>{title}</h2>
      {lead && <p>{lead}</p>}
    </header>
    {children}
  </section>
);

const Intro: React.FC<{ run: EvalShowcase | null }> = ({ run }) => {
  const recorded = run?.voice?.recorded ?? [];
  const voice = run?.voice;
  return (
    <div className="page-intro">
      <div className="evidence-crumbs">
        <Link to="/" className="linkbtn">← Home</Link>
        <span aria-hidden="true">/</span>
        <span>Evaluation</span>
      </div>
      <h1>How we test Chhaya</h1>
      <p>
        Before Chhaya rings a real person, an AI model plays residents against Chhaya's real instructions, tools and
        triage: someone over 80 who lives alone and feels dizzy, a resident with chest pain, an outdoor worker with a
        headache. They speak English, Hindi and Marwari. We check that each one gets the right tier, that nobody who
        needs a health worker now is given a lower tier, and that every emergency is told to call 108.
      </p>
      <p>
        Ask Chhaya, the officer's assistant, is checked question by question against a fixed dataset: did it use the
        right data, is the answer correct, is every number from the data. Each evaluation is run once and shown as it
        came out, with its date and model.
      </p>
      {run && (
        <ul className="evidence-meta">
          <li>Check-in calls: {recorded.length ? recorded.map(r => `${r.conversations} conversations ${provenance('recorded', r.date, r.model, voice?.stand_in)}`).join('; ') : NOT_MEASURED}</li>
          <li>Ask Chhaya: {run.ask ? `${provenance('run', run.ask.run_at, run.ask.model, run.ask.stand_in)}` : NOT_MEASURED}</li>
          <li>Results file: {provenance('built', run.generated_at)}</li>
        </ul>
      )}
      {run?.voice && voiceStandIn(run.voice) && <p className="evidence-hint">{voiceStandIn(run.voice)}</p>}
      {run?.ask && standIn(run.ask) && <p className="evidence-hint">{standIn(run.ask)}</p>}
    </div>
  );
};

const Headlines: React.FC<{ voice: EvalVoice | null; ask: EvalAsk | null }> = ({ voice, ask }) => {
  const recorded = voice?.recorded.at(-1);
  const when = voice
    ? `${provenance('Recorded', recorded?.date, voice.model, voice.stand_in)}${voice.stand_in ? ' (stand-in model)' : ''}`
    : null;
  const e = voice?.emergencies;
  return (
    <section aria-label="Headline results" className="figure-row">
      <Figure label="Red residents under-triaged" value={voice && of(voice.red_under_triaged, voice.red_expected)}
        note={voice && <>Given a lower tier than Red. {when}</>} />
      <Figure label="Conversations at the expected tier" value={voice && of(voice.tier_matches, voice.conversations)} note={when} />
      <Figure label="Emergencies told to call 108" value={e ? of(e.line_spoken, e.flagged) : null}
        note={e && <>{`Flagged in ${of(e.flagged, e.total)} emergencies; counted over those flagged. `}{e.unknown > 0 && `${unrecorded(e.unknown)} `}{when}</>} />
      <Figure label="Ask Chhaya answers passing every check" value={ask && of(ask.passed, ask.expected)}
        note={ask && <>{!ask.complete && 'The run was incomplete. '}{provenance('Run', ask.run_at, ask.model, ask.stand_in)}{ask.stand_in ? ' (stand-in model)' : ''}</>} />
    </section>
  );
};

const CheckInCalls: React.FC<{ voice: EvalVoice }> = ({ voice }) => {
  const e = voice.emergencies;
  const q = voice.required_questions;
  return (
    <Section id="evidence-calls" title="Check-in calls with simulated residents"
      lead={`${voice.conversations} conversations between Chhaya and residents played by ${voice.stand_in ? 'a model' : 'Gemini'}, ${provenance('recorded', voice.recorded.at(-1)?.date, voice.model, voice.stand_in)}.`}>
      {voiceStandIn(voice) && <p className="evidence-warning">{voiceStandIn(voice)}</p>}
      <div className="evidence-calls">
        <div className="evidence-grid">
          <p className="evidence-hint">Rows: the tier the resident should get. Columns: the tier Chhaya gave.</p>
          <div className="ops-table-wrap">
            <table className="ops-table evidence-tier-grid" aria-label="Expected tier against the tier Chhaya gave">
              <thead>
                <tr><th scope="col">Expected</th>{TIERS.map(t => <th key={t} scope="col" className="num">{tierLabel(t)}</th>)}<th scope="col" className="num">No tier</th></tr>
              </thead>
              <tbody>
                {TIERS.map(expected => (
                  <tr key={expected}>
                    <th scope="row"><TierPill tier={expected} /></th>
                    {[...TIERS, 'none' as const].map(got => (
                      <td key={got} className={`num ${got === expected ? 'evidence-match' : ''}`}>{voice.confusion[expected]?.[got] ?? 0}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <TierLegend />
        </div>
        <dl className="evidence-facts">
          <div>
            <dt>At the expected tier, by language</dt>
            <dd><ul>{Object.entries(voice.by_language).map(([code, l]) => <li key={code}>{language(code)}: {of(l.matched, l.total)}</li>)}</ul></dd>
          </div>
          <div>
            <dt>Required questions</dt>
            <dd>All asked in {of(q.asked_all, q.full_calls)} calls that ran to the end</dd>
          </div>
          <div>
            <dt>Emergencies</dt>
            <dd>
              Flagged in {of(e.flagged, e.total)}. Told to call 108 before the call ended: {of(e.line_spoken, e.flagged)}.
              {e.unknown > 0 && ` ${unrecorded(e.unknown)}`}
            </dd>
          </div>
        </dl>
      </div>
      <details className="evidence-details">
        <summary>Every conversation ({voice.rows.length})</summary>
        <div className="ops-table-wrap">
          <table className="ops-table">
            <thead>
              <tr><th scope="col">Resident</th><th scope="col">Language</th><th scope="col">Expected</th><th scope="col">Got</th><th scope="col">Result</th><th scope="col" className="num">Lines</th><th scope="col">Told to call 108</th></tr>
            </thead>
            <tbody>
              {voice.rows.map(row => (
                <tr key={row.id}>
                  <td className="cell-strong">{persona(row.persona_id)}</td>
                  <td>{language(row.language)}</td>
                  <td><TierPill tier={row.expected} /></td>
                  <td><TierPill tier={row.got} /></td>
                  <td className={row.passed ? '' : 'evidence-fail'}>{row.passed ? 'Passed' : 'Failed'}</td>
                  <td className="num">{row.lines}</td>
                  <td className={row.emergency && !row.line_spoken ? 'evidence-fail' : ''}>{row.emergency ? (row.line_spoken ? 'Yes' : 'No') : 'Not an emergency'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </Section>
  );
};

const AskChhaya: React.FC<{ ask: EvalAsk | null; refusals: EvalShowcase['refusals'] }> = ({ ask, refusals }) => (
  <Section id="evidence-ask" title="Ask Chhaya"
    lead={ask ? `${of(ask.passed, ask.expected)} questions passed every check, ${provenance('run', ask.run_at, ask.model, ask.stand_in)}.` : undefined}>
    {ask ? (
      <>
        {standIn(ask) && <p className="evidence-warning">{standIn(ask)}</p>}
        {!ask.complete && <p className="evidence-warning">The run was incomplete: {of(ask.cases, ask.expected)} questions finished.</p>}
        <div className="ops-table-wrap">
          <table className="ops-table evidence-ask-table">
            <thead>
              <tr>
                <th scope="col">Question</th>
                {CHECKS.map(([key, label]) => <th key={key} scope="col">{label}</th>)}
                <th scope="col">Result</th>
                <th scope="col" className="num">Time</th>
              </tr>
            </thead>
            <tbody>
              {ask.rows.map((row, index) => (
                <tr key={`${row.case}-${row.run}-${index}`}>
                  <td className="evidence-question">{row.question || row.case}</td>
                  {CHECKS.map(([key]) => {
                    const check = row.checks[key];
                    return check
                      ? <td key={key} className={check.passed ? '' : 'evidence-fail'}>{check.passed ? 'Passed' : 'Failed'}</td>
                      : <td key={key} className="muted"><span aria-hidden="true">–</span><span className="sr-only">Not checked</span></td>;
                  })}
                  <td className={row.passed ? '' : 'evidence-fail'}>{row.passed ? 'Passed' : 'Failed'}</td>
                  <td className="num">{seconds(row.seconds) ?? '–'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </>
    ) : <p className="evidence-missing">{NOT_MEASURED}</p>}
    <p className="evidence-refusals">
      <b>Refusals:</b>{' '}
      {refusals
        ? `${of(refusals.passed, refusals.cases)} refused correctly, ${day(refusals.run_at)}${refusals.rerun ? '' : ', not re-run'}${refusals.model ? `, with ${refusals.model}` : ''}.`
        : NOT_MEASURED}
    </p>
  </Section>
);

const stats = (s: EvalStats | null | undefined, count: number, unit: string) =>
  s ? [`median ${s.p50} s`, s.p90 != null && `90th percentile ${s.p90} s`, `slowest ${s.max} s`, `over ${count} ${unit}`].filter(Boolean).join(', ') : NOT_MEASURED;

const STEPS: Array<['gate' | 'model' | 'tool' | 'specialists', string, string]> = [
  ['gate', 'Topic check', 'var(--color-data-reached)'],
  ['model', 'Model', 'var(--color-data-visits)'],
  ['tool', 'Data tools', 'var(--color-data-unreached)'],
  // The analyst and the call summariser: their own model calls and data tools.
  ['specialists', 'Specialist agents', 'var(--color-data-pending)'],
];

const Speed: React.FC<{ latency: EvalShowcase['latency']; ask: EvalAsk | null }> = ({ latency, ask }) => {
  const voice = latency?.voice ?? null;
  const earlier = latency?.voice_pickup_earlier;
  const steps = ask?.steps_s;
  const parts = STEPS.map(([key, label, colour]) => ({ label, colour, value: steps?.[key] })).filter(p => p.value != null) as Array<{ label: string; colour: string; value: number }>;
  const total = parts.reduce((sum, p) => sum + p.value, 0);
  const answer = ask?.latency_s?.p50 != null ? ask.latency_s : null;
  return (
    <Section id="evidence-speed" title="Speed">
      <div className="evidence-speed">
        <div>
          <h3>Voice calls</h3>
          {voice ? (
            <dl className="evidence-facts">
              <div><dt>Pickup: from answering to Chhaya's first word</dt><dd>{stats(voice.pickup_s, voice.calls, 'calls')}</dd></div>
              <div><dt>Reply: from the resident stopping to Chhaya starting</dt><dd>{stats(voice.reply_s, voice.turns, 'turns')}</dd></div>
              {(voice.reply_with_tool_s || voice.reply_without_tool_s) && (
                <div><dt>Reply, split</dt><dd>With a data tool: median {seconds(voice.reply_with_tool_s?.p50) ?? NOT_MEASURED.toLowerCase()}. Without: median {seconds(voice.reply_without_tool_s?.p50) ?? NOT_MEASURED.toLowerCase()}.</dd></div>
              )}
              <div><dt>Measured</dt><dd>{day(voice.measured_at)}{voice.model && `, with ${voice.model}`}, from the service's logs at our server</dd></div>
            </dl>
          ) : <p className="evidence-missing">{NOT_MEASURED}</p>}
          {earlier && <p className="evidence-hint">Earlier, {day(earlier.date)}: answering to first word went from {earlier.before_s} s to {earlier.after_s} s{earlier.model ? ` (${earlier.model})` : ''}.</p>}
        </div>
        <div>
          <h3>Ask Chhaya</h3>
          {answer ? (
            <p>Time per answer: median {answer.p50} s, slowest {answer.max} s, {provenance('run', ask!.run_at, ask!.model, ask!.stand_in)}.{ask!.stand_in && ` ${STAND_IN_TIMES}`}</p>
          ) : <p className="evidence-missing">{NOT_MEASURED}</p>}
          {ask && (parts.length && total > 0 ? (
            <>
              <div className="evidence-steps" role="img"
                aria-label={`Ask Chhaya time by step, median seconds: ${parts.map(p => `${p.label.toLowerCase()} ${p.value} s`).join(', ')}`}>
                {parts.map(p => <i key={p.label} style={{ flexGrow: p.value, background: p.colour }} />)}
              </div>
              <ul className="evidence-steps-key">
                {parts.map(p => <li key={p.label}><i style={{ background: p.colour }} aria-hidden="true" />{p.label} {p.value} s</li>)}
              </ul>
              <p className="evidence-hint">The median of each step{steps?.invocations ? ` over ${steps.invocations} answers` : ''}; the medians need not add up to the median answer.{ask.stand_in && ` ${STAND_IN_TIMES}`}</p>
            </>
          ) : <p className="evidence-hint">Split by step: {NOT_MEASURED.toLowerCase()}.</p>)}
        </div>
      </div>
    </Section>
  );
};

const newestFirst = (rows: EvalShowcase['history']) => rows.map((row, index) => ({ row, index }))
  .sort((a, b) => (b.row.date ?? '').localeCompare(a.row.date ?? '') || a.index - b.index).map(x => x.row);

const History: React.FC<{ rows: EvalShowcase['history'] }> = ({ rows: unsorted }) => {
  const rows = newestFirst(unsorted);
  return (
  <Section id="evidence-history" title="How we got here">
    {rows.length ? (
      <ul className="evidence-history">
        {rows.map((row, index) => (
          <li key={`${row.date}-${index}`}>
            <span className="evidence-history-date">{day(row.date)}</span>
            <span>
              {row.what}: <b>{row.before} → {row.after}</b>
              {row.model && <span className="muted"> ({row.model})</span>}
            </span>
          </li>
        ))}
      </ul>
    ) : <p className="evidence-missing">{NOT_MEASURED}</p>}
  </Section>
  );
};

export const EvidencePage: React.FC = () => {
  const { data, isLoading, error } = useLoad<EvalLatest>(() => request<EvalLatest>('/api/eval/latest'));
  const run = data?.run ?? null;

  return (
    <div className="page">
      <header className="welcome-header">
        <Link to="/" aria-label="Chhaya home">
          <ChhayaBrand />
        </Link>
        <nav aria-label="Views">
          <Link to="/regional/overview" className="linkbtn">Regional officer</Link>
          <Link to="/ward/overview?state=RJ&district=churu" className="linkbtn">Field crew</Link>
        </nav>
      </header>

      <div className="app-header-demo">
        Demo data: the residents are fictional. Calls in the rounds are simulated calls with AI-played residents; test calls you make are real.
      </div>

      <main className="page-main evidence-page">
        <Intro run={run} />
        {isLoading ? <p className="evidence-hint">Loading the results…</p>
          : error ? <p className="evidence-warning">The results could not be loaded. Reload the page to try again.</p>
          : !run ? <p className="evidence-empty">No results have been published for this build yet.</p>
          : (
            <>
              <Headlines voice={run.voice} ask={run.ask} />
              {run.voice ? <CheckInCalls voice={run.voice} /> : (
                <Section id="evidence-calls" title="Check-in calls with simulated residents"><p className="evidence-missing">{NOT_MEASURED}</p></Section>
              )}
              <AskChhaya ask={run.ask} refusals={run.refusals} />
              <Speed latency={run.latency} ask={run.ask} />
              <History rows={run.history ?? []} />
              <Section id="evidence-limits" title="What we have not tested yet">
                {run.limitations?.length ? <ul className="evidence-limits">{run.limitations.map(item => <li key={item}>{item}</li>)}</ul>
                  : <p className="evidence-missing">{NOT_MEASURED}</p>}
              </Section>
            </>
          )}
      </main>
    </div>
  );
};

export default EvidencePage;

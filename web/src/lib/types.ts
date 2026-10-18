export interface CharacterCard {
  id: string
  name: string
  age: number
  ward: string
  ward_name: string
  living: string
  condition?: string
  work?: string
  nudge: string
  demonstrates: string
}

export interface DemoInfo {
  round_id: string | null
  built_at: string | null
  cards: CharacterCard[]
  languages: Array<{ code: string; label: string; experimental?: boolean }>
}

export interface RoundOverview {
  round?: {
    id: string
    district: string
    hazard: string
    severity: string
    opened_at: string
    alert_id?: string
    peak_temp_c?: number | null
    peak_hour_local?: number | null
  } | null
  alert?: {
    id: string
    district: string
    hazard: string
    severity: string
    headline?: string
    peak_temp_c?: number
    source?: string
    issued_at?: string
    effective_from?: string
  } | null
  counts?: {
    targeted: number
    queued: number
    in_call: number
    reached: number
    unreachable: number
    red: number
    amber: number
    green: number
  }
  calls?: Array<{
    call_id: string
    resident_id: string
    resident_name: string
    age: number
    ward_id: string
    ward_name: string
    risk_score: number
    risk_factors: string[]
    attempt: number
    status: string
    tier: 'red' | 'amber' | 'green' | null
    yours: boolean
  }>
}

export interface GlassBoxQuestion {
  id: string
  label: string
  value: string | null
  value_label: string | null
  quote: string | null
  at: string | null
}

export interface GlassBoxFeed {
  call_id: string
  status: 'queued' | 'dialing' | 'in_progress' | 'completed' | 'dropped' | 'no_answer' | 'busy' | 'failed'
  started_at: string
  ended_at: string | null
  end_outcome: string | null
  language_used: string
  respondent: 'self' | 'family' | 'other' | null
  transcript: Array<{ role: 'agent' | 'resident' | 'system'; text: string; at: string }>
  questions: GlassBoxQuestion[]
  emergency: { reason: string; at: string } | null
  rules_so_far: Array<{ id: string; tier: 'amber' | 'red'; label: string }>
  tier_so_far: 'green' | 'amber' | 'red' | null
  case: {
    id: string
    tier: 'amber' | 'red' | 'green'
    reasons: string[]
    reason_labels: string[]
    ward_id: string
    note: string | null
  } | null
}

export interface WardCase {
  id: string
  tier: 'RED' | 'AMBER' | 'GREEN'
  name: string
  age: number
  reasons: string
  quote?: string
  note?: string
  status: string
  visited_at?: string
  outcome?: string
  outcome_label?: string
  yours?: boolean
  can_mark?: boolean
  lat?: number
  lng?: number
  quotes?: Record<string, string>
  dialogue?: Array<{ role: string; text: string; at?: string }>
}

export interface WardInfo {
  id: string
  name: string
  worker_name: string
  to_visit_today: number
  visited_today: number
}

export interface DistrictReport {
  round_id: string
  district: string
  hazard: string
  severity: string
  includes_yours: boolean
  opened_at: string
  closed_at?: string
  targeted: number
  reached: number
  unreachable: number
  still_trying: number
  not_callable: number
  tiers_by_ward: Array<{
    ward_id: string
    ward_name: string
    red: number
    amber: number
    green: number
    total: number
  }>
  problems: Array<{ rule: string; label: string; count: number }>
  response: {
    visited: number
    open: number
    median_minutes: number
  }
  open_red_cases: Array<{
    name: string
    age: number
    ward_name: string
    reasons: string
  }>
  summary?: string
}

// GET /api/eval/latest: the results file behind "How we test Chhaya" (backend/scripts/build_eval_showcase.py).
// A part that has not been measured is null.
export type EvalTier = 'red' | 'amber' | 'green'
export interface EvalStats { p50: number; p90?: number; p95?: number; max: number }
export interface EvalVoice {
  model: string | null
  recorded: Array<{ date: string; commit: string | null; model: string | null; conversations: number }>
  /** True when Claude stood in for Chhaya's text model in the recording; resident_model played the residents. */
  stand_in?: boolean
  resident_model?: string | null
  conversations: number
  tier_matches: number
  red_expected: number
  red_under_triaged: number
  /** Expected tier, then the tier Chhaya gave ("none" when the call ended without one). */
  confusion: Record<EvalTier, Record<EvalTier | 'none', number>>
  by_language: Record<string, { matched: number; total: number }>
  required_questions: { asked_all: number; full_calls: number }
  /** total: flagged or emergency personas; line_spoken and unknown count the flagged ones. unknown: the moment
   *  of the flag was not recorded, so a 108 line anywhere after the resident's last words counted. */
  emergencies: { total: number; flagged: number; line_spoken: number; unknown: number }
  rows: Array<{
    id: string; persona_id: string; language: string; expected: string; got: string | null
    passed: boolean; lines: number; emergency: boolean; line_spoken: boolean
  }>
}
export interface EvalAsk {
  run_at: string
  commit: string | null
  model: string | null
  /** True when Claude answered in place of Gemini for this run. */
  stand_in?: boolean
  small_model?: string | null
  /** The model that graded the answers. */
  judge_model?: string | null
  cases: number
  passed: number
  expected: number
  complete: boolean
  latency_s: Partial<EvalStats> | null
  /** Median seconds per step across the answers. */
  steps_s: { gate?: number; model?: number; tool?: number; specialists?: number; invocations?: number } | null
  rows: Array<{
    case: string; question: string; passed: boolean; seconds: number | null; run: number
    checks: Record<string, { score: number | null; passed: boolean }>
  }>
}
export interface EvalVoiceLatency {
  measured_at: string
  since: string
  until: string
  turns: number
  reply_s: EvalStats | null
  reply_with_tool_s: EvalStats | null
  reply_without_tool_s: EvalStats | null
  /** Call pickups measured. */
  calls: number
  pickup_s: EvalStats | null
  /** The live model the service ran on those calls. */
  model: string | null
}
export interface EvalShowcase {
  version: number
  generated_at: string
  commit: string
  voice: EvalVoice | null
  ask: EvalAsk | null
  refusals: { run_at: string; cases: number; passed: number; rerun: boolean; model?: string | null } | null
  latency: {
    voice: EvalVoiceLatency | null
    voice_pickup_earlier?: { date: string; before_s: string; after_s: string; commit: string; model?: string | null } | null
  } | null
  history: Array<{ date: string; commit: string | null; what: string; before: string; after: string; fill?: string; model?: string | null }>
  limitations: string[]
}
export interface EvalLatest { run: EvalShowcase | null }

// GET /api/weather: the stored weather of one state, district or ward (live snapshots or the demo scenario).
export type WeatherSeverity = 'red' | 'orange' | 'yellow' | null
export interface WeatherHour {
  hour: number; temp_c: number | null; feels_like_c: number | null; heat_index_c: number | null
  humidity_pct: number | null; rain_pct: number | null
  condition_type: string | null; condition_text: string | null; icon_uri: string | null; is_day: boolean | null
}
export interface WeatherDay {
  date: string; max_c: number | null; min_c: number | null
  condition_type: string | null; condition_text: string | null; icon_uri: string | null; rain_pct: number | null
}
export interface WeatherView {
  area: { kind: 'state' | 'district' | 'ward'; id: string; name: string; state: string }
  source: 'google_weather' | 'demo_scenario'
  fetched_at: string | null
  now: { hour: number; temp_c: number | null; feels_like_c: number | null; condition_text: string | null; icon_uri: string | null; is_day: boolean | null } | null
  today: { max_c: number | null; min_c: number | null; humidity_pct: number | null; rain_pct: number | null } | null
  hours: WeatherHour[]
  days: WeatherDay[]
  state: {
    name: string; mean_peak_c: number | null; max_peak_c: number | null
    districts: { id: string; name: string; peak_c: number | null; peak_hour: number | null; alert: WeatherSeverity | string }[]
  } | null
  alert: { severity: WeatherSeverity | string; reason_text: string | null; raw: Record<string, unknown> } | null
  unavailable_since: string | null
}

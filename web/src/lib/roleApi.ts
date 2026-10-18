// The role-view API (backend/app/api.py, tagged "Role views"); shapes follow backend/app/schemas.py.
import { request } from './api'
import { getMode } from './mode'
import type { WeatherView } from './types'

export type Tier = 'red' | 'amber' | 'green'
export type Need = 'water_ors' | 'medicine_check' | 'cooling_centre'
export type CallStatus = 'not_called' | 'reached' | 'no_answer' | 'trying'

export type ResidentRow = {
  id: string; name: string; name_hi: string; age: number
  ward_id: string | null; ward_name: string | null; worker_id: string | null
  risk_score: number; lives_alone: boolean; outdoor_worker: boolean; conditions: string[]
  lat: number | null; lng: number | null
  call_status: CallStatus; attempts: number
  tier: Tier | null; reasons: string[]; reason_labels: string[]; needs: Need[]
  case_id: string | null; case_status: 'open' | 'visited' | null
  opened_at: string | null; visited_at: string | null
  outcome: string | null; outcome_label: string | null
  waiting_minutes: number | null; escalated: boolean; raised: boolean; yours: boolean
  // Marks for what the demo plays rather than records: a call held with an AI-played resident, a visit no one made.
  simulated?: 'ai_resident' | null; simulated_visit?: boolean
}
export type WorkerSummary = { id: string; name: string; name_hi: string; ward_id: string | null; ward_name: string | null; open: number; done: number }
export type WorkersView = { district_id: string; workers: WorkerSummary[] }
export type Place = { name: string; lat: number; lng: number }
export type WorkerView = {
  worker: { id: string; name: string; name_hi: string; ward_id: string; ward_name: string; district_id: string; district_name: string }
  counts: { to_visit: number; done: number; red_open: number; longest_wait_minutes: number | null }
  carry: { id: Need; label: string; count: number }[]
  health_centre: Place | null
  residents: ResidentRow[]
}

export type RoundFigures = {
  called: number; reached: number; no_answer: number; trying: number
  red: number; amber: number; green: number
  to_visit: number; visited: number; red_open: number; median_minutes_to_visit: number | null
}
export type RoundWarning = {
  hazard: string; severity: string | null; source: string | null; peak_temp_c: number | null; peak_hour_local: number | null; opened_at: string | null
  // Why the alert was raised: a heat scenario or a real forecast, the sentence, the threshold crossed and when.
  scenario?: boolean; reason?: string | null; threshold_c?: number | null; raised_at?: string | null
}
export type HourTemp = { hour: number; temp_c: number }
export type DistrictCard = { id: string; name: string; state: string | null; wards: number; residents: number; high_risk: number; living_alone: number; aged_75_plus: number; warning: RoundWarning | null; round: RoundFigures | null }
export type RegionView = { districts: DistrictCard[] }
export type WardCard = { id: string; name: string; worker_id: string | null; worker_name: string | null; residents: number; high_risk: number; round: RoundFigures | null }
export type DistrictView = {
  district: { id: string; name: string; state: string | null }
  warning: RoundWarning | null
  as_of: string | null
  round: RoundFigures | null
  wards: WardCard[]
  problems: { rule: string; label: string; count: number }[]
  progress: { minutes: number[]; reached: number[]; opened: number[]; visited: number[] } | null
  forecast: HourTemp[] | null
  attention: ResidentRow[]
  residents: ResidentRow[]
  // The real forecast beside a scenario curve, the heat thresholds, and before a round the residents Chhaya would call first.
  real_forecast?: HourTemp[] | null
  // Where `forecast` comes from: the heat scenario's curve before a round, or the round's own.
  forecast_source?: 'scenario' | 'round' | 'weather' | null
  thresholds?: { orange: number; red: number } | null
  call_plan?: PlanRow[] | null
}
/** One resident the round would call first: reasons are plain words (or rule ids with labels beside them). */
export type PlanRow = { id: string; name: string; name_hi?: string; age: number; ward_name?: string | null; risk_score?: number; reasons?: string[]; reason_labels?: string[] }

export type CaseDetail = {
  id: string; tier: Tier; status: 'open' | 'visited'
  opened_at: string | null; visited_at: string | null
  outcome: string | null; outcome_label: string | null; visit_note: string
  simulated_visit?: boolean
  waiting_minutes: number | null
  escalation: { reason: string; reason_label: string; note: string; at: string; acknowledged_at: string | null; reply: string } | null
  raised: { observation: string; observation_label: string; at: string } | null
  worker_id: string | null; ward_worker_id: string | null; reassigned_from: string | null
  can_act: boolean; can_acknowledge: boolean
}
export type ResidentView = {
  resident: ResidentRow & { language: string | null; district_id: string; district_name: string; worker_name: string | null; emergency_contact_name: string | null }
  warning: RoundWarning | null
  call: { status: CallStatus; attempts: number; first_at: string | null; answered_by: string | null; language_used: string | null; recorded_at?: string | null } | null
  questions: { id: string; label: string; value: string; value_label: string; quote: string | null; worry: 'amber' | 'red' | null }[]
  rules: { id: string; label: string; tier: Tier; question_id: string | null; detail: string }[]
  transcript: { role: 'agent' | 'resident' | 'system'; text: string; at: string | null }[]
  note: string
  case: CaseDetail | null
  health_centre: Place | null
  workers: { id: string; name: string; name_hi: string; ward_name: string | null; open: number }[]
  // When the conversation with an AI-played resident was recorded; the marks may also sit on the resident row.
  recorded_at?: string | null; simulated?: 'ai_resident' | null; simulated_visit?: boolean
}

const read = <T,>(path: string) => request<T>(path, { cache: 'no-store' })
const part = encodeURIComponent

export const getRegion = () => read<RegionView>('/api/region')
export const getDistrict = (id: string) => read<DistrictView>(`/api/districts/${part(id)}`)
export const getWorkers = () => read<WorkersView>('/api/workers')
export const getWorker = (id: string) => read<WorkerView>(`/api/workers/${part(id)}/residents`)
export const getResident = (id: string) => read<ResidentView>(`/api/residents/${part(id)}`)
/** The stored weather of the area in view: a ward, else a district, else a state. Never waits on the Weather API. */
export const getWeather = (area: { state?: string; district_id?: string; ward_id?: string }) => {
  const path = weatherPath(area), key = `${getMode()} ${path}`
  // A read started early by prefetchWeather is used once, if it is still fresh; else the weather is read now.
  const early = earlyWeather.get(key)
  earlyWeather.delete(key)
  return early && Date.now() - early.at < EARLY_WEATHER_MS ? early.read : read<WeatherView>(path)
}

/** Starts reading the weather of the area before the page that shows it is drawn (the workspace's first load). */
export const prefetchWeather = (area: { state?: string; district_id?: string; ward_id?: string }) => {
  // The dataset is part of the key: a read from one never answers the other.
  const path = weatherPath(area), key = `${getMode()} ${path}`
  if (earlyWeather.has(key)) return
  const early = read<WeatherView>(path)
  early.catch(() => earlyWeather.delete(key))  // the page reads again and shows its own error
  earlyWeather.set(key, { at: Date.now(), read: early })
}

const EARLY_WEATHER_MS = 30_000
const earlyWeather = new Map<string, { at: number; read: Promise<WeatherView> }>()
const weatherPath = (area: { state?: string; district_id?: string; ward_id?: string }) => {
  const query = new URLSearchParams()
  if (area.state) query.set('state', area.state)
  if (area.district_id) query.set('district_id', area.district_id)
  if (area.ward_id) query.set('ward_id', area.ward_id)
  const search = query.toString()
  return `/api/weather${search ? '?' + search : ''}`
}

export type CaseAction =
  | { action: 'visit'; outcome: string; note: string }
  | { action: 'escalate'; reason: string; note: string }
  | { action: 'acknowledge'; reply: string }
  | { action: 'reassign'; worker_id: string }
  | { action: 'raise'; observation: string }

/** Act on a case. The API refuses with a sentence (ApiError.message) and, mostly, a reason. */
export function actOnCase(caseId: string, { action, ...body }: CaseAction) {
  return request<unknown>(`/api/cases/${part(caseId)}/${action}`, { method: 'POST', body: JSON.stringify(body) })
}

import { request } from './lib/api'

export type Tier = 'red' | 'amber' | 'green'
export type Role = 'admin' | 'crew'
export type Scope = { state_id: string; district_id: string; ward_id: string; round_id: string }
export type Worker = { id: string; name: string; ward_ids?: string[]; active_tasks?: number }
export type Session = { role: Role; worker_id: string | null; demo: boolean; persistent: boolean; assistant_ready: boolean; notifications_ready: boolean; workers: Worker[] }
export type Ward = { id: string; name: string; synthetic: boolean }
export type District = { id: string; name: string; state_id: string; onboarded: boolean; wards: Ward[] }
export type State = { id: string; name: string; source_url: string; districts: District[] }
export type Geography = { states: State[]; source: string; retrieved_at: string; ward_note: string }
export type Case = { reason_ids: string[]; id: string; resident_id: string; round_id: string; resident_name: string; age: number; language: string; risk_score: number; ward_id: string; ward_name: string; worker_id: string | null; tier: Tier; status: string; note: string; reason_labels: string[]; task_status: string; opened_at: string | null; visited_at: string | null; outcome: string | null; simulated_visit?: boolean }
export type Task = { id: string; case_id: string; worker_id: string; status: string; created_at: string; updated_at: string; note?: string; outcome?: string }
export type Activity = { id: string; event: string; actor: string; at: string; detail?: string; case_id: string }
export type ResidentRow = { id: string; name: string; age: number; language: string; district_id: string; ward_id: string; ward_name: string; case_id: string | null; outreach: string; lat: number | null; lng: number | null; risk_score: number; lives_alone: boolean; outdoor_worker: boolean; conditions: string[]; answers: Record<string, { value: string; quote: string; at: string | null }>; call: { id: string; attempt: number; status: string; started_at: string | null; ended_at: string | null; created_at: string | null } | null; name_hi?: string; needs?: string[]; escalated?: boolean; raised?: boolean; simulated?: string | null }
export type Facility = { id: string; name: string; ward_id: string; type: string; lat: number | null; lng: number | null }
export type Overview = { wards: { id: string; name: string; district_id: string; worker_id: string | null; lat: number | null; lng: number | null }[]; facilities: Facility[]; residents: ResidentRow[]; scope: Scope; as_of: string; has_data: boolean; demo: boolean; counts: Record<string, number>; tiers: Record<Tier, number>; coverage: Record<string, number>; cases: Case[]; groups: { id: string; name: string; level: string; red: number; amber: number; green: number }[]; workers: Worker[]; tasks: Task[]; events: Activity[]; available_rounds: { id: string; district_id: string; hazard: string; opened_at: string }[]; rounds: { id: string; district_id: string; hazard: string; severity: string | null; status: string; opened_at: string; peak_temp_c: number | null }[] }
export type Feed = { call_id: string; status: string; started_at: string | null; ended_at: string | null; language_used: string | null; transcript: { role: string; text: string; at?: string }[]; questions: { id: string; label: string; value?: string; value_label: string | null; quote: string | null; at: string | null }[] }
export type Detail = { case: Case; task: Task | null; resident: { name: string; age: number; language: string; lives_alone: boolean; conditions: string[]; lat: number | null; lng: number | null }; calls: Feed[]; events: Activity[] }
export type Action = 'assign_crew' | 'notify_crew' | 'notify_resident'
export type Proposal = { id: string; action: Action; case_id: string; worker_id: string; worker_name: string; resident_name: string; content: string; recipient: string; status: string; demo: boolean; created_at: string; expires_at: string; approved_at?: string; submitted_at?: string; provider_updated_at?: string; error?: string }
export type Message = { id: string; role: 'user' | 'assistant'; text: string; mode: string; at: string; data_as_of?: string; sources?: string[]; action_errors?: string[]; kind?: string }
export type Thread = { id: string; title: string; scope: Scope; updated_at: string; messages: Message[]; proposals: Proposal[]; busy: boolean }
export type OpsApi = <T>(path: string, body?: unknown) => Promise<T>

export function makeOpsApi(role: Role, worker: string): OpsApi {
  return async <T,>(path: string, body?: unknown): Promise<T> => {
    return request<T>(`/api/ops${path}`, {
      method: body === undefined ? 'GET' : 'POST', cache: 'no-store',
      headers: { 'Content-Type': 'application/json', 'X-Demo-Role': role, 'X-Demo-Worker': worker },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    })
  }
}

export const time = (value?: string | null) => value ? new Intl.DateTimeFormat('en-IN', { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)) + ' IST' : 'Not recorded'
export const words = (value: string) => { const spaced = value.replaceAll('_', ' '); return spaced.charAt(0).toUpperCase() + spaced.slice(1) }
export const actionLabel: Record<Action, string> = { assign_crew: 'Assign a health worker', notify_crew: 'Send the worker a message', notify_resident: 'Call the resident with an update' }
export const scopeQuery = (scope: Scope) => new URLSearchParams(Object.entries(scope).filter(([, value]) => value)).toString()

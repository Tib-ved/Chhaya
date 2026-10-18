// What the workspace shell and its pages need (session, geography, overview), built from the
// role-view API alone: /api/region, /api/districts/{id} and /api/workers. Nothing here calls /api/ops.
// Read when a page opens, on Refresh and after an action; never on a timer.
import type { Case, Geography, Overview, ResidentRow, Role, Scope, Session, Tier } from '../operations'
import { getDistrict, getRegion, getWorker } from './roleApi'
import type { DistrictCard, DistrictView, ResidentRow as Row, WorkerView } from './roleApi'

/** Every district's card, in full only the districts of the states read so far, and the health workers of
 * `teamStates`. The workers come after their state's districts: the officer's pages are drawn without them. */
export type Loaded = { region: DistrictCard[]; districts: DistrictView[]; team: WorkerView[]; states: string[]; teamStates: string[] }

const stateOfCard = (d: DistrictCard) => d.state || 'Other'
const stateOf = (d: DistrictView) => d.district.state || 'Other'

/** The state to show: the one asked for, else the state of the district or worker asked for,
 * else the first state under a warning. A worker's id begins with her district's. */
export function stateFor(region: DistrictCard[], wanted: { state: string; district: string; worker: string }): string {
  if (wanted.state && region.some(d => stateOfCard(d) === wanted.state)) return wanted.state
  const id = wanted.district || (wanted.worker.includes('-worker-') ? wanted.worker.split('-worker-')[0] : '')
  const card = region.find(d => d.id === id) || region.find(d => d.warning) || region[0]
  return card ? stateOfCard(card) : ''
}

/** A state's districts in full. */
export function loadState(region: DistrictCard[], state: string): Promise<DistrictView[]> {
  return Promise.all(region.filter(d => stateOfCard(d) === state).map(d => getDistrict(d.id)))
}

/** Every ward's worker in a state, with her residents. */
export function loadTeam(loaded: Loaded, state: string): Promise<WorkerView[]> {
  const districts = loaded.districts.filter(d => stateOf(d) === state)
  const workerIds = [...new Set(districts.flatMap(d => d.wards.map(w => w.worker_id)).filter((id): id is string => Boolean(id)))]
  return Promise.all(workerIds.map(getWorker))
}

/** The region and the picked state's districts. `previous` is the load being refreshed: its workers stay
 * shown until their state's are read again. */
export async function loadRegion(pick: (region: DistrictCard[]) => string, previous: Loaded | null): Promise<Loaded> {
  const region = (await getRegion()).districts
  const state = pick(region)
  return { region, districts: state ? await loadState(region, state) : [], team: previous?.team || [], states: state ? [state] : [], teamStates: [] }
}

export function withState(loaded: Loaded, state: string, districts: DistrictView[]): Loaded {
  if (loaded.states.includes(state)) return loaded
  return { ...loaded, districts: [...loaded.districts, ...districts], states: [...loaded.states, state] }
}

/** The state's workers as just read, in place of any shown before. */
export function withTeam(loaded: Loaded, state: string, team: WorkerView[]): Loaded {
  if (loaded.teamStates.includes(state)) return loaded
  const ids = new Set(team.map(w => w.worker.id))
  return { ...loaded, team: [...loaded.team.filter(w => !ids.has(w.worker.id)), ...team], teamStates: [...loaded.teamStates, state] }
}

/** States and districts as the API lists them; a district's wards once its state is read. A state's id is its name. */
export function geographyOf({ region, districts }: Loaded): Geography {
  const names = [...new Set(region.map(stateOfCard))].sort()
  const full = new Map(districts.map(d => [d.district.id, d]))
  return {
    source: '', retrieved_at: '', ward_note: '',
    states: names.map(name => ({
      id: name, name, source_url: '',
      districts: region.filter(d => stateOfCard(d) === name).map(d => ({
        id: d.id, name: d.name, state_id: name, onboarded: true,
        wards: (full.get(d.id)?.wards || []).map(w => ({ id: w.id, name: w.name, synthetic: false })),
      })),
    })),
  }
}

/** The API has no roles: anyone may look as the officer or as any worker, as in the demo. */
export function sessionOf({ team }: Loaded, role: Role): Session {
  return {
    role, worker_id: null, demo: true, persistent: true, assistant_ready: false, notifications_ready: false,
    workers: team.map(w => ({ id: w.worker.id, name: w.worker.name, ward_ids: [w.worker.ward_id], active_tasks: w.counts.to_visit })),
  }
}

const OUTREACH: Record<Row['call_status'], string> = { reached: 'reached', no_answer: 'unreachable', trying: 'trying', not_called: 'not_called' }
const mean = (values: (number | null)[]) => {
  const known = values.filter((v): v is number => v != null)
  return known.length ? known.reduce((a, b) => a + b, 0) / known.length : null
}

/** The part of the region in view: the officer's chosen area, or one worker's residents. */
export function overviewOf({ districts, team }: Loaded, role: Role, worker: string, scope: Scope): Overview {
  const mine = team.find(w => w.worker.id === worker)
  const chosen = districts.filter(d => role === 'crew'
    ? d.district.id === mine?.worker.district_id
    : (!scope.state_id || stateOf(d) === scope.state_id) && (!scope.district_id || d.district.id === scope.district_id))
  const wards = chosen.flatMap(d => d.wards.map(w => ({ ...w, district_id: d.district.id })))
    .filter(w => role === 'crew' ? w.id === mine?.worker.ward_id : !scope.ward_id || w.id === scope.ward_id)
  const keep = (r: Row) => role === 'crew' ? r.worker_id === worker : !scope.ward_id || r.ward_id === scope.ward_id
  const rows = chosen.flatMap(d => d.residents.filter(keep).map(r => ({ ...r, district_id: d.district.id })))

  const residents: ResidentRow[] = rows.map(r => ({
    id: r.id, name: r.name, name_hi: r.name_hi, age: r.age, language: '', district_id: r.district_id,
    ward_id: r.ward_id || '', ward_name: r.ward_name || '', case_id: r.case_id, outreach: OUTREACH[r.call_status],
    lat: r.lat, lng: r.lng, risk_score: r.risk_score, lives_alone: r.lives_alone, outdoor_worker: r.outdoor_worker,
    conditions: r.conditions, answers: {}, needs: r.needs, escalated: r.escalated, raised: r.raised, simulated: r.simulated ?? null,
    // The lists carry no call times; the case opens as the call ends, so that moment stands in for it.
    call: r.call_status === 'not_called' ? null : { id: '', attempt: r.attempts, status: r.call_status, started_at: r.opened_at, ended_at: r.opened_at, created_at: null },
  }))
  const cases: Case[] = rows.filter(r => r.case_id && r.tier).map(r => ({
    id: r.case_id!, resident_id: r.id, round_id: '', resident_name: r.name, age: r.age, language: '', risk_score: r.risk_score,
    ward_id: r.ward_id || '', ward_name: r.ward_name || '', worker_id: r.worker_id, tier: r.tier!, status: r.case_status || 'open',
    note: '', reason_ids: r.reasons, reason_labels: r.reason_labels, task_status: '', opened_at: r.opened_at, visited_at: r.visited_at, outcome: r.outcome,
    simulated_visit: Boolean(r.simulated_visit),
  }))
  const rounds = chosen.filter(d => d.warning && d.round).map(d => ({
    id: d.district.id, district_id: d.district.id, hazard: d.warning!.hazard, severity: d.warning!.severity,
    status: 'open', opened_at: d.warning!.opened_at || '', peak_temp_c: d.warning!.peak_temp_c,
  }))
  const tiers = Object.fromEntries((['red', 'amber', 'green'] as Tier[]).map(t => [t, cases.filter(c => c.tier === t).length])) as Record<Tier, number>
  const toVisit = cases.filter(c => c.tier !== 'green' && c.status === 'open').length

  return {
    scope, demo: true, has_data: rounds.length > 0 && residents.length > 0,
    as_of: chosen.map(d => d.as_of).find(Boolean) || new Date().toISOString(),
    wards: wards.map(w => {
      const inWard = rows.filter(r => r.ward_id === w.id)
      return { id: w.id, name: w.name, district_id: w.district_id, worker_id: w.worker_id, lat: mean(inWard.map(r => r.lat)), lng: mean(inWard.map(r => r.lng)) }
    }),
    facilities: team.filter(w => w.health_centre && wards.some(ward => ward.id === w.worker.ward_id))
      .map(w => ({ id: `centre-${w.worker.ward_id}`, ward_id: w.worker.ward_id, type: 'health_centre', ...w.health_centre! })),
    residents, cases, rounds, tiers,
    counts: { registered: residents.length, to_visit: toVisit },
    coverage: {}, groups: [], tasks: [], events: [], available_rounds: [],
    workers: team.map(w => ({ id: w.worker.id, name: w.worker.name, ward_ids: [w.worker.ward_id], active_tasks: w.counts.to_visit })),
  }
}

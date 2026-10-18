import { useMemo } from 'react';
import { useWorkspace } from '../components/WorkspaceContext';
import { areaName, facilityName, hindiReasons, personName, workerName } from './wardLocale';
import type { Resident, Scope } from './roleData';
import { COND, NEEDS, OUTCOMES, TELL, SAW, applyFilter, callLabel, dur, isOpen, needsVisit, riskWhy } from './roleData';

export type { ResidentFilter } from './roleData';
const minute = (value?: string | null) => value ? Date.parse(value) / 60000 : NaN;
const clock = (value: number) => Number.isFinite(value) ? new Intl.DateTimeFormat('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value * 60000)) : 'Not recorded';
const median = (values: number[]) => {
  const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
  const m = Math.floor(sorted.length / 2);
  return sorted.length ? Math.round(sorted.length % 2 ? sorted[m] : (sorted[m - 1] + sorted[m]) / 2) : null;
};

export function useRoleData() {
  const workspace = useWorkspace();
  const { data, geography, scope } = workspace;
  return useMemo(() => {
    const NOW = minute(data.as_of);
    const WARDS = data.wards.map(w => {
      const worker = data.workers.find(person => person.id === w.worker_id || person.ward_ids?.includes(w.id));
      const facility = data.facilities.find(f => f.ward_id === w.id && f.type === 'health_centre');
      return { id: w.id, name: w.name, hi: areaName(w.name, 'hi'), district: w.district_id, worker: worker?.id || '', workerName: worker?.name || 'Unassigned', workerHi: worker ? workerName(worker.name, 'hi') : 'कार्यकर्ता तय नहीं है', centre: facility?.name || 'No facility recorded', centreHi: facility ? facilityName(facility.name, 'hi') : 'केंद्र की जानकारी दर्ज नहीं है', lat: w.lat ?? 0, lng: w.lng ?? 0 };
    }).filter(w => !scope.ward_id || w.id === scope.ward_id);
    const DISTRICTS = geography.states.filter(s => !scope.state_id || s.id === scope.state_id).flatMap(s => s.districts).filter(d => !scope.district_id || d.id === scope.district_id).map(d => {
      const current = data.rounds.find(r => r.district_id === d.id);
      const ward = WARDS.find(w => w.district === d.id);
      return { id: d.id, name: d.name, onboarded: d.onboarded, lat: ward?.lat ?? 0, lng: ward?.lng ?? 0, warning: current?.severity ? { level: current.severity, hazard: current.hazard, peak: current.peak_temp_c, peakHour: 'Not recorded', opened: clock(minute(current.opened_at)) } : null, workers: WARDS.filter(w => w.district === d.id).map(w => [w.workerName, w.workerHi]) };
    });
    const RULES: Record<string, [string, string, 'red' | 'amber' | 'green']> = {};
    const RESIDENTS: (Resident & { caseId?: string; reachedAt?: number; needs?: string[] })[] = data.residents.map(r => {
      const c = data.cases.find(c => c.id === r.case_id);
      const task = data.tasks.find(t => t.case_id === r.case_id);
      const ward = WARDS.find(w => w.id === r.ward_id);
      c?.reason_ids.forEach((id, index) => { RULES[id] = [c.reason_labels[index] || id, hindiReasons[id] || c.reason_labels[index] || id, c.tier]; });
      const answers = Object.fromEntries(Object.entries(r.answers).map(([key, answer]) => [key === 'fluids_food' ? 'fluids' : key, { v: answer.value, quote: answer.quote }]));
      return {
        id: r.id, caseId: c?.id, name: r.name, hi: r.name_hi || personName(r.name, 'hi'), needs: r.needs, sex: 'unknown', age: r.age,
        alone: r.lives_alone, outdoor: r.outdoor_worker, conds: r.conditions || [], ward: r.ward_id,
        district: r.district_id, lat: r.lat ?? 0, lng: r.lng ?? 0, contact: ['Not recorded', 'रिकॉर्ड नहीं'],
        worker: c?.worker_id || ward?.worker || '', risk: r.risk_score || 0,
        call: r.call ? { status: r.outreach === 'reached' ? 'reached' : r.outreach === 'unreachable' ? 'noanswer' : 'trying', attempts: r.call.attempt, at: minute(r.call.started_at || r.call.created_at || r.call.ended_at) } : null,
        reachedAt: r.outreach === 'reached' ? minute(r.call?.ended_at) : undefined,
        answers, tier: c?.tier || null, reasons: c?.reason_ids || [], opened: minute(c?.opened_at), note: c?.note || '',
        visit: c && c.status !== 'open' ? { outcome: c.outcome || 'unknown', at: minute(c.visited_at), note: task?.note || '', simulated: Boolean(c.simulated_visit) } : null,
        esc: r.escalated ? { reason: 'other', note: '', at: minute(c?.opened_at), ack: false, reply: '' } : null,
        raised: r.raised ? { obs: 'other', at: minute(c?.opened_at) } : null, respondent: 'unknown',
        simulated: r.simulated === 'ai_resident',
      };
    });
    const inScope = (selection: Scope = {}) => RESIDENTS.filter(r => (!selection.district || r.district === selection.district) && (!selection.ward || r.ward === selection.ward) && (!selection.worker || r.worker === selection.worker));
    const waiting = (r: Resident) => Number.isFinite(r.opened) ? Math.max(0, NOW - r.opened!) : 0;
    const stats = (selection: Scope = {}) => {
      const all = inScope(selection), called = all.filter(r => r.call), cases = all.filter(r => r.tier);
      return {
        all, called, cases, reached: called.filter(r => r.call?.status === 'reached').length,
        noanswer: called.filter(r => r.call?.status === 'noanswer').length, trying: called.filter(r => r.call?.status === 'trying').length,
        tiers: { red: cases.filter(r => r.tier === 'red').length, amber: cases.filter(r => r.tier === 'amber').length, green: cases.filter(r => r.tier === 'green').length },
        need: cases.filter(needsVisit).length, visited: cases.filter(r => needsVisit(r) && r.visit).length,
        open: cases.filter(isOpen).length, redOpen: cases.filter(r => r.tier === 'red' && !r.visit).length,
        high: all.filter(r => r.risk >= 5).length, alone: all.filter(r => r.alone).length, old: all.filter(r => r.age >= 75).length,
        medianVisit: median(cases.filter(r => r.visit && needsVisit(r)).map(r => r.visit!.at - r.opened!)),
        // Calls with AI-played residents and visits no one made, for the line that says so.
        simulatedCalls: all.filter(r => r.simulated).length, simulatedVisits: all.filter(r => r.visit?.simulated).length,
      };
    };
    const progress = (selection: Scope = {}) => {
      const all = inScope(selection);
      const times = all.flatMap(r => [r.reachedAt, r.opened, r.visit?.at]).filter((n): n is number => typeof n === 'number' && Number.isFinite(n));
      const xs = [...new Set(times)].sort((a, b) => a - b);
      return { xs, reached: xs.map(t => all.filter(r => r.reachedAt != null && r.reachedAt <= t).length), opened: xs.map(t => all.filter(r => r.tier && r.opened! <= t).length), visited: xs.map(t => all.filter(r => r.visit && r.visit.at <= t).length) };
    };
    const CARRY: Record<string, string> = { water_ors: 'ors', medicine_check: 'med', cooling_centre: 'cool' };
    const needsOf = (resident: Resident & { needs?: string[] }) => (resident.needs || []).map(need => CARRY[need]).filter(Boolean);
    const open = (id: string) => { workspace.openResident(id); return RESIDENTS.find(r => r.id === id); };
    return {
      ...workspace, NOW, DISTRICTS, WARDS, RESIDENTS, RULES, COND, NEEDS, OUTCOMES, TELL, SAW,
      GUIDE: {} as Record<string, [string, string, string][]>, FORECAST: { hours: [] as number[], temp: [] as number[] },
      inScope, stats, progress, waiting, needsOf, clock, dur, isOpen, needsVisit, applyFilter, callLabel, riskWhy,
      ackEscalation: (id: string, _reply = '') => open(id), reassignWorker: (id: string, _worker: string) => open(id),
      recordVisit: (id: string, _outcome: string, _note = '') => open(id), tellOfficer: (id: string, _reason: string, _note = '') => open(id), raiseToRed: (id: string, _observation: string) => open(id),
    };
  }, [workspace, data, geography, scope]);
}

import React from 'react';
import { useWorkspaceNavigate } from '../../components/WorkspaceContext';
import { SidebarLayout } from '../../components/SidebarLayout';
import { useRoleData } from '../../lib/operationalData';
import { HorizontalBarChart } from '../../components/charts/HorizontalBarChart';
import { TierLegend } from '../../components/Legends';

export const WardOverview: React.FC = () => {
  const navigate = useWorkspaceNavigate();
  const { WARDS, NEEDS, RULES, stats, dur, waiting, needsOf, isOpen, needsVisit, worker: selectedWorker, language: lang } = useRoleData();

  // Active ASHA worker
  const churuWards = WARDS;

  if (!churuWards.length) return <SidebarLayout role="ward" narrow><p>{lang === 'hi' ? 'अभी आपके लिए कोई वार्ड तय नहीं है।' : 'No wards have been assigned to you yet.'}</p></SidebarLayout>;
  const activeWard = churuWards.find(w => w.worker === selectedWorker) || churuWards[0];
  const ws = stats({ worker: activeWard.worker });

  const todo = ws.all
    .filter(isOpen)
    .sort((a, b) => (b.tier === 'red' ? 1 : 0) - (a.tier === 'red' ? 1 : 0) || waiting(b) - waiting(a));
  const done = ws.all.filter(r => needsVisit(r) && r.visit);
  const redOpenCount = todo.filter(r => r.tier === 'red').length;

  // Carry supplies checklist calculated from resident needs
  const carry: Record<string, number> = {};
  todo.forEach(r => {
    needsOf(r).forEach(n => {
      carry[n] = (carry[n] || 0) + 1;
    });
  });

  // Reasons reported in this ward
  const reasons = Object.keys(RULES)
    .map(k => ({
      label: RULES[k][lang === 'hi' ? 1 : 0],
      n: todo.filter(r => r.reasons.includes(k)).length,
      onClick: () => navigate(`/ward/visits?reason=${k}`)
    }))
    .filter(r => r.n > 0)
    .sort((a, b) => b.n - a.n);

  const fresh = ws.all.find(r => r.test && isOpen(r));
  const longestWait = todo.length > 0 ? dur(waiting(todo[0]), lang === 'hi') : '–';

  return (
    <SidebarLayout role="ward" narrow>
      {/* Worker Switcher Header */}
      <div className="head"><div><h1>{lang === 'hi' ? 'मेरे क्षेत्र की स्थिति' : 'My area at a glance'}</h1><p>{lang === 'hi' ? 'आपके कार्य क्षेत्र के कॉल और मुलाकातें' : 'Calls and visits in your assigned area'}</p></div></div>

      {/* Fresh Test Case Alert if any */}
      {fresh && (
        <button
          type="button"
          className="rowcard rowcard-brand"
          onClick={() => navigate(`/ward/visits?resident=${fresh.id}`)}
        >
          <div className="grow">
            <b>
              {lang === 'hi' ? 'नया मामला (परीक्षण कॉल)' : 'New case (test call)'}:{' '}
              {lang === 'hi' ? fresh.hi : fresh.name}
            </b>
          </div>
          <span className="pill">{lang === 'hi' ? 'खोलें' : 'Open'}</span>
        </button>
      )}

      {/* Frontline KPIs */}
      <div className="kpis grid-2">
        <button
          type="button"
          className="kpi"
          onClick={() => navigate('/ward/visits?tab=todo')}
        >
          <b>{todo.length}</b>
          <span>{lang === 'hi' ? 'मिलना बाकी' : 'To visit'}</span>
        </button>

        <button
          type="button"
          className="kpi"
          onClick={() => navigate('/ward/visits?tab=done')}
        >
          <b>{done.length}</b>
          <span>{lang === 'hi' ? 'पूरी हुई मुलाकातें' : 'Done'}</span>
        </button>

        <button
          type="button"
          className={`kpi ${redOpenCount > 0 ? 'alert' : ''}`}
          onClick={() => navigate('/ward/visits?tier=red')}
        >
          <b>{redOpenCount}</b>
          <span>{lang === 'hi' ? 'लाल श्रेणी में मुलाकात बाकी' : 'Red, not visited'}</span>
        </button>

        <div className="kpi">
          <b className="text-dialog">{longestWait}</b>
          <span>{lang === 'hi' ? 'सबसे लंबा इंतज़ार' : 'Longest wait'}</span>
        </div>
      </div>

      <TierLegend className="mt-1" />

      {/* Carry Today Checklist */}
      <section className="card">
        <header>
          <h2>{lang === 'hi' ? 'आज साथ ले जाएँ' : 'What to bring'}</h2>
          <p>
            {lang === 'hi'
              ? 'कॉल में बताई गई ज़रूरतों के आधार पर। निकलने से पहले सामान जाँच लें।'
              : 'Based on reported needs. Check supplies before setting out.'}
          </p>
        </header>
        {Object.keys(carry).length > 0 ? (
          <div className="supplies-grid">
            {Object.keys(carry).map(k => (
              <div key={k} className="supply-item-card">
                <span className="supply-name">{NEEDS[k] ? NEEDS[k][lang === 'hi' ? 1 : 0] : k}</span>
                <span className="supply-count">× {carry[k]}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="muted">
            {lang === 'hi' ? 'सामान की कोई अतिरिक्त ज़रूरत दर्ज नहीं है।' : 'No supply needs recorded'}
          </p>
        )}
      </section>

      {/* Problems Reported in Ward */}
      <section className="card">
        <header>
          <h2>{lang === 'hi' ? 'मिलने की वजहें' : 'Reasons for a visit'}</h2>
          <p>{lang === 'hi' ? 'किसी वजह पर दबाएँ, उससे जुड़े निवासियों की सूची खुलेगी।' : 'Tap a bar to see those residents'}</p>
        </header>
        {reasons.length > 0 ? (
          <HorizontalBarChart rows={reasons} />
        ) : (
          <p className="muted">
            {lang === 'hi' ? 'इस समय कोई मुलाकात बाकी नहीं है।' : 'No visits are waiting right now.'}
          </p>
        )}
      </section>

      {/* Morning Progress Bar */}
      <section className="card">
        <div className="kv">
          <span>
            {lang === 'hi' ? `${done.length + todo.length} में से ${done.length} ${done.length === 1 ? 'मुलाकात पूरी हुई' : 'मुलाकातें पूरी हुईं'}` : `${done.length} of ${done.length + todo.length} visits completed`}
          </span>
          <b>{Math.round((done.length / Math.max(1, done.length + todo.length)) * 100)}%</b>
        </div>
        <div className="progress-track" role="img" aria-label={`${Math.round((done.length / Math.max(1, done.length + todo.length)) * 100)}% of visits completed`}>
          <div className="progress-fill" style={{ width: `${(done.length / Math.max(1, done.length + todo.length)) * 100}%` }} />
        </div>
      </section>
    </SidebarLayout>
  );
};

export default WardOverview;

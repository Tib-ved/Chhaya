import React, { useState } from 'react';
import { useWorkspaceSearchParams } from '../../components/WorkspaceContext';
import { SidebarLayout } from '../../components/SidebarLayout';
import { useRoleData } from '../../lib/operationalData';
import { noteText, statusLabel } from '../../lib/wardLocale';
import { SimulatedPills } from '../../components/HeatPanel';
import { tierLabel } from '../../lib/labels';
import { TierLegend } from '../../components/Legends';
import { CoordinateLink } from '../../components/CoordinateLink';

export const WardVisits: React.FC = () => {
  const [searchParams, setSearchParams] = useWorkspaceSearchParams();
  const { WARDS, RESIDENTS, COND, RULES, GUIDE, NEEDS, OUTCOMES, TELL, SAW, dur, waiting, clock, needsOf, isOpen, needsVisit, recordVisit, tellOfficer, raiseToRed, applyFilter, worker: selectedWorker, language: lang } = useRoleData();

  // Active ASHA worker
  const churuWards = WARDS;

  const tab = searchParams.get('tab') || 'todo';
  const filterTier = searchParams.get('tier') || undefined;
  const filterReason = searchParams.get('reason') || undefined;
  const activeResidentId = searchParams.get('resident');

  // Action drawer panel state: 'visit' | 'tell' | 'raise' | null
  const [activePanel, setActivePanel] = useState<'visit' | 'tell' | 'raise' | null>(null);
  const [selectedOutcome, setSelectedOutcome] = useState('advised_ok');
  const [selectedTellReason, setSelectedTellReason] = useState('supplies');
  const [selectedSawObservation, setSelectedSawObservation] = useState('confused');
  const [panelNote, setPanelNote] = useState('');
  const [, setRefresh] = useState(0);

  if (!churuWards.length) return <SidebarLayout role="ward" narrow><p>{lang === 'hi' ? 'अभी आपके लिए कोई वार्ड तय नहीं है।' : 'No wards have been assigned to you yet.'}</p></SidebarLayout>;
  const activeWard = churuWards.find(w => w.worker === selectedWorker) || churuWards[0];
  const myResidents = RESIDENTS.filter(r => r.worker === activeWard.worker);

  const todoCount = myResidents.filter(isOpen).length;
  const doneCount = myResidents.filter(r => needsVisit(r) && r.visit).length;
  const allCount = myResidents.length;

  // Tab filtering
  let list =
    tab === 'done'
      ? myResidents.filter(r => needsVisit(r) && r.visit)
      : tab === 'all'
      ? myResidents
      : myResidents.filter(isOpen);

  list = applyFilter(list, {
    tier: filterTier,
    reason: filterReason
  }).sort(
    (a, b) =>
      (b.tier === 'red' ? 1 : 0) - (a.tier === 'red' ? 1 : 0) ||
      waiting(b) - waiting(a)
  );

  const activeResident = RESIDENTS.find(r => r.id === activeResidentId);

  // Doorstep guidance items for active resident
  const guideItems: [string, string, string][] = [];
  if (activeResident) {
    const seen = new Set<string>();
    activeResident.reasons.forEach(rId => {
      (GUIDE[rId] || []).forEach(g => {
        if (!seen.has(g[1])) {
          seen.add(g[1]);
          guideItems.push(g);
        }
      });
    });
    guideItems.sort((a, b) => (a[0] === 'danger' ? -1 : b[0] === 'danger' ? 1 : 0));
  }

  const handleSaveVisit = () => {
    if (!activeResident) return;
    recordVisit(activeResident.id, selectedOutcome, panelNote);
    setActivePanel(null);
    setPanelNote('');
    setRefresh(r => r + 1);
  };

  const handleSaveTell = () => {
    if (!activeResident) return;
    tellOfficer(activeResident.id, selectedTellReason, panelNote);
    setActivePanel(null);
    setPanelNote('');
    setRefresh(r => r + 1);
  };

  const handleSaveRaise = () => {
    if (!activeResident) return;
    raiseToRed(activeResident.id, selectedSawObservation);
    setActivePanel(null);
    setRefresh(r => r + 1);
  };

  const KIND_LABEL: Record<string, [string, string]> = {
    check: ['Check', 'जाँचें'],
    ask: ['Ask', 'पूछें'],
    bring: ['Bring', 'साथ ले जाएँ'],
    danger: ['Urgent', 'तुरंत']
  };

  return (
    <SidebarLayout role="ward" narrow>
      {/* 1. If Resident Detail Open */}
      {activeResident ? (
        <div className="stack">
          <button
            type="button"
            className="linkbtn self-start"
            onClick={() => {
              const next = new URLSearchParams(searchParams);
              next.delete('resident');
              setSearchParams(next);
              setActivePanel(null);
            }}
          >
            {lang === 'hi' ? 'सूची पर वापस' : 'Back to the visit list'}
          </button>

          {/* Resident Header Card */}
          <section className="card">
            <div className="chips">
              {activeResident.tier && (
                <span className={`pill ${activeResident.tier}`}>
                  <i className={`dot ${activeResident.tier}`} />
                  {lang === 'hi' ? statusLabel(activeResident.tier, lang) : `${tierLabel(activeResident.tier)} tier`}
                </span>
              )}
              {activeResident.test && <span className="pill tag">{lang === 'hi' ? 'टेस्ट कॉल' : 'Test call'}</span>}
              <SimulatedPills call={activeResident.simulated} visit={activeResident.visit?.simulated} hi={lang === 'hi'} />
              {isOpen(activeResident) && (
                <span className="muted text-small">
                  {lang === 'hi' ? 'इंतज़ार' : 'Waiting'} {dur(waiting(activeResident), lang === 'hi')}
                </span>
              )}
            </div>

            <div className="flex-between items-baseline gap-2">
              <h1 className={`text-headline ${activeResident.simulated ? 'simulated-name' : ''}`}>
                {lang === 'hi' ? activeResident.hi : activeResident.name}, {activeResident.age}
              </h1>
              {activeResident.lat != null && activeResident.lng != null && (
                <CoordinateLink lat={activeResident.lat} lng={activeResident.lng} />
              )}
            </div>

            <div className="chips">
              {activeResident.alone && (
                <span className="pill">{lang === 'hi' ? 'अकेले रहते हैं' : 'Lives alone'}</span>
              )}
              {activeResident.outdoor && (
                <span className="pill">{lang === 'hi' ? 'बाहर काम करते हैं' : 'Works outdoors'}</span>
              )}
              {activeResident.conds.map(c => (
                <span key={c} className="pill">
                  {COND[c] ? COND[c][lang === 'hi' ? 1 : 0] : c}
                </span>
              ))}
            </div>

            {/* Quick Action Buttons */}
            <div className="visit-quick-actions">
              <button
                type="button"
                className="btn ghost placeholder text-small"
                disabled
                title="Synthetic numbers in prototype"
              >
                {lang === 'hi' ? 'फ़ोन' : 'Call'}
              </button>
              <button
                type="button"
                className="btn ghost placeholder text-small"
                disabled
                title="Synthetic numbers in prototype"
              >
                {lang === 'hi' ? activeResident.contact[1] : activeResident.contact[0]}
              </button>
              <a
                href={`https://www.google.com/maps/dir/?api=1&destination=${activeResident.lat},${activeResident.lng}`}
                target="_blank"
                rel="noopener noreferrer"
                className="btn compact text-small no-underline"
              >
                {lang === 'hi' ? 'रास्ता' : 'Directions'}
              </a>
            </div>
          </section>

          {/* Emergency Escalation Banner if raised */}
          {activeResident.raised && (
            <div className="carebox">
              <b className="red-ink">
                {lang === 'hi' ? 'अब लाल स्तर। अधिकारी को सूचना दे दी गई है।' : 'Now Red. The officer has been told.'}
              </b>
              <h2>{lang === 'hi' ? 'अभी इलाज तक पहुँचाएँ' : 'Get them to medical care now'}</h2>
              <div className="kv">
                <span>{lang === 'hi' ? 'नज़दीकी शीतल केंद्र' : 'Nearest health centre'}</span>
                <b>{lang === 'hi' ? activeWard.centreHi : activeWard.centre}</b>
              </div>
              <a
                href={`https://www.google.com/maps/dir/?api=1&destination=${activeWard.lat},${activeWard.lng}`}
                target="_blank"
                rel="noopener noreferrer"
                className="btn danger"
              >
                {lang === 'hi' ? 'स्वास्थ्य केंद्र का रास्ता' : 'Directions to the health centre'}
              </a>
              <b className="text-title red-ink">
                {lang === 'hi' ? 'एम्बुलेंस: 108 डायल करें' : 'Ambulance: dial 108'}
              </b>
            </div>
          )}

          {/* Escalation to Officer status */}
          {activeResident.esc && (
            <div className="escbox">
              <b>
                {lang === 'hi' ? 'अधिकारी के पास' : 'With the officer'}:{' '}
                {TELL.find(t => t[0] === activeResident.esc?.reason)?.[lang === 'hi' ? 2 : 1] ||
                  activeResident.esc.reason}
              </b>
              {activeResident.esc.ack ? (
                <span>
                  {lang === 'hi' ? 'अधिकारी ने देख लिया है' : 'Officer has acknowledged'}
                  {activeResident.esc.reply ? `: “${activeResident.esc.reply}”` : ''}
                </span>
              ) : (
                <span className="muted">
                  {lang === 'hi' ? 'अधिकारी के जवाब का इंतज़ार है' : 'Awaiting officer response'}
                </span>
              )}
            </div>
          )}

          {/* Already Visited Card */}
          {activeResident.visit && (
            <section className="card edge-green">
              <header>
                <h2 className="green-ink">
                  {lang === 'hi' ? 'मुलाक़ात दर्ज हो चुकी है' : 'Visit recorded'}
                </h2>
                <span>{clock(activeResident.visit.at)}</span>
              </header>
              {activeResident.visit.simulated && <span className="pill blue self-start">{lang === 'hi' ? 'सिम्युलेटेड मुलाक़ात' : 'Simulated visit'}</span>}
              <b>
                {OUTCOMES.find(o => o[0] === activeResident.visit?.outcome)?.[lang === 'hi' ? 2 : 1] ||
                  activeResident.visit.outcome}
              </b>
              {activeResident.visit.note && <p>“{activeResident.visit.note}”</p>}
            </section>
          )}

          {/* Supplies to Carry */}
          {needsOf(activeResident).length > 0 && (
            <section className="card">
              <header>
                <h2>{lang === 'hi' ? 'साथ ले जाएँ' : 'Supplies to carry'}</h2>
              </header>
              <div className="chips">
                {needsOf(activeResident).map(n => (
                  <span key={n} className="pill amber">
                    {NEEDS[n] ? NEEDS[n][lang === 'hi' ? 1 : 0] : n}
                  </span>
                ))}
              </div>
            </section>
          )}

          {/* Doorstep Guidance from Ministry of Health */}
          {guideItems.length > 0 && (
            <section className="card edge-brand">
              <header>
                <h2>{lang === 'hi' ? 'दरवाज़े पर क्या करें' : 'At the doorstep'}</h2>
              </header>
              <ul className="guide">
                {guideItems.map((g, i) => (
                  <li key={i}>
                    <span className={`gk ${g[0]}`}>
                      {KIND_LABEL[g[0]] ? KIND_LABEL[g[0]][lang === 'hi' ? 1 : 0] : g[0]}
                    </span>
                    <span className="text-body">{g[lang === 'hi' ? 2 : 1]}</span>
                  </li>
                ))}
              </ul>
              <p className="muted text-small mt-2">
                {lang === 'hi'
                  ? 'सरकारी सलाह से तय पाठ। AI ने नहीं लिखा।'
                  : 'Fixed text from official Ministry of Health heatwave protocol.'}
              </p>
            </section>
          )}

          {/* What They Said on Phone */}
          {activeResident.answers && (
            <section className="card">
              <header>
                <h2>{lang === 'hi' ? 'उन्होंने क्या कहा' : 'What they reported'}</h2>
              </header>
              {Object.keys(activeResident.answers).map(q => {
                const ans = activeResident.answers![q];
                return (
                  <div key={q} className="kv kv-col">
                    <div className="kv-row">
                      <span className="text-small">{q[0].toUpperCase() + q.slice(1)}</span>
                      <b className="text-small">{ans.v}</b>
                    </div>
                    <div className="quote">“{ans.quote}”</div>
                  </div>
                );
              })}
              {activeResident.note && (
                <div className="note mt-2">
                  <small>{lang === 'hi' ? 'कॉल का सार · मॉडल ने लिखा, जवाबों से मिलाएँ' : 'Call summary · written by a model, check it against the answers'}</small>
                  {noteText(activeResident.note, lang).text}
                </div>
              )}
            </section>
          )}

          {/* Interactive Panels: Record Visit / Tell Officer / Raise to Red */}
          {activePanel === 'visit' && (
            <section className="card edge-brand">
              <h2>{lang === 'hi' ? 'मुलाक़ात दर्ज करें' : 'Record the visit'}</h2>
              <span className="lab">{lang === 'hi' ? 'मुलाक़ात का नतीजा' : 'Outcome of the visit'}</span>
              <div className="opts">
                {OUTCOMES.map(o => (
                  <button
                    key={o[0]}
                    type="button"
                    className={`opt ${selectedOutcome === o[0] ? 'selected' : ''}`}
                    onClick={() => setSelectedOutcome(o[0])}
                  >
                    {o[lang === 'hi' ? 2 : 1]}
                  </button>
                ))}
              </div>
              <div>
                <label className="lab" htmlFor="visit-note-text">
                  {lang === 'hi' ? 'नोट (ज़रूरी नहीं)' : 'Note (optional)'}
                </label>
                <textarea
                  id="visit-note-text"
                  className="field"
                  rows={2}
                  maxLength={200}
                  value={panelNote}
                  onChange={e => setPanelNote(e.target.value)}
                />
              </div>
              <button type="button" className="btn big" onClick={handleSaveVisit}>
                {lang === 'hi' ? 'सहेजें' : 'Save visit'}
              </button>
              <button type="button" className="linkbtn" onClick={() => setActivePanel(null)}>
                {lang === 'hi' ? 'रद्द करें' : 'Cancel'}
              </button>
            </section>
          )}

          {activePanel === 'tell' && (
            <section className="card edge-blue">
              <h2>{lang === 'hi' ? 'अधिकारी को बताएँ' : 'Tell the officer'}</h2>
              <span className="lab">{lang === 'hi' ? 'कारण' : 'Reason'}</span>
              <div className="opts">
                {TELL.map(t => (
                  <button
                    key={t[0]}
                    type="button"
                    className={`opt ${selectedTellReason === t[0] ? 'selected' : ''}`}
                    onClick={() => setSelectedTellReason(t[0])}
                  >
                    {t[lang === 'hi' ? 2 : 1]}
                  </button>
                ))}
              </div>
              <div>
                <label className="lab" htmlFor="tell-note-text">
                  {lang === 'hi' ? 'संदेश' : 'Message'}
                </label>
                <textarea
                  id="tell-note-text"
                  className="field"
                  rows={2}
                  maxLength={200}
                  value={panelNote}
                  onChange={e => setPanelNote(e.target.value)}
                />
              </div>
              <button type="button" className="btn blue big" onClick={handleSaveTell}>
                {lang === 'hi' ? 'अधिकारी को भेजें' : 'Send to the officer'}
              </button>
              <button type="button" className="linkbtn" onClick={() => setActivePanel(null)}>
                {lang === 'hi' ? 'रद्द करें' : 'Cancel'}
              </button>
            </section>
          )}

          {activePanel === 'raise' && (
            <section className="card edge-red">
              <h2>{lang === 'hi' ? 'हालत ख़राब है: लाल करें' : 'Raise the case to Red'}</h2>
              <span className="lab">{lang === 'hi' ? 'आपने क्या देखा?' : 'What did you observe?'}</span>
              <div className="opts">
                {SAW.map(s => (
                  <button
                    key={s[0]}
                    type="button"
                    className={`opt ${selectedSawObservation === s[0] ? 'selected' : ''}`}
                    onClick={() => setSelectedSawObservation(s[0])}
                  >
                    {s[lang === 'hi' ? 2 : 1]}
                  </button>
                ))}
              </div>
              <button type="button" className="btn danger big" onClick={handleSaveRaise}>
                {lang === 'hi' ? 'लाल करें' : 'Raise to Red'}
              </button>
              <button type="button" className="linkbtn" onClick={() => setActivePanel(null)}>
                {lang === 'hi' ? 'रद्द करें' : 'Cancel'}
              </button>
            </section>
          )}

          {/* Action Trigger Buttons when no panel active */}
          {!activePanel && isOpen(activeResident) && (
            <div className="stack-sm">
              <button
                type="button"
                className="btn big"
                onClick={() => setActivePanel('visit')}
              >
                {lang === 'hi' ? 'मुलाक़ात दर्ज करें' : 'Record the visit'}
              </button>
              <button
                type="button"
                className="btn ghost big blue-ghost"
                onClick={() => setActivePanel('tell')}
              >
                {lang === 'hi' ? 'अधिकारी को बताएँ' : 'Tell the officer'}
              </button>
              {activeResident.tier === 'amber' && (
                <button
                  type="button"
                  className="btn danger big"
                  onClick={() => setActivePanel('raise')}
                >
                  {lang === 'hi' ? 'हालत ज़्यादा ख़राब है: लाल करें' : 'Worse: raise to Red'}
                </button>
              )}
            </div>
          )}
        </div>
      ) : (
        /* 2. Visit List Screen */
        <div className="stack">
          {/* Segmented Queue Tab Selector */}
          <div className="ward-segmented-tabs" role="group" aria-label="Visit queues">
            <button
              type="button"
              className={`ward-tab-btn ${tab === 'todo' ? 'active' : ''}`}
              onClick={() => {
                const next = new URLSearchParams(searchParams);
                next.set('tab', 'todo');
                setSearchParams(next);
              }}
            >
              <span>{lang === 'hi' ? 'मिलना बाकी' : 'To visit'}</span>
              <span className="ward-tab-badge">{todoCount}</span>
            </button>
            <button
              type="button"
              className={`ward-tab-btn ${tab === 'done' ? 'active' : ''}`}
              onClick={() => {
                const next = new URLSearchParams(searchParams);
                next.set('tab', 'done');
                setSearchParams(next);
              }}
            >
              <span>{lang === 'hi' ? 'पूरी हुईं' : 'Done'}</span>
              <span className="ward-tab-badge">{doneCount}</span>
            </button>
            <button
              type="button"
              className={`ward-tab-btn ${tab === 'all' ? 'active' : ''}`}
              onClick={() => {
                const next = new URLSearchParams(searchParams);
                next.set('tab', 'all');
                setSearchParams(next);
              }}
            >
              <span>{lang === 'hi' ? 'सभी' : 'Everyone'}</span>
              <span className="ward-tab-badge">{allCount}</span>
            </button>
          </div>

          <TierLegend className="mt-1" />

          {/* Filter Chips if any */}
          {(filterTier || filterReason) && (
            <div className="chips">
              {filterTier && (
                <button
                  type="button"
                  className="chip"
                  onClick={() => {
                    const next = new URLSearchParams(searchParams);
                    next.delete('tier');
                    setSearchParams(next);
                  }}
                >
                  {lang === 'hi' ? statusLabel(filterTier, lang) : tierLabel(filterTier)} ✕
                </button>
              )}
              {filterReason && (
                <button
                  type="button"
                  className="chip"
                  onClick={() => {
                    const next = new URLSearchParams(searchParams);
                    next.delete('reason');
                    setSearchParams(next);
                  }}
                >
                  {RULES[filterReason]?.[lang === 'hi' ? 1 : 0]} ✕
                </button>
              )}
            </div>
          )}

          {/* Cards List */}
          {list.length === 0 ? (
            <p className="muted empty-centred">
              {lang === 'hi'
                ? 'इस सूची में अभी कोई मुलाकात बाकी नहीं है।'
                : 'Nobody needs a visit right now.'}
            </p>
          ) : (
            <div className="stack">
              {list.map(r => (
                <div
                  key={r.id}
                  className="card visit-card"
                >
                  <div className="flex-between items-baseline gap-2">
                    <b className={`text-title ${r.simulated ? 'simulated-name' : ''}`}>
                      {lang === 'hi' ? r.hi : r.name}, {r.age}
                    </b>
                    {r.lat != null && r.lng != null && (
                      <CoordinateLink lat={r.lat} lng={r.lng} />
                    )}
                  </div>

                  <span className="text-body ink-2">
                    {r.reasons.map(x => RULES[x]?.[lang === 'hi' ? 1 : 0]).join(' · ') ||
                      (lang === 'hi' ? 'कोई चिंता नहीं बताई' : 'No concern raised')}
                  </span>

                  <span className="muted text-small">
                    {r.visit
                      ? `${lang === 'hi' ? 'मुलाक़ात' : 'Visited'} ${clock(r.visit.at)}`
                      : isOpen(r)
                      ? `${lang === 'hi' ? 'इंतज़ार' : 'Waiting'} ${dur(waiting(r), lang === 'hi')}`
                      : ''}
                  </span>

                  {/* All tags positioned below name and visit in an even manner */}
                  <div className="ops-resident-badges mt-1">
                    {r.tier && (
                      <span className={`pill ${r.tier}`}>
                        <i className={`dot ${r.tier}`} />
                        {lang === 'hi' ? statusLabel(r.tier, lang) : tierLabel(r.tier)}
                      </span>
                    )}
                    {r.test && <span className="pill tag">{lang === 'hi' ? 'टेस्ट कॉल' : 'Test call'}</span>}
                    <SimulatedPills call={r.simulated} visit={r.visit?.simulated} hi={lang === 'hi'} />
                    {r.esc && !r.esc.ack && (
                      <span className="pill blue">
                        {lang === 'hi' ? 'अधिकारी के पास' : 'With the officer'}
                      </span>
                    )}
                    {needsOf(r).map(n => (
                      <span key={n} className="pill amber">
                        {NEEDS[n] ? NEEDS[n][lang === 'hi' ? 1 : 0] : n}
                      </span>
                    ))}
                  </div>

                  <div className="row-actions">
                    <button
                      type="button"
                      className="btn flex-1"
                      onClick={() => {
                        const next = new URLSearchParams(searchParams);
                        next.set('resident', r.id);
                        setSearchParams(next);
                      }}
                    >
                      {lang === 'hi' ? 'खोलें' : 'Open'}
                    </button>
                    <a
                      href={`https://www.google.com/maps/dir/?api=1&destination=${r.lat},${r.lng}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="btn ghost flex-1 no-underline text-center"
                    >
                      {lang === 'hi' ? 'रास्ता' : 'Directions'}
                    </a>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </SidebarLayout>
  );
};

export default WardVisits;

import React from 'react';
import { SidebarLayout } from '../../components/SidebarLayout';
import { useRoleData } from '../../lib/operationalData';
import { facilityName, personName, statusLabel, text } from '../../lib/wardLocale';
import { CoordinateLink } from '../../components/CoordinateLink';
import { TierLegend } from '../../components/Legends';

export const WardMap: React.FC = () => {
  const { WARDS, RESIDENTS, worker, openResident, data, language } = useRoleData();
  const t = (english: string, hindi: string) => text(language, english, hindi);
  const activeWard = WARDS.find(w => w.worker === worker) || WARDS[0];
  if (!activeWard) return <SidebarLayout role="ward" narrow><p>{t('No wards have been assigned to you yet.', 'अभी आपके लिए कोई वार्ड तय नहीं है।')}</p></SidebarLayout>;
  const residents = RESIDENTS.filter(r => r.worker === activeWard.worker && data.residents.some(record => record.id === r.id && record.lat != null && record.lng != null));
  const facilities = data.facilities.filter(f => f.ward_id === activeWard.id && f.lat != null && f.lng != null);
  const health = facilities.find(f => f.type === 'health_centre');
  const cooling = facilities.find(f => f.type === 'cooling_centre');

  // SVG coordinate projection helper around ward center
  const cx = activeWard.lng, cy = activeWard.lat, W = 600, H = 400;
  const points = [...residents, ...facilities];
  const maxLng = Math.max(.002, ...points.map(p => Math.abs(p.lng! - cx)));
  const maxLat = Math.max(.002, ...points.map(p => Math.abs(p.lat! - cy)));
  const toX = (lng: number) => W / 2 + (lng - cx) / maxLng * (W / 2 - 50);
  const toY = (lat: number) => H / 2 - (lat - cy) / maxLat * (H / 2 - 50);

  return <SidebarLayout role="ward" narrow>
    <div className="head">
      <div>
        <h1>{language === 'hi' ? `${activeWard.hi}: नक्शा और केंद्र` : `${activeWard.name} map and facilities`}</h1>
        <p>{t('This sketch shows locations from the register, not roads or routes. Resident locations are fictional; ward points are approximate.', 'यह नक्शा रजिस्टर में दर्ज जगहें दिखाता है। इसमें सड़कें या रास्ते नहीं हैं। निवासियों की जगहें काल्पनिक हैं; वार्ड के बिंदु अनुमानित हैं।')}</p>
      </div>
    </div>

    {/* Interactive SVG Field Map */}
    <section className="card">
      <header>
        <h2>{t('Residents and nearby facilities', 'निवासी और आसपास के केंद्र')}</h2>
        <CoordinateLink
          lat={cy}
          lng={cx}
          label={language === 'hi' ? `वार्ड का दर्ज स्थान: ${cy.toFixed(4)}, ${cx.toFixed(4)}` : `Ward centre: ${cy.toFixed(4)}, ${cx.toFixed(4)}`}
        />
      </header>
      <div className="map-frame">
        <svg viewBox={`0 0 ${W} ${H}`} role="group" aria-label={t('Resident and facility locations', 'निवासियों और केंद्रों की जगहें')} className="map-svg">
          {/* Grid Lines */}
          <line x1="0" y1={H / 2} x2={W} y2={H / 2} stroke="var(--line)" strokeDasharray="4 4" />
          <line x1={W / 2} y1="0" x2={W / 2} y2={H} stroke="var(--line)" strokeDasharray="4 4" />
          {/* Health Centre Central Pin */}
          {health && <g><rect x={toX(health.lng!) - 9} y={toY(health.lat!) - 9} width="18" height="18" fill="var(--blue)" /><text x={toX(health.lng!)} y={toY(health.lat!) + 26} textAnchor="middle" fontSize="12" fill="var(--ink)">{t('PHC', 'स्वास्थ्य केंद्र')}</text><title>{facilityName(health.name, language)}</title></g>}
          {/* Cooling Centre Pin */}
          {cooling && <g><rect x={toX(cooling.lng!) - 8} y={toY(cooling.lat!) - 8} width="16" height="16" fill="var(--green)" transform={`rotate(45 ${toX(cooling.lng!)} ${toY(cooling.lat!)})`} /><text x={toX(cooling.lng!)} y={toY(cooling.lat!) + 26} textAnchor="middle" fontSize="12" fill="var(--ink)">{t('Cooling centre', 'शीतल केंद्र')}</text><title>{facilityName(cooling.name, language)}</title></g>}
          {/* Resident Pins */}
          {residents.map(r => <g key={r.id} tabIndex={0} role="button" aria-label={language === 'hi' ? `${personName(r.name, language)} का विवरण खोलें` : `Open resident ${r.name}`} className="map-pin" onClick={() => openResident(r.id)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openResident(r.id); } }}>
            <circle cx={toX(r.lng)} cy={toY(r.lat)} r="11" fill={r.visit ? 'var(--grey-mark)' : r.tier ? `var(--${r.tier})` : 'var(--grey-mark)'} stroke="white" strokeWidth="2" />
            <text x={toX(r.lng)} y={toY(r.lat) - 16} textAnchor="middle" fontSize="12" fill="var(--ink)">{personName(r.name, language).split(' ')[0]}</text><title>{personName(r.name, language)} · {r.tier ? statusLabel(r.tier, language) : t('Not assessed', 'प्राथमिकता तय नहीं')}</title>
          </g>)}
        </svg>
      </div>

      {/* Legend */}
      <div className="map-legend-bar">
        <TierLegend />
        <div className="map-legend-facilities">
          <span><span className="legend-symbol symbol-square" /> {t('PHC (primary health centre)', 'स्वास्थ्य केंद्र')}</span>
          <span><span className="legend-symbol symbol-diamond" /> {t('Cooling centre', 'शीतल केंद्र')}</span>
          <span><span className="legend-symbol symbol-circle-gray" /> {t('Visited or unassessed', 'मुलाकात दर्ज या अन्य')}</span>
        </div>
      </div>
    </section>

    {/* Selected Resident Card Drawer */}
    <section className="card">
      <header>
        <h2>{t('Residents on this map', 'इस नक्शे पर निवासी')}</h2>
        <p>{t('Choose a resident to read their call and check the visit status.', 'कॉल और मुलाकात का विवरण देखने के लिए निवासी चुनें।')}</p>
      </header>
      <div className="chips">
        {residents.map(r => (
          <button
            type="button"
            className={`chip ${r.simulated ? 'simulated-chip' : ''}`}
            key={r.id}
            onClick={() => openResident(r.id)}
          >
            {personName(r.name, language)}
          </button>
        ))}
      </div>
    </section>

    {/* Facilities & Cooling Centre List */}
    <section className="card">
      <header>
        <h2>{t('Recorded facilities', 'रजिस्टर में दर्ज केंद्र')}</h2>
      </header>
      {!facilities.length && <p className="muted">{t('No facility locations recorded.', 'किसी केंद्र की जगह दर्ज नहीं है।')}</p>}
      {facilities.map(f => (
        <div className="kv" key={f.id}>
          <div>
            <b>{facilityName(f.name, language)}</b>
            <p className="muted">{t('Hours and stock are not recorded. Confirm before travel.', 'खुलने का समय और उपलब्ध सामान दर्ज नहीं हैं। जाने से पहले पता कर लें।')}</p>
            {f.lat != null && f.lng != null && (
              <CoordinateLink lat={f.lat} lng={f.lng} className="mt-1" />
            )}
          </div>
          <a
            href={`https://www.google.com/maps/dir/?api=1&destination=${f.lat},${f.lng}`}
            target="_blank"
            rel="noopener noreferrer"
            className="btn ghost no-underline"
          >
            {t('Directions', 'रास्ता देखें')}
          </a>
        </div>
      ))}
    </section>
  </SidebarLayout>;
};

export default WardMap;

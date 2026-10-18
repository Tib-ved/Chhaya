import React, { useEffect, useRef, useState } from 'react';
import type { ResidentRow } from '../lib/roleApi';
import { TierLegend, RiskScoreLegend } from './Legends';
import { tierLabel } from '../lib/labels';
import { CoordinateLink } from './CoordinateLink';

interface OverviewDrilldownDialogProps {
  title: string;
  description: string;
  residents: ResidentRow[];
  onClose: () => void;
  onOpenResident: (id: string) => void;
}

export const OverviewDrilldownDialog: React.FC<OverviewDrilldownDialogProps> = ({
  title,
  description,
  residents,
  onClose,
  onOpenResident,
}) => {
  const dialog = useRef<HTMLDialogElement>(null);
  const [filterText, setFilterText] = useState('');

  useEffect(() => {
    dialog.current?.showModal();
  }, []);

  const filtered = residents.filter(r => {
    if (!filterText.trim()) return true;
    const query = filterText.toLowerCase();
    return (
      r.name.toLowerCase().includes(query) ||
      (r.ward_name && r.ward_name.toLowerCase().includes(query)) ||
      (r.tier && tierLabel(r.tier).toLowerCase().includes(query)) ||
      r.reasons.some(reason => reason.toLowerCase().includes(query)) ||
      r.reason_labels.some(label => label.toLowerCase().includes(query))
    );
  });

  return (
    <dialog
      ref={dialog}
      className="ops-dialog ops-drilldown-dialog"
      aria-labelledby="drilldown-title"
      onCancel={onClose}
      onClick={e => {
        if (e.target === dialog.current) onClose();
      }}
    >
      <div className="ops-drilldown-header">
        <div className="ops-drilldown-title-group">
          <h2 id="drilldown-title">
            {title} ({residents.length})
          </h2>
          <p>{description}</p>
        </div>
        <button
          type="button"
          className="ops-close"
          onClick={onClose}
          aria-label="Close dialog"
        >
          Close
        </button>
      </div>

      <div className="ops-drilldown-body">
        <div className="ops-drilldown-meta">
          <div className="chips">
            <TierLegend />
            <RiskScoreLegend />
          </div>
          <input
            type="search"
            className="input-search"
            placeholder="Search in this list…"
            value={filterText}
            onChange={e => setFilterText(e.target.value)}
            style={{ width: '220px', height: '36px', minHeight: '36px', padding: '6px 12px', fontSize: '13px' }}
          />
        </div>

        {filtered.length === 0 ? (
          <div className="ops-drilldown-empty">
            <p>{filterText ? 'No residents match your search filter.' : 'No residents in this list.'}</p>
          </div>
        ) : (
          <div className="ops-table-wrap">
            <table className="ops-table">
              <thead>
                <tr>
                  <th>Resident</th>
                  <th className="num">Age</th>
                  <th>Ward</th>
                  <th className="num">Risk</th>
                  <th>Location</th>
                  <th>Call</th>
                  <th>Tier</th>
                  <th>Rule matched</th>
                  <th style={{ textAlign: 'right' }}>Action</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map(r => {
                  const reasonText = r.reason_labels?.length
                    ? r.reason_labels.join(' · ')
                    : r.reasons.join(' · ') || (r.tier === 'green' ? 'No concern raised' : '–');
                  const callText = r.call_status === 'no_answer'
                    ? `No answer (${r.attempts || 3} attempts)`
                    : r.call_status === 'reached'
                    ? 'Reached'
                    : r.call_status === 'trying'
                    ? 'Trying'
                    : 'Not called';

                  return (
                    <tr
                      key={r.id}
                      className="interactive"
                      onClick={() => onOpenResident(r.id)}
                    >
                      <td title={r.name}>
                        <div className="ops-resident-cell">
                          <button
                            type="button"
                            className={`ops-resident-name-btn ${r.simulated ? 'simulated' : ''}`}
                            title={r.name}
                            onClick={e => {
                              e.stopPropagation();
                              onOpenResident(r.id);
                            }}
                          >
                            {r.name}
                          </button>
                          <div className="ops-resident-badges">
                            {r.escalated && <span className="pill blue">Flagged</span>}
                            {r.simulated && <span className="pill blue">Simulated</span>}
                          </div>
                        </div>
                      </td>
                      <td className="num">{r.age}</td>
                      <td className="cell-truncate" title={r.ward_name || '–'}>
                        {r.ward_name || '–'}
                      </td>
                      <td className="num">{r.risk_score}</td>
                      <td>
                        {r.lat != null && r.lng != null ? (
                          <CoordinateLink lat={r.lat} lng={r.lng} />
                        ) : (
                          <span className="muted">–</span>
                        )}
                      </td>
                      <td className="cell-truncate" title={callText}>
                        {callText}
                      </td>
                      <td>
                        {r.tier ? (
                          <span className={`pill ${r.tier}`}>
                            <i className={`dot ${r.tier}`} />
                            {tierLabel(r.tier)}
                          </span>
                        ) : (
                          <span className="muted">None</span>
                        )}
                      </td>
                      <td className="cell-truncate-lg" title={reasonText}>
                        {reasonText}
                      </td>
                      <td style={{ textAlign: 'right' }}>
                        <button
                          type="button"
                          className="btn ghost text-small"
                          style={{ minHeight: '32px', padding: '4px 10px' }}
                          onClick={e => {
                            e.stopPropagation();
                            onOpenResident(r.id);
                          }}
                        >
                          Open case ›
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </dialog>
  );
};

export default OverviewDrilldownDialog;

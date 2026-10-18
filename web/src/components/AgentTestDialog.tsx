import React, { useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import { openInDemo } from '../lib/mode';

const CARD_MAP: Record<string, string> = {
  'kamla devi': 'alone_dizzy',
  'ramesh kumar': 'must_work',
  'gulab devi': 'family_answers',
  'mohan singh': 'emergency',
};

export interface AgentTestResident {
  id: string;
  name: string;
  age: number;
  ward?: string;
  alone?: boolean;
  outdoor?: boolean;
  conds?: string[];
  risk?: number;
}

export interface AgentTestDialogProps {
  resident: AgentTestResident;
  wardName: string;
  condLabels?: Record<string, [string, string]>;
  onClose: () => void;
}

export const AgentTestDialog: React.FC<AgentTestDialogProps> = ({
  resident,
  wardName,
  condLabels,
  onClose,
}) => {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    dialogRef.current?.showModal();
  }, []);

  const queryParams = new URLSearchParams({
    resident: resident.id,
    name: resident.name,
    age: String(resident.age),
    ward: wardName,
  });
  if (resident.alone) queryParams.set('alone', '1');
  if (resident.outdoor) queryParams.set('outdoor', '1');
  if (resident.conds && resident.conds.length > 0) {
    queryParams.set('conds', resident.conds.join(','));
  }

  const knownCard = CARD_MAP[resident.name.trim().toLowerCase()];
  if (knownCard) {
    queryParams.set('card', knownCard);
  }

  const browserUrl = `/call?${queryParams.toString()}`;
  const phoneUrl = `/call?via=phone&${queryParams.toString()}`;

  return (
    <dialog
      ref={dialogRef}
      className="ops-dialog ops-test-dialog"
      aria-labelledby="agent-test-title"
      onCancel={onClose}
      onClick={e => {
        if (e.target === dialogRef.current) onClose();
      }}
    >
      <div className="ops-dialog-header">
        <div>
          <span className="ops-eyebrow">Try a check-in call</span>
          <h2 id="agent-test-title">Start the call as {resident.name}</h2>
          <p>
            {resident.age} years · {wardName}
            {resident.risk != null ? ` · Risk score ${resident.risk}` : ''}
          </p>
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

      <div className="ops-test-dialog-body">
        <section className="ops-test-dialog-resident" aria-label="Resident profile">
          <div className="ops-test-dialog-meta">
            <div className="chips">
              <span className="pill">Age {resident.age}</span>
              <span className="pill">
                {resident.alone ? 'Lives alone' : 'Lives with family'}
              </span>
              {resident.outdoor && (
                <span className="pill">Works outdoors (Labourer/Farmer)</span>
              )}
              {resident.conds &&
                resident.conds.map(c => (
                  <span key={c} className="pill">
                    {condLabels && condLabels[c] ? condLabels[c][0] : c}
                  </span>
                ))}
              {resident.risk != null && (
                <span className="pill">Risk score {resident.risk}</span>
              )}
            </div>
            <p className="ops-test-dialog-nudge">
              <b>What to expect: </b>Talk to Chhaya as {resident.name}. Chhaya asks how you are and records your answers, exactly as it would on a real outreach call.
            </p>
          </div>
        </section>

        <div className="ops-test-dialog-options">
          <section className="card call-start-option" aria-labelledby="dialog-call-browser">
            <header>
              <h3 id="dialog-call-browser">In your browser</h3>
            </header>
            <p>Use your microphone. Headphones stop the agent hearing itself.</p>
            <ul className="call-start-facts">
              <li>Up to five minutes</li>
              <li>Hindi, English, Marathi, Gujarati or Marwari</li>
            </ul>
            <Link
              to={browserUrl}
              className="btn big"
              onClick={e => {
                if (openInDemo(browserUrl)) e.preventDefault();
                onClose();
              }}
            >
              Start a microphone call
            </Link>
          </section>

          <section className="card call-start-option" aria-labelledby="dialog-call-phone">
            <header>
              <h3 id="dialog-call-phone">Get a phone call</h3>
            </header>
            <p>Chhaya rings the number you enter, as it would ring a resident.</p>
            <ul className="call-start-facts">
              <li>Up to three minutes</li>
              <li>Only your own number</li>
            </ul>
            <Link
              to={phoneUrl}
              className="btn big ghost"
              onClick={e => {
                if (openInDemo(phoneUrl)) e.preventDefault();
                onClose();
              }}
            >
              Get a call on your phone
            </Link>
          </section>
        </div>

        <div className="ops-test-dialog-footer">
          <small className="muted">
            These are real AI calls. A phone call rings only the number you enter and adds no case to the register.
          </small>
        </div>
      </div>
    </dialog>
  );
};

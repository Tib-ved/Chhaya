import React from 'react';
import { getMode, switchMode, type Mode } from '../lib/mode';

const OPTIONS: { mode: Mode; label: string }[] = [
  { mode: 'live', label: 'Live data' },
  { mode: 'demo', label: 'Demo data' },
];

/** The top bar's Live data | Demo data switch. A change reloads the page so every view reads the other dataset. */
export const ModeSwitch: React.FC = () => {
  const current = getMode();
  const choose = (mode: Mode) => {
    if (mode === current) return;
    switchMode(mode);
    window.location.reload();
  };
  return (
    <div className="mode-switch" role="group" aria-label="Data source">
      {OPTIONS.map(o => (
        <button key={o.mode} type="button" className={o.mode === current ? 'active' : ''} aria-pressed={o.mode === current} onClick={() => choose(o.mode)}>
          {o.label}
        </button>
      ))}
    </div>
  );
};

/** Shown on every workspace page while the demo dataset is in view. */
export const DemoBand: React.FC = () => getMode() === 'demo'
  ? <div className="demo-band" role="note">Demo dataset: a fixed heat day for 4 Rajasthan districts.</div>
  : null;

export default ModeSwitch;

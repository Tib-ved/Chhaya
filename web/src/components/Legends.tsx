import React from 'react';
import { HIGH_RISK_SCORE, TIER_MEANING, tierLabel } from '../lib/labels';

/** How a risk score is built, as in backend/app/risk.py. */
export const RiskScoreLegend: React.FC<{ className?: string }> = ({ className = '' }) => (
  <div className={`score-legend ${className}`} role="note" aria-label="How the risk score is counted">
    <b>Risk score</b>
    <span>Aged 65 to 74 <em>+2</em></span>
    <span>Aged 75 or over <em>+3</em></span>
    <span>Lives alone <em>+2</em></span>
    <span>Works outdoors <em>+2</em></span>
    <span>Each health condition <em>+1</em></span>
    <span className="score-legend-high">{HIGH_RISK_SCORE} or more is high risk</span>
  </div>
);

/** What Red, Amber and Green ask of the health worker. */
export const TierLegend: React.FC<{ className?: string }> = ({ className = '' }) => (
  <div className={`score-legend ${className}`} role="note" aria-label="What the tiers mean">
    <b>Tier</b>
    {(['red', 'amber', 'green'] as const).map(tier => (
      <span key={tier}><i className={`dot ${tier}`} aria-hidden="true" /> <strong>{tierLabel(tier)}:</strong> {TIER_MEANING[tier].toLowerCase()}</span>
    ))}
  </div>
);

// One way of writing tiers and warning levels on every screen: "Red", "Amber", "Green", "Yellow".
// The backend's "orange" warning level is shown as Amber, the same word and colour as the Amber tier.

export const capitalise = (value?: string | null) => value ? value[0].toUpperCase() + value.slice(1).toLowerCase() : '';

/** "Red", "Amber" or "Green" for a tier however the API spelt it. */
export const tierLabel = (tier?: string | null) => capitalise(tier);

/** "Red", "Amber" or "Yellow" for a warning level; the API's "orange" reads as Amber. */
export const severityLabel = (severity?: string | null) => severity?.toLowerCase() === 'orange' ? 'Amber' : capitalise(severity);

/** "Red heat warning", "Amber cold warning"; "Recorded heat warning" when the level is unknown. */
export const warningLabel = (severity?: string | null, hazard?: string | null) =>
  `${severityLabel(severity) || 'Recorded'} ${(hazard || 'heat').toLowerCase().replace(/wave$/, '')} warning`.replace(/\s+/g, ' ');

/** What each tier asks of the health worker, as the call result says it. */
export const TIER_MEANING: Record<'red' | 'amber' | 'green', string> = {
  red: 'A health worker should go now',
  amber: 'A health worker should visit today',
  green: 'No visit needed today',
};

/** As on the backend (app/region.py HIGH_RISK_SCORE). */
export const HIGH_RISK_SCORE = 5;

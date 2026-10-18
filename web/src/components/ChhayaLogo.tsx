import React from 'react';

interface ChhayaLogoProps {
  size?: number;
  className?: string;
}

export const ChhayaLogo: React.FC<ChhayaLogoProps> = ({ size = 36, className = '' }) => {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      aria-hidden="true"
      className={className}
      style={{ flexShrink: 0, display: 'inline-block' }}
    >
      {/* Background warm sun tint circle */}
      <circle cx="50" cy="50" r="50" fill="#FEF3E0" />
      {/* Blazing sun orb */}
      <circle cx="76" cy="30" r="13" fill="#F28C28" />
      {/* Tree trunk */}
      <rect x="42" y="58" width="8" height="33" fill="#8B5A2B" />
      {/* Cool grass shade patch */}
      <ellipse cx="52" cy="91" rx="29" ry="3.5" fill="#CBE6C4" />
      {/* Tree foliage canopy providing deep shade */}
      <circle cx="52" cy="38" r="19" fill="#18673A" />
      <circle cx="37" cy="52" r="16" fill="#18673A" />
      <circle cx="68" cy="52" r="16" fill="#18673A" />
      <rect x="40" y="44" width="26" height="20" fill="#18673A" />
      {/* Person resting safely in shade */}
      <path
        d="M63 71c1 10 7 16 17 17"
        fill="none"
        stroke="#14343D"
        strokeWidth="5"
        strokeLinecap="round"
      />
      <rect x="59" y="66" width="8" height="8" rx="1.5" fill="#14343D" />
      <rect x="76" y="84" width="8" height="8" rx="1.5" fill="#14343D" />
    </svg>
  );
};

export default ChhayaLogo;

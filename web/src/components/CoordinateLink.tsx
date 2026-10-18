import React from 'react';

/** Clickable coordinate link that opens Google Maps in a new tab with intuitive visual indicators */
export const CoordinateLink: React.FC<{
  lat: number;
  lng: number;
  className?: string;
  label?: string;
}> = ({ lat, lng, className = '', label }) => {
  const url = `https://www.google.com/maps/search/?api=1&query=${lat},${lng}`;
  const text = label || `${lat.toFixed(4)}, ${lng.toFixed(4)}`;

  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className={`geo-coord-link ${className}`}
      title={`Open coordinates (${lat.toFixed(4)}, ${lng.toFixed(4)}) in Google Maps`}
      aria-label={`Open coordinates (${lat.toFixed(4)}, ${lng.toFixed(4)}) in Google Maps`}
      onClick={e => e.stopPropagation()}
    >
      <svg className="geo-pin-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z" />
        <circle cx="12" cy="10" r="3" />
      </svg>
      <span className="geo-coord-text">{text}</span>
      <svg className="geo-ext-icon" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
        <polyline points="15 3 21 3 21 9" />
        <line x1="10" y1="14" x2="21" y2="3" />
      </svg>
    </a>
  );
};

export default CoordinateLink;

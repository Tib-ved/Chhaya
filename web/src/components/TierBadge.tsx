import React from 'react'

interface TierBadgeProps {
  tier: 'RED' | 'AMBER' | 'GREEN' | string
  size?: 'normal' | 'large'
}

export const TierBadge: React.FC<TierBadgeProps> = ({ tier, size = 'normal' }) => {
  const upper = tier.toUpperCase()

  let background = 'var(--color-visited-gray)'
  let color = 'var(--color-surface)'

  if (upper === 'RED') {
    background = 'var(--color-red)'
  } else if (upper === 'AMBER') {
    background = 'var(--color-amber)'
  } else if (upper === 'GREEN') {
    background = 'var(--color-green)'
  }

  const padding = size === 'large' ? '4px 14px' : '2px 10px'
  const fontSize = size === 'large' ? '15px' : '12px'

  return (
    <span
      style={{
        display: 'inline-block',
        padding,
        borderRadius: '6px',
        background,
        color,
        fontWeight: 700,
        fontSize,
        letterSpacing: '0.04em',
        lineHeight: 1.2,
      }}
    >
      {upper}
    </span>
  )
}

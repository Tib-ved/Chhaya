import React from 'react'
import { Link, useLocation } from 'react-router-dom'

const STEPS = [
  { step: 1, label: '1 Alert', path: '/alert' },
  { step: 2, label: '2 Call', path: '/call' },
  { step: 3, label: '3 Visit', path: '/visit' },
  { step: 4, label: '4 Report', path: '/report' },
]

export const StepBar: React.FC = () => {
  const location = useLocation()

  return (
    <nav aria-label="Steps" style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', alignItems: 'center' }}>
      {STEPS.map((s) => {
        const isActive = location.pathname.startsWith(s.path)
        return (
          <Link
            key={s.step}
            to={s.path}
            aria-current={isActive ? 'step' : undefined}
            style={{
              padding: '8px 18px',
              borderRadius: '999px',
              border: isActive ? '1px solid var(--color-brand)' : '1px solid var(--color-line)',
              background: isActive ? 'var(--color-brand)' : 'transparent',
              color: isActive ? 'var(--color-surface)' : 'var(--color-text-main)',
              fontWeight: isActive ? 600 : 500,
              fontSize: '16px',
              textDecoration: 'none',
              transition: 'all 0.15s ease',
            }}
          >
            {s.label}
          </Link>
        )
      })}
    </nav>
  )
}

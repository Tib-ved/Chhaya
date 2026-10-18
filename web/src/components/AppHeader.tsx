import React from 'react'
import { Link } from 'react-router-dom'
import { ChhayaBrand } from './ChhayaBrand'

interface AppHeaderProps {
  subtitle?: React.ReactNode
  /** Line the header up with a page that uses the full width of the screen. */
  wide?: boolean
}

export const AppHeader: React.FC<AppHeaderProps> = ({ subtitle, wide = false }) => {
  return (
    <header className="app-header no-print">
      <div className={`app-header-inner ${wide ? 'wide' : ''}`}>
        <Link to="/" className="app-header-brand">
          <ChhayaBrand />
        </Link>
        {subtitle && <div className="app-header-subtitle">{subtitle}</div>}
        <nav className="app-header-nav" aria-label="Views">
          <Link to="/regional/overview" className="linkbtn text-small">Regional Officer view</Link>
          <Link to="/ward/overview" className="linkbtn text-small">Field crew view</Link>
        </nav>
      </div>
      <div className="app-header-demo">
        Demo data: the residents are fictional. Calls in the rounds are simulated calls with AI-played residents; test calls you make are real.
      </div>
    </header>
  )
}

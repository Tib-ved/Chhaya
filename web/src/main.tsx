import React from 'react'
import ReactDOM from 'react-dom/client'

// Self-hosted fonts: IBM Plex Sans for the interface, Mukta for Devanagari, IBM Plex Mono for rule ids
import '@fontsource/ibm-plex-sans/400.css'
import '@fontsource/ibm-plex-sans/500.css'
import '@fontsource/ibm-plex-sans/600.css'
import '@fontsource/ibm-plex-sans/700.css'
import '@fontsource/mukta/400.css'
import '@fontsource/mukta/500.css'
import '@fontsource/mukta/600.css'
import '@fontsource/mukta/700.css'
import '@fontsource/ibm-plex-mono/500.css'

// Tokens & Base styles
import './styles/tokens.css'
import './styles/base.css'
import './styles/roleViews.css'
import './styles/system.css'

import App from './App'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)

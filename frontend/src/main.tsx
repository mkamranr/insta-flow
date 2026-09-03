import { applyTheme, storedTheme } from './components/common/ThemeToggle'
import React from 'react'
// Before the first paint: otherwise a dark reader gets a white flash.
applyTheme(storedTheme())

import ReactDOM from 'react-dom/client'
import { Providers } from './app/providers'
import { Router } from './app/router'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <Providers>
      <Router />
    </Providers>
  </React.StrictMode>,
)

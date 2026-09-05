import { applyTheme, storedTheme } from './components/common/ThemeToggle'
import React from 'react'
// Before the first paint: otherwise a dark reader gets a white flash.
applyTheme(storedTheme())

import ReactDOM from 'react-dom/client'
import { Providers } from './app/providers'
import { recordLoad } from './app/loadLog'
import { Router } from './app/router'
import './index.css'

// Before render: a load that ends in a crash is still worth recording.
recordLoad()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <Providers>
      <Router />
    </Providers>
  </React.StrictMode>,
)

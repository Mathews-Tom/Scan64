import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { registerSW } from 'virtual:pwa-register'
import { initializeDesktop, isDesktop } from './platform'
import { DesktopHost } from './components/DesktopHost'
import { initializeBoardAppearance } from './boardAppearance'
import { initializeBoardSounds } from './boardSounds'

if (!isDesktop()) {
  const workers = navigator.serviceWorker
  if (import.meta.env.PROD && workers && !workers.controller && typeof PerformanceObserver !== 'undefined') {
    // Workbox can only intercept requests made after this document is claimed.
    const missed = new Set<string>()
    let controlledAt = Infinity
    const cacheMissed = () => {
      if (!workers.controller || !missed.size) return
      workers.controller.postMessage({ type: 'CACHE_URLS', payload: { urlsToCache: [...missed] } })
      missed.clear()
    }
    new PerformanceObserver(list => {
      for (const entry of list.getEntries()) {
        // A request started before control can finish after controllerchange.
        if (entry.startTime > controlledAt) continue
        const url = new URL(entry.name)
        if (url.origin === location.origin && url.href === url.origin + url.pathname && url.pathname.startsWith('/assets/') && url.pathname.endsWith('.js')) {
          missed.add(url.href)
        }
      }
      cacheMissed()
    }).observe({ type: 'resource', buffered: true })
    workers.addEventListener('controllerchange', () => {
      controlledAt = performance.now()
      cacheMissed()
    }, { once: true })
  }
  registerSW({ immediate: true })
}

initializeBoardAppearance()
initializeBoardSounds()

const rootElement = document.getElementById('root')!
const root = createRoot(rootElement)

if (isDesktop()) {
  rootElement.textContent = 'Starting Scan64…'
  rootElement.setAttribute('role', 'status')
  const renderDesktop = (initialError: string | null) => {
    rootElement.removeAttribute('role')
    root.render(<StrictMode><DesktopHost initialError={initialError} /></StrictMode>)
  }
  void initializeDesktop().then(
    () => { renderDesktop(null) },
    error => { renderDesktop(String(error)) },
  )
} else {
  root.render(<StrictMode><App /></StrictMode>)
}

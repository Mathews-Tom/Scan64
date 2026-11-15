import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { registerSW } from 'virtual:pwa-register'
import { initializeDesktop, isDesktop } from './platform'
import { DesktopHost } from './components/DesktopHost'

if (!isDesktop()) registerSW({ immediate: true })

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

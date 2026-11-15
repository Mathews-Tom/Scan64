import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { registerSW } from 'virtual:pwa-register'
import { initializeDesktop, isDesktop } from './platform'
import { DesktopHost } from './components/DesktopHost'
import { initializeBoardAppearance } from './boardAppearance'

if (!isDesktop()) registerSW({ immediate: true })

initializeBoardAppearance()

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

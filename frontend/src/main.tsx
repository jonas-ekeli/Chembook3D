import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { UntilStopped } from './components/ShutDown'
import { startPresence } from './lifecycle'

startPresence() // D107: this tab counts as open while it keeps in touch with the backend

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <UntilStopped>
      <App />
    </UntilStopped>
  </StrictMode>,
)

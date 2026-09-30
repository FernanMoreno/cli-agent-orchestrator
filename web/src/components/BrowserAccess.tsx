import { useState } from 'react'
import { BrowserLogin } from './BrowserLogin'
import { BrowserSetup } from './BrowserSetup'

export function BrowserAccess({ setupAvailable = false }: { setupAvailable?: boolean }) {
  const [view, setView] = useState<'login' | 'create'>('login')
  const navigation = (busy: boolean) => <div role="tablist" aria-label="Acceso a CAO" className="flex w-full max-w-md gap-2 rounded-lg border border-gray-700 bg-gray-900 p-1">
    {(['login', 'create'] as const).map(key => <button key={key} type="button" role="tab" id={`cao-auth-tab-${key}`} aria-controls="cao-auth-panel" aria-selected={view === key} tabIndex={view === key ? 0 : -1} disabled={busy} onClick={() => setView(key)} onKeyDown={event => {
      if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
        event.preventDefault()
        const next = event.key === 'Home' ? 'login' : event.key === 'End' ? 'create' : view === 'login' ? 'create' : 'login'
        setView(next)
        queueMicrotask(() => document.getElementById(`cao-auth-tab-${next}`)?.focus())
      }
    }} className={`flex-1 rounded px-4 py-2 text-sm ${view === key ? 'bg-emerald-600 text-white' : 'text-gray-300 hover:bg-gray-800'} disabled:opacity-50`}>{key === 'login' ? 'Iniciar sesión' : 'Crear cuenta'}</button>)}
  </div>
  if (view === 'login') return <BrowserLogin navigation={navigation} accountConfigured={!setupAvailable} onCreateAccount={() => setView('create')} />
  if (setupAvailable) return <BrowserSetup navigation={navigation} onSignIn={() => setView('login')} />
  return <main className="min-h-screen bg-gray-950 text-gray-200 flex flex-col gap-4 items-center justify-center p-6">
    {navigation(false)}
    <section role="tabpanel" id="cao-auth-panel" aria-labelledby="cao-auth-tab-create" className="w-full max-w-md rounded-lg border border-gray-700 bg-gray-900 p-6 space-y-4">
      <h1 className="text-xl font-semibold">Crear cuenta</h1>
      <p>La cuenta de esta instalación ya está creada.</p>
      <p className="text-sm text-gray-400">Inicia sesión con tu usuario y contraseña.</p>
      <button type="button" onClick={() => setView('login')} className="rounded bg-emerald-600 px-4 py-2">Volver a iniciar sesión</button>
    </section>
  </main>
}

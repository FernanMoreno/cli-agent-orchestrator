import { useState, type ReactNode } from 'react'
import { authSnapshot, BrowserAuthError, initializeBrowserAuth, setupBrowser } from '../auth'
import { CaoMark } from './CaoMark'

export function BrowserSetup({ navigation, onSignIn }: { navigation?: (busy: boolean) => ReactNode; onSignIn?: () => void }) {
  const [username, setUsername] = useState('felni')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [remember, setRemember] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [publicationUncertain, setPublicationUncertain] = useState(false)
  return <main className="min-h-screen bg-gray-950 text-gray-200 flex flex-col gap-4 items-center justify-center p-6">
    {navigation?.(busy || publicationUncertain)}
    <form role={navigation ? 'tabpanel' : undefined} id={navigation ? 'cao-auth-panel' : undefined} aria-labelledby={navigation ? 'cao-auth-tab-create' : undefined} className="w-full max-w-md rounded-lg border border-gray-700 bg-gray-900 p-6 space-y-4" onSubmit={async event => {
      event.preventDefault(); if (publicationUncertain) return; setError('')
      if (password !== confirmation) { setError('Las contraseñas no coinciden.'); return }
      if (password.length < 10 || password.length > 128) { setError('Usa una contraseña de entre 10 y 128 caracteres.'); return }
      setBusy(true)
      try { await setupBrowser(username.trim(), password, remember) }
      catch (cause) {
        if (cause instanceof BrowserAuthError && cause.code === 'setup_publication_uncertain') {
          setPublicationUncertain(true)
          setError(cause.message)
        } else setError(cause instanceof BrowserAuthError && [401, 403].includes(cause.status)
          ? 'Abre el enlace de acceso autorizado de esta instalación para configurar tu cuenta.'
          : cause instanceof BrowserAuthError && cause.status === 409
            ? 'La cuenta ya está configurada. Actualiza esta página para iniciar sesión.'
            : cause instanceof BrowserAuthError && cause.status === 422
              ? 'Revisa el usuario y la contraseña e inténtalo de nuevo.'
              : 'No se ha podido confirmar el acceso. Actualiza esta página antes de volver a intentarlo.')
      } finally { setPassword(''); setConfirmation(''); setBusy(false) }
    }}>
      <CaoMark size={48} />
      <h1 className="text-xl font-semibold">Crear cuenta</h1>
      <p className="text-sm text-gray-400">Crea tu acceso con usuario y contraseña. Después entrarás directamente en tu panel de CAO.</p>
      <label className="block">Usuario<input className="mt-1 w-full rounded border border-gray-600 bg-gray-800 p-2" name="username" autoComplete="username" value={username} onChange={event => setUsername(event.target.value)} required maxLength={64} pattern="[A-Za-z0-9._\-]+" disabled={busy || publicationUncertain} /></label>
      <label className="block">Contraseña<input className="mt-1 w-full rounded border border-gray-600 bg-gray-800 p-2" name="password" type="password" autoComplete="new-password" value={password} onChange={event => setPassword(event.target.value)} required minLength={10} maxLength={128} disabled={busy || publicationUncertain} /></label>
      <p className="text-sm text-gray-400">Entre 10 y 128 caracteres. Puedes usar una frase larga.</p>
      <label className="block">Confirmar contraseña<input className="mt-1 w-full rounded border border-gray-600 bg-gray-800 p-2" name="password-confirmation" type="password" autoComplete="new-password" value={confirmation} onChange={event => setConfirmation(event.target.value)} required minLength={10} maxLength={128} disabled={busy || publicationUncertain} /></label>
      <label className="flex gap-2"><input name="remember" type="checkbox" checked={remember} onChange={event => setRemember(event.target.checked)} disabled={busy || publicationUncertain} />Recordar este navegador</label>
      {error && <p role="alert" className="text-red-400">{error}</p>}
      <button type="submit" disabled={busy || publicationUncertain} className="rounded bg-emerald-600 px-4 py-2">{busy ? 'Configurando acceso…' : 'Crear cuenta y entrar'}</button>
      {publicationUncertain && <button type="button" disabled={busy} onClick={async () => {
        setBusy(true)
        try {
          await initializeBrowserAuth()
          const confirmed = authSnapshot()
          if (confirmed.mode === 'bearer' && confirmed.status === 'authenticated' && confirmed.config?.local_setup_available === true) {
            setPublicationUncertain(false)
            setError('')
          }
        } finally { setBusy(false) }
      }} className="rounded bg-emerald-600 px-4 py-2">Ir a iniciar sesión</button>}
      {onSignIn && !publicationUncertain && <p className="text-sm text-gray-400">¿Ya tienes cuenta? <button type="button" disabled={busy || publicationUncertain} onClick={onSignIn} className="text-emerald-400 hover:underline">Iniciar sesión</button></p>}
    </form>
  </main>
}

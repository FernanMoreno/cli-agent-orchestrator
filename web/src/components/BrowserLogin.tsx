import { useState, useSyncExternalStore, type ReactNode } from 'react'
import { authSnapshot, BrowserAuthError, loginBrowser, subscribeBrowserAuth } from '../auth'

export function BrowserLogin({ navigation, onCreateAccount, accountConfigured = true }: { navigation?: (busy: boolean) => ReactNode; onCreateAccount?: () => void; accountConfigured?: boolean }) {
  const auth = useSyncExternalStore(subscribeBrowserAuth, authSnapshot)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [remember, setRemember] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const limits = auth.config
  const duration = (seconds: unknown) => typeof seconds === 'number' ? `${Math.round(seconds / 3600)} horas` : 'el límite configurado'
  return <main className="min-h-screen bg-gray-950 text-gray-200 flex flex-col gap-4 items-center justify-center p-6">
    {navigation?.(busy)}
    <form role={navigation ? 'tabpanel' : undefined} id={navigation ? 'cao-auth-panel' : undefined} aria-labelledby={navigation ? 'cao-auth-tab-login' : undefined} className="w-full max-w-md rounded-lg border border-gray-700 bg-gray-900 p-6 space-y-4" onSubmit={async event => {
      event.preventDefault(); setError('')
      if (!accountConfigured) { setError('Crea tu cuenta antes de iniciar sesión.'); setPassword(''); return }
      setBusy(true)
      try { await loginBrowser(username, password, remember) }
      catch (cause) {
        setError(cause instanceof BrowserAuthError ? (cause.code === 'login_throttled' ? `Demasiados intentos. Vuelve a intentarlo en ${cause.retryAfter ?? '60'} segundos.` : cause.message) : cause instanceof Error && cause.message.startsWith('Las cookies') ? cause.message : 'Servidor no disponible. Vuelve a intentarlo cuando haya conexión.')
      } finally { setPassword(''); setBusy(false) }
    }}>
      <h1 className="text-xl font-semibold">Iniciar sesión</h1>
      {auth.message && <p className="text-sm" role="status">{auth.message}</p>}
      <label className="block">Usuario<input className="mt-1 w-full rounded border border-gray-600 bg-gray-800 p-2" name="username" autoComplete="username" value={username} onChange={e=>setUsername(e.target.value)} required disabled={busy} /></label>
      <label className="block">Contraseña<input className="mt-1 w-full rounded border border-gray-600 bg-gray-800 p-2" name="password" type="password" autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)} required disabled={busy} /></label>
      <label className="flex gap-2"><input name="remember" type="checkbox" checked={remember} onChange={e=>setRemember(e.target.checked)} disabled={busy} />Recordar este navegador</label>
      <p className="text-sm text-gray-400">{remember ? 'La sesión se conserva hasta alcanzar el límite de inactividad o duración máxima. Si borras los datos del navegador, perderás el acceso.' : 'El acceso temporal dura esta sesión del navegador, dentro de los límites de inactividad y duración máxima.'}</p>
      {limits && <p className="text-sm text-gray-400">Inactividad: {duration(limits[remember ? 'remembered_idle_seconds' : 'temporal_idle_seconds'])}. Duración máxima: {duration(limits[remember ? 'remembered_absolute_seconds' : 'temporal_absolute_seconds'])}.</p>}
      {error && <p role="alert" className="text-red-400">{error}</p>}
      <button type="submit" disabled={busy} className="rounded bg-emerald-600 px-4 py-2">{busy ? 'Iniciando sesión…' : 'Iniciar sesión'}</button>
      {onCreateAccount && <p className="text-sm text-gray-400">¿No tienes cuenta? <button type="button" disabled={busy} onClick={onCreateAccount} className="text-emerald-400 hover:underline">Crear cuenta</button></p>}
    </form>
  </main>
}

import { useState, useSyncExternalStore } from 'react'
import { authSnapshot, BrowserAuthError, changeBrowserPassword, logoutBrowser, subscribeBrowserAuth } from '../auth'

export function BrowserAccount() {
  const auth = useSyncExternalStore(subscribeBrowserAuth, authSnapshot)
  const [changing, setChanging] = useState(false)
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const signOut = async (all = false) => {
    setBusy(true); setError('')
    try { await logoutBrowser(all) } catch { setError('El cierre de sesión está pendiente de confirmación. Reinténtalo cuando el servidor esté disponible.') }
    finally { setBusy(false) }
  }
  return <div className="text-xs space-x-3">
    <span>{auth.session?.username}</span>
    <button disabled={busy} onClick={()=>void signOut()}>Cerrar sesión</button>
    <button disabled={busy} onClick={()=>void signOut(true)}>Cerrar todas las sesiones</button>
    <button disabled={busy} onClick={()=>setChanging(!changing)}>Cambiar contraseña</button>
    {auth.session && <details className="inline-block"><summary>Límites de la sesión</summary><p>Acceso hasta {new Date(auth.session.access_expires_at*1000).toLocaleString()}</p><p>Límite de inactividad {new Date(auth.session.idle_expires_at*1000).toLocaleString()}</p><p>Duración máxima hasta {new Date(auth.session.absolute_expires_at*1000).toLocaleString()}</p></details>}
    {error && <p role="alert">{error}</p>}
    {changing && <div role="dialog" aria-modal="true" aria-label="Cambiar contraseña" className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center">
      <form className="bg-gray-900 p-6 rounded space-y-4" onSubmit={async event=>{
        event.preventDefault(); setBusy(true); setError('')
        try { await changeBrowserPassword(current,next) }
        catch(cause) { setError(cause instanceof BrowserAuthError ? cause.message : 'El resultado de la operación es incierto. Comprueba el estado de la sesión antes de volver a intentarlo.') }
        finally { setCurrent(''); setNext(''); setBusy(false) }
      }}>
        <h2>Cambiar contraseña</h2>
        <label className="block">Contraseña actual<input className="block bg-gray-800 p-2" type="password" autoComplete="current-password" value={current} onChange={e=>setCurrent(e.target.value)} required disabled={busy} /></label>
        <label className="block">Nueva contraseña<input className="block bg-gray-800 p-2" type="password" autoComplete="new-password" value={next} onChange={e=>setNext(e.target.value)} required disabled={busy} /></label>
        <p>Al cambiar la contraseña se cierran todas las sesiones del navegador.</p>
        <button disabled={busy} type="submit">Guardar contraseña</button>
        <button disabled={busy} type="button" onClick={()=>{setChanging(false);setCurrent('');setNext('')}}>Cancelar</button>
        {error && <p role="alert">{error}</p>}
      </form>
    </div>}
  </div>
}

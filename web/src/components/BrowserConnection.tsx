import { useEffect, useState } from 'react'
import { api } from '../api'
import { setBrowserBearer } from '../auth'

export function BrowserConnection() {
  const [required, setRequired] = useState(false)
  const [token, setToken] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    const show = () => { setRequired(true); setError('Se requiere autenticación o el acceso ha caducado.') }
    window.addEventListener('cao-auth-required', show)
    return () => window.removeEventListener('cao-auth-required', show)
  }, [])
  return <>
    <button className="text-xs text-gray-300 hover:text-white" onClick={() => setRequired(true)}>Conectar</button>
    {required && <div role="dialog" aria-modal="true" aria-labelledby="connection-title" className="fixed inset-0 z-50 flex items-center justify-center bg-black/70">
      <form className="w-full max-w-lg rounded-lg border border-gray-700 bg-gray-900 p-6 space-y-4" onSubmit={async event => {
        event.preventDefault()
        setBusy(true); setError('')
        try {
          setBrowserBearer(token.trim())
          await api.listSessions()
          setToken('')
          window.location.reload()
        } catch { setError('No se ha podido conectar. Comprueba el servidor y renueva tu token de acceso.') }
        finally { setBusy(false) }
      }}>
        <h2 id="connection-title" className="text-lg font-semibold">Conectar con CAO</h2>
        <p className="text-sm text-gray-300">Abre un enlace de acceso local nuevo o introduce tu token de acceso de CAO. Solo se guarda en esta pestaña del navegador.</p>
        <label className="block text-sm">Token de acceso de CAO
          <input type="password" autoComplete="off" spellCheck={false} value={token} onChange={event => setToken(event.target.value)} className="mt-2 w-full rounded border border-gray-600 bg-gray-800 p-2" required />
        </label>
        {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
        <div className="flex gap-4">
          <button disabled={busy} type="submit" className="rounded bg-emerald-600 px-4 py-2">{busy ? 'Conectando…' : 'Conectar'}</button>
          <button disabled={busy} type="button" onClick={() => { setRequired(false); setToken('') }}>Cancelar</button>
        </div>
      </form>
    </div>}
  </>
}

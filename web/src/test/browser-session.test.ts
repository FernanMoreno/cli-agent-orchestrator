import { afterEach, expect, it, vi } from 'vitest'
import * as auth from '../auth'
const session = { session_id:'one', username:'operator', remembered:true, access_expires_at:4600, idle_expires_at:9000, absolute_expires_at:10000, server_time:1000, session_revision:1 }
const json = (value: unknown, status=200) => new Response(JSON.stringify(value),{status})
afterEach(() => { vi.unstubAllGlobals(); sessionStorage.clear() })
it('bootstraps local sessions without retaining a historical bearer', async () => {
  sessionStorage.setItem('cao.browser.bearer','old.token')
  vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(json({mode:'local_password'})).mockResolvedValueOnce(json(session)))
  await auth.initializeBrowserAuth()
  expect(auth.authSnapshot().status).toBe('authenticated')
  expect(sessionStorage.getItem('cao.browser.bearer')).toBeNull()
  expect(auth.browserBearer()).toBeNull()
})
it('uses cookie credentials and browser marker on writes', async () => {
  const fetch = vi.fn().mockResolvedValueOnce(json({mode:'local_password'})).mockResolvedValueOnce(json(session)).mockResolvedValue(json({}))
  vi.stubGlobal('fetch',fetch); await auth.initializeBrowserAuth()
  await auth.browserFetch('/work-items',{method:'POST',body:'{}'})
  const opts = fetch.mock.calls[fetch.mock.calls.length - 1][1]
  expect(opts.credentials).toBe('same-origin'); expect(opts.redirect).toBe('error')
  expect(new Headers(opts.headers).get('X-CAO-Browser')).toBe('1')
  expect(new Headers(opts.headers).has('Authorization')).toBe(false)
})
it('renews and repeats only one safe read after lease rejection', async () => {
  const fetch = vi.fn().mockResolvedValueOnce(json({mode:'local_password'})).mockResolvedValueOnce(json(session)).mockResolvedValueOnce(json({detail:{code:'access_renewal_required'}},401)).mockResolvedValueOnce(json(session)).mockResolvedValueOnce(json([]))
  vi.stubGlobal('fetch',fetch); await auth.initializeBrowserAuth()
  expect((await auth.browserFetch('/sessions')).status).toBe(200)
  expect(fetch.mock.calls.map(call=>call[0])).toEqual(['/auth/config','/auth/session','/sessions','/auth/renew','/sessions'])
})
it('does not replay a mutation rejected after send', async () => {
  const fetch=vi.fn().mockResolvedValueOnce(json({mode:'local_password'})).mockResolvedValueOnce(json(session)).mockResolvedValueOnce(json({detail:{code:'access_renewal_required'}},401))
  vi.stubGlobal('fetch',fetch); await auth.initializeBrowserAuth()
  expect((await auth.browserFetch('/work-items',{method:'POST'})).status).toBe(401)
  expect(fetch).toHaveBeenCalledTimes(3)
})
it('preserves session authority on unavailable renewal',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(json({mode:'local_password'})).mockResolvedValueOnce(json(session)).mockResolvedValueOnce(json({detail:{code:'auth_unavailable'}},503)))
  await auth.initializeBrowserAuth(); await expect(auth.renewBrowserSession()).rejects.toThrow()
  expect(auth.authSnapshot().status).toBe('unavailable'); expect(auth.authSnapshot().session?.session_id).toBe('one')
})
it('keeps logout pending when unavailable and aborts protected requests',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(json({mode:'local_password'})).mockResolvedValueOnce(json(session)).mockRejectedValueOnce(new TypeError('offline')))
  await auth.initializeBrowserAuth(); const signal=auth.browserAuthSignal()
  await expect(auth.logoutBrowser()).rejects.toThrow()
  expect(signal.aborted).toBe(true); expect(auth.authSnapshot().logoutPending).toBe(true)
})
it('ignores a late old rejection after a replacement login',async()=>{
 let complete!: (response:Response)=>void
 const fetch=vi.fn().mockResolvedValueOnce(json({mode:'local_password'})).mockResolvedValueOnce(json(session)).mockImplementationOnce(()=>new Promise<Response>(resolve=>{complete=resolve})).mockResolvedValueOnce(json({...session,session_id:'new'})).mockResolvedValueOnce(json({...session,session_id:'new'}))
 vi.stubGlobal('fetch',fetch); await auth.initializeBrowserAuth()
 const pending=auth.browserFetch('/sessions')
 await vi.waitFor(()=>expect(fetch).toHaveBeenCalledTimes(3))
 await auth.loginBrowser('operator','new password',true)
 complete(json({detail:{code:'session_revoked'}},401)); await expect(pending).rejects.toThrow('La sesión ha cambiado')
 expect(auth.authSnapshot().session?.session_id).toBe('new')
})
it('confirms cookie after login and reports blocked cookies',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(json({mode:'local_password'})).mockResolvedValueOnce(json({detail:{code:'session_required'}},401)).mockResolvedValueOnce(json(session)).mockResolvedValueOnce(json({detail:{code:'session_required'}},401)))
 await auth.initializeBrowserAuth()
 await expect(auth.loginBrowser('operator','password',false)).rejects.toThrow('Las cookies están bloqueadas')
 expect(auth.authSnapshot().session).toBeNull()
})
it('marks protected auth storage failures as unavailable without expiring session',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(json({mode:'local_password'})).mockResolvedValueOnce(json(session)).mockResolvedValueOnce(json({detail:{code:'auth_unavailable'}},503)))
 await auth.initializeBrowserAuth(); await auth.browserFetch('/sessions')
 expect(auth.authSnapshot().status).toBe('unavailable')
 expect(auth.authSnapshot().session?.session_id).toBe('one')
})
it('does not send a write if logout overtakes its preflight renewal',async()=>{
 let complete!:(response:Response)=>void
 const expiredLease={...session,access_expires_at:1000}
 const fetch=vi.fn().mockResolvedValueOnce(json({mode:'local_password'})).mockResolvedValueOnce(json(expiredLease)).mockImplementationOnce(()=>new Promise<Response>(resolve=>{complete=resolve})).mockResolvedValueOnce(new Response(null,{status:204})).mockResolvedValueOnce(json({detail:{code:'session_revoked'}},401)).mockResolvedValue(json({}))
 vi.stubGlobal('fetch',fetch);await auth.initializeBrowserAuth()
 const pending=auth.browserFetch('/work-items',{method:'POST'})
 await vi.waitFor(()=>expect(fetch).toHaveBeenCalledTimes(3))
 await auth.logoutBrowser();complete(json(session))
 await expect(pending).rejects.toThrow('La sesión ha cambiado')
 expect(fetch.mock.calls.map(call=>call[0])).not.toContain('/work-items')
})
it('confirms the current cookie after logout so a concurrent new login survives',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(json({mode:'local_password'})).mockResolvedValueOnce(json(session)).mockResolvedValueOnce(new Response(null,{status:204})).mockResolvedValueOnce(json({...session,session_id:'replacement'})))
 await auth.initializeBrowserAuth();await auth.logoutBrowser()
 expect(auth.authSnapshot().status).toBe('authenticated')
 expect(auth.authSnapshot().session?.session_id).toBe('replacement')
})
it('does not retry pending revocation against a new cookie session',async()=>{
 const fetch=vi.fn().mockResolvedValueOnce(json({mode:'local_password'})).mockResolvedValueOnce(json(session)).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(json({...session,session_id:'replacement'}))
 vi.stubGlobal('fetch',fetch);await auth.initializeBrowserAuth();await expect(auth.logoutBrowser()).rejects.toThrow()
 await auth.logoutBrowser()
 expect(auth.authSnapshot().session?.session_id).toBe('replacement')
 expect(fetch.mock.calls.filter(call=>call[0]==='/auth/logout')).toHaveLength(1)
})
it('retires old requests when another tab replaces the cookie session',async()=>{
 let complete!:(response:Response)=>void
 const fetch=vi.fn().mockResolvedValueOnce(json({mode:'local_password'})).mockResolvedValueOnce(json(session)).mockImplementationOnce(()=>new Promise<Response>(resolve=>{complete=resolve})).mockResolvedValueOnce(json({...session,session_id:'other-tab'}))
 vi.stubGlobal('fetch',fetch);await auth.initializeBrowserAuth();const pending=auth.browserFetch('/sessions')
 await vi.waitFor(()=>expect(fetch).toHaveBeenCalledTimes(3));await auth.refreshBrowserSession()
 complete(json({detail:{code:'session_revoked'}},401));await expect(pending).rejects.toThrow('La sesión ha cambiado')
 expect(auth.authSnapshot().session?.session_id).toBe('other-tab')
})
it('reports timeout after a sent mutation as uncertain instead of replaying it',async()=>{
 const controller=new AbortController()
 const fetch=vi.fn().mockResolvedValueOnce(json({mode:'local_password'})).mockResolvedValueOnce(json(session)).mockImplementationOnce(()=>{controller.abort();return Promise.reject(new DOMException('timed out','AbortError'))})
 vi.stubGlobal('fetch',fetch);await auth.initializeBrowserAuth()
 await expect(auth.browserFetch('/work-items',{method:'POST',signal:controller.signal})).rejects.toThrow('El resultado de la operación es incierto')
 expect(fetch).toHaveBeenCalledTimes(3)
})
it('rejects a safe-read retry completed after replacement login',async()=>{
 let complete!:(response:Response)=>void
 const fetch=vi.fn().mockResolvedValueOnce(json({mode:'local_password'})).mockResolvedValueOnce(json(session)).mockResolvedValueOnce(json({detail:{code:'access_renewal_required'}},401)).mockResolvedValueOnce(json(session)).mockImplementationOnce(()=>new Promise<Response>(resolve=>{complete=resolve})).mockResolvedValueOnce(json({...session,session_id:'replacement'})).mockResolvedValueOnce(json({...session,session_id:'replacement'}))
 vi.stubGlobal('fetch',fetch);await auth.initializeBrowserAuth();const pending=auth.browserFetch('/sessions')
 await vi.waitFor(()=>expect(fetch).toHaveBeenCalledTimes(5));await auth.loginBrowser('operator','password',false)
 complete(json([]));await expect(pending).rejects.toThrow('La sesión ha cambiado')
})

import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, terminalSocketUrl } from '../api'
import { browserFetch, consumeBrowserLink, setBrowserBearer } from '../auth'

const token = 'signed.operator.token'
afterEach(() => { sessionStorage.clear(); vi.unstubAllGlobals() })
describe('authenticated browser transports', () => {
  it('consumes a private access fragment without leaving it in history', () => {
    history.replaceState(null, '', '/?view=home#cao_token=signed.operator.token')
    consumeBrowserLink()
    expect(location.hash).toBe('')
    expect(location.search).toBe('?view=home')
    expect(sessionStorage.getItem('cao.browser.bearer')).toBe(token)
    expect(localStorage.getItem('cao.browser.bearer')).toBeNull()
    history.replaceState(null, '', '/')
  })
  it('adds bearer without losing SSE headers', async () => {
    setBrowserBearer(token)
    const fetch = vi.fn().mockResolvedValue(new Response('', { status: 200 }))
    vi.stubGlobal('fetch', fetch)
    await browserFetch('/workflows/runs/run/events', { headers: { Accept: 'text/event-stream' } })
    const headers = new Headers(fetch.mock.calls[0][1].headers)
    expect(headers.get('Accept')).toBe('text/event-stream')
    expect(headers.get('Authorization')).toBe(`Bearer ${token}`)
  })
  it('rejects cross-origin transport before sending credentials', async () => {
    setBrowserBearer(token)
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    await expect(browserFetch('https://other.invalid/sessions')).rejects.toThrow('mismo origen')
    expect(fetch).not.toHaveBeenCalled()
  })
  it('rejects malformed tokens', () => {
    expect(() => setBrowserBearer('bad\nheader')).toThrow('Formato de token inválido')
    expect(sessionStorage.getItem('cao.browser.bearer')).toBeNull()
  })
  it('does not clear a replacement credential after an older request fails', async () => {
    setBrowserBearer(token)
    let complete!: (response: Response) => void
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => { complete = resolve })))
    const pending = browserFetch('/sessions')
    setBrowserBearer('fresh.operator.token')
    complete(new Response('{}', { status: 401 }))
    await pending
    expect(sessionStorage.getItem('cao.browser.bearer')).toBe('fresh.operator.token')
  })
  it('uses tab bearer for protected REST requests', async () => {
    sessionStorage.setItem('cao.browser.bearer', token)
    const fetch = vi.fn().mockResolvedValue(new Response('[]', { status: 200 }))
    vi.stubGlobal('fetch', fetch)
    await api.listSessions()
    const options = fetch.mock.calls[0][1]
    expect(new Headers(options.headers).get('Authorization')).toBe(`Bearer ${token}`)
    expect(options.redirect).toBe('error')
  })
  it('passes bearer through the existing websocket handshake', () => {
    sessionStorage.setItem('cao.browser.bearer', token)
    expect(new URL(terminalSocketUrl('term-1')).searchParams.get('token')).toBe(token)
  })
  it('clears rejected bearer and reports required authentication', async () => {
    sessionStorage.setItem('cao.browser.bearer', token)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 401 })))
    const listener = vi.fn()
    window.addEventListener('cao-auth-required', listener)
    try {
      await expect(api.listSessions()).rejects.toThrow('401')
      expect(sessionStorage.getItem('cao.browser.bearer')).toBeNull()
      expect(listener).toHaveBeenCalledTimes(1)
    } finally { window.removeEventListener('cao-auth-required', listener) }
  })
})

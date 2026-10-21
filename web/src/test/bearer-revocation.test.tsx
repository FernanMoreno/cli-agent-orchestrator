import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import App from '../App'
import { api } from '../api'
import { authSnapshot } from '../auth'
vi.mock('../components/AgentPanel', () => ({ AgentPanel: () => null }))
vi.mock('../components/ProfilesPanel', () => ({ ProfilesPanel: () => null }))
vi.mock('../components/DashboardHome', () => ({ DashboardHome: () => <div>Protected dashboard</div> }))
afterEach(() => { cleanup(); vi.unstubAllGlobals(); sessionStorage.clear(); history.replaceState({}, '', '/') })

const token = 'signed.operator.token'

function bearerServer(mode: 'bearer' | 'disabled' = 'bearer') {
  const server = { rejected: false }
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/auth/config')) return new Response(JSON.stringify({ mode }))
    if (server.rejected) return new Response('{}', { status: 401 })
    if (url.includes('/fleet')) return new Response('{}', { status: 404 })
    return new Response('[]')
  })
  vi.stubGlobal('fetch', fetch)
  return server
}

it('withdraws the dashboard and asks for a new token when the bearer is rejected', async () => {
  sessionStorage.setItem('cao.browser.bearer', token)
  const server = bearerServer()
  render(<App />)
  expect(await screen.findByText('Protected dashboard')).toBeInTheDocument()
  server.rejected = true
  await act(async () => { await expect(api.listSessions()).rejects.toThrow() })
  await waitFor(() => expect(screen.queryByText('Protected dashboard')).toBeNull())
  expect(authSnapshot().status).toBe('revoked')
  expect(sessionStorage.getItem('cao.browser.bearer')).toBeNull()
  expect(screen.getByRole('dialog')).toBeInTheDocument()
  expect(screen.getByLabelText('Token de acceso de CAO')).toBeInTheDocument()
})

it('does not hand back a read that was in flight when the bearer was rejected', async () => {
  sessionStorage.setItem('cao.browser.bearer', token)
  bearerServer()
  render(<App />)
  await screen.findByText('Protected dashboard')
  let release!: (response: Response) => void
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    if (String(input).includes('/sessions/slow'))
      return new Promise<Response>((resolve) => { release = resolve })
    return Promise.resolve(new Response('{}', { status: 401 }))
  }))
  const slow = api.getSession('slow')
  await act(async () => { await expect(api.listSessions()).rejects.toThrow() })
  release(new Response(JSON.stringify({ id: 'slow', terminals: [] })))
  await expect(slow).rejects.toThrow()
})

it.each([null, token])('does not revoke disabled authentication on 401 with stored token %s', async (storedToken) => {
  if (storedToken) sessionStorage.setItem('cao.browser.bearer', storedToken)
  let rejected = false
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/auth/config')) return new Response(JSON.stringify({ mode: 'disabled' }))
    if (rejected) return new Response('{}', { status: 401 })
    if (url.includes('/fleet')) return new Response('{}', { status: 404 })
    return new Response('[]')
  }))
  render(<App />)
  await screen.findByText('Protected dashboard')
  rejected = true
  await act(async () => { await expect(api.listSessions()).rejects.toThrow() })
  expect(authSnapshot().mode).toBe('disabled')
  expect(authSnapshot().status).not.toBe('revoked')
  expect(sessionStorage.getItem('cao.browser.bearer')).toBeNull()
  expect(screen.getByText('Protected dashboard')).toBeInTheDocument()
})

it.each(['bearer', 'disabled'] as const)('refuses a response body after token rejection under %s authentication', async (mode) => {
  sessionStorage.setItem('cao.browser.bearer', token)
  bearerServer(mode)
  render(<App />)
  await screen.findByText('Protected dashboard')
  let body!: ReadableStreamDefaultController<Uint8Array>
  let reading!: () => void
  const bodyReading = new Promise<void>((resolve) => { reading = resolve })
  const response = new Response(new ReadableStream<Uint8Array>({ start(controller) { body = controller } }))
  const readText = response.text.bind(response)
  vi.spyOn(response, 'text').mockImplementation(() => { reading(); return readText() })
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).includes('/sessions/slow-body')) return response
    return new Response('{}', { status: 401 })
  }))
  const slow = api.getSession('slow-body')
  await bodyReading
  await act(async () => { await expect(api.listSessions()).rejects.toThrow() })
  if (mode === 'disabled') {
    expect(authSnapshot().status).not.toBe('revoked')
    expect(sessionStorage.getItem('cao.browser.bearer')).toBeNull()
  }
  body.enqueue(new TextEncoder().encode(JSON.stringify({ name: 'slow-body', terminals: [] })))
  body.close()
  await expect(slow).rejects.toThrow()
})

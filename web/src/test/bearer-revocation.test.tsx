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

function bearerServer() {
  const server = { rejected: false }
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/auth/config')) return new Response(JSON.stringify({ mode: 'bearer' }))
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

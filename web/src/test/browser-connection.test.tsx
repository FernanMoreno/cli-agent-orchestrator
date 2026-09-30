import { afterEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { BrowserConnection } from '../components/BrowserConnection'

afterEach(() => { cleanup(); sessionStorage.clear(); vi.unstubAllGlobals() })

it('shows authentication required when protected polling returns 401', () => {
  render(<BrowserConnection />)
  act(() => { window.dispatchEvent(new Event('cao-auth-required')) })
  expect(screen.getByRole('dialog')).toBeInTheDocument()
  expect(screen.getByRole('alert')).toHaveTextContent('Se requiere autenticación o el acceso ha caducado')
  expect(screen.getByLabelText('Token de acceso de CAO')).toHaveAttribute('type', 'password')
})

it('validates the entered credential against the server and retains the dialog on rejection', async () => {
  const fetch = vi.fn().mockResolvedValue(new Response('{}', { status: 401 }))
  vi.stubGlobal('fetch', fetch)
  render(<BrowserConnection />)
  fireEvent.click(screen.getByRole('button', { name: 'Conectar' }))
  fireEvent.change(screen.getByLabelText('Token de acceso de CAO'), { target: { value: 'invalid.operator.token' } })
  fireEvent.submit(screen.getByLabelText('Token de acceso de CAO').closest('form')!)
  expect(await screen.findByText('No se ha podido conectar. Comprueba el servidor y renueva tu token de acceso.')).toBeInTheDocument()
  expect(sessionStorage.getItem('cao.browser.bearer')).toBeNull()
  expect(new Headers(fetch.mock.calls[0][1].headers).get('Authorization')).toBe('Bearer invalid.operator.token')
})

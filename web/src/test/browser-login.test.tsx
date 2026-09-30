import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { BrowserLogin } from '../components/BrowserLogin'
import App from '../App'
vi.mock('../components/AgentPanel',()=>({AgentPanel:()=>null}))
vi.mock('../components/ProfilesPanel',()=>({ProfilesPanel:()=>null}))
vi.mock('../components/DashboardHome',()=>({DashboardHome:()=> <div>Protected dashboard</div>}))
afterEach(()=>{cleanup();vi.unstubAllGlobals()})
it('offers password manager fields and opt-in remembering',()=>{
 render(<BrowserLogin />)
 expect(screen.getByLabelText('Usuario')).toHaveAttribute('autocomplete','username')
 expect(screen.getByLabelText('Contraseña')).toHaveAttribute('autocomplete','current-password')
 expect(screen.getByLabelText('Recordar este navegador')).not.toBeChecked()
})
it('never mounts protected views before bootstrap verifies a cookie',async()=>{
 const fetch=vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({mode:'local_password'}))).mockResolvedValueOnce(new Response(JSON.stringify({detail:{code:'session_required',message:'Sign in required'}}),{status:401}))
 vi.stubGlobal('fetch',fetch); render(<App />)
 expect(screen.queryByText('Protected dashboard')).toBeNull()
 expect(await screen.findByLabelText('Usuario')).toBeInTheDocument()
 expect(fetch.mock.calls.map(call=>call[0])).toEqual(['/auth/config','/auth/session'])
})
it('clears password after rejected sign in and uses a generic error',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify({detail:{code:'credentials_rejected',message:'Credentials rejected'}}),{status:401})))
 render(<BrowserLogin />)
 fireEvent.change(screen.getByLabelText('Usuario'),{target:{value:'operator'}})
 fireEvent.change(screen.getByLabelText('Contraseña'),{target:{value:'bad password'}})
 fireEvent.submit(screen.getByLabelText('Contraseña').closest('form')!)
 expect(await screen.findByRole('alert')).toHaveTextContent('Usuario o contraseña incorrectos.')
 expect(screen.getByLabelText('Contraseña')).toHaveValue('')
})

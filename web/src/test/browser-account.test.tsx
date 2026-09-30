import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { BrowserAccount } from '../components/BrowserAccount'
import { initializeBrowserAuth, authSnapshot } from '../auth'
const session={session_id:'one',username:'operator',remembered:true,access_expires_at:4600,idle_expires_at:9000,absolute_expires_at:10000,server_time:1000,session_revision:1}
afterEach(()=>{cleanup();vi.unstubAllGlobals()})
it('offers revocation controls and password-manager change fields',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({mode:'local_password'}))).mockResolvedValueOnce(new Response(JSON.stringify(session))))
 await initializeBrowserAuth(); render(<BrowserAccount />)
 expect(screen.getByRole('button',{name:'Cerrar todas las sesiones'})).toBeInTheDocument()
 fireEvent.click(screen.getByRole('button',{name:'Cambiar contraseña'}))
 expect(screen.getByLabelText('Contraseña actual')).toHaveAttribute('autocomplete','current-password')
 expect(screen.getByLabelText('Nueva contraseña')).toHaveAttribute('autocomplete','new-password')
})
it('returns to login after current session is revoked',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({mode:'local_password'}))).mockResolvedValueOnce(new Response(JSON.stringify(session))).mockResolvedValueOnce(new Response(null,{status:204})).mockResolvedValueOnce(new Response(JSON.stringify({detail:{code:'session_revoked'}}),{status:401})))
 await initializeBrowserAuth(); render(<BrowserAccount />)
 fireEvent.click(screen.getByRole('button',{name:'Cerrar sesión'}))
 await vi.waitFor(()=>expect(authSnapshot().status).toBe('anonymous'))
})

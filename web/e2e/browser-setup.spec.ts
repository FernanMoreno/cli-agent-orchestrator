import { test, expect } from '@playwright/test'
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { createInterface } from 'node:readline'
import { resolve } from 'node:path'

let child: ChildProcessWithoutNullStreams
let origin: string
let bootstrapToken: string
const responses: Record<string, unknown>[] = []
async function waitFor(predicate: (value: Record<string, unknown>) => boolean) {
  const deadline = Date.now() + 60_000
  while (Date.now() < deadline) {
    const i = responses.findIndex(predicate)
    if (i >= 0) return responses.splice(i, 1)[0]
    if (child.exitCode !== null) throw new Error('Setup fixture exited')
    await new Promise(resolve => setTimeout(resolve, 50))
  }
  throw new Error('Setup fixture timeout')
}
test.beforeAll(async () => {
  child = spawn('uv', ['run', 'python', 'test/fixtures/browser_auth_runtime.py', '--setup'], { cwd: resolve(import.meta.dirname, '../..'), stdio: 'pipe' })
  // Never log the bootstrap capability emitted through this private pipe.
  createInterface({ input: child.stdout }).on('line', line => { try { responses.push(JSON.parse(line)) } catch {} })
  const ready = await waitFor(value => value.ready === true)
  origin = String(ready.origin); bootstrapToken = String(ready.bootstrap_token)
})
test.afterAll(async () => {
  if (child && child.exitCode === null) {
    child.stdin.write('{"action":"stop"}\n')
    await new Promise<void>(resolve => {
      const timer = setTimeout(() => { child.kill('SIGTERM'); resolve() }, 15_000)
      child.once('exit', () => { clearTimeout(timer); resolve() })
    })
  }
})

test('authorized web setup connects the main dashboard and survives server restart', async ({ page, context }) => {
  const password = 'TenChars1!'
  await page.goto(origin)
  const unauthorized = await page.evaluate(async password => {
    const r = await fetch('/auth/setup', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CAO-Browser': '1' }, body: JSON.stringify({ username: 'felni', password, remember: true }) })
    return r.status
  }, password)
  expect(unauthorized).toBe(401)
  await page.goto(`${origin}/#cao_token=${bootstrapToken}`)
  await expect(page.getByRole('heading', { name: 'Iniciar sesión' })).toBeVisible()
  await page.getByRole('button', { name: 'Crear cuenta', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Crear cuenta' })).toBeVisible()
  expect(page.url()).not.toContain('cao_token')
  await expect(page.getByRole('button', { name: /^Cerrar sesión$/i })).not.toBeVisible()
  await page.getByLabel('Usuario', { exact: true }).fill('felni')
  await page.getByLabel('Contraseña', { exact: true }).fill(password)
  await page.getByLabel('Confirmar contraseña', { exact: true }).fill(password)
  await page.getByLabel('Recordar este navegador').check()
  await page.getByRole('button', { name: 'Crear cuenta y entrar' }).click()
  await expect(page.getByRole('button', { name: /^Cerrar sesión$/i })).toBeVisible()
  const config = await page.evaluate(async () => (await fetch('/auth/config')).json())
  expect(config.mode).toBe('local_password')
  const cookies = await context.cookies(origin)
  const cookie = cookies.find(c => c.name.startsWith('cao_browser_'))!
  expect(cookie.httpOnly).toBe(true)
  const storage = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage }, cookie: document.cookie }))
  expect(storage).not.toContain(bootstrapToken)
  expect(storage).not.toContain(password)
  expect(storage).not.toContain(cookie.value)
  child.stdin.write('{"action":"restart"}\n')
  await waitFor(value => value.ack === 'restart')
  await page.reload()
  await expect(page.getByRole('button', { name: /^Cerrar sesión$/i })).toBeVisible()
  await page.getByRole('button', { name: /^Cerrar sesión$/i }).click()
  await expect(page.getByLabel(/^Usuario$/i)).toBeVisible()
  await page.getByLabel(/^Usuario$/i).fill('felni')
  await page.getByLabel(/^Contraseña$/i).fill(password)
  await page.getByRole('button', { name: /^Iniciar sesión$/i }).click()
  await expect(page.getByRole('button', { name: /^Cerrar sesión$/i })).toBeVisible()
})

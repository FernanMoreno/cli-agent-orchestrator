import { test, expect, type Page, type BrowserContext } from '@playwright/test'
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { createInterface } from 'node:readline'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

const password = 'Fixture-password-484!'
let child: ChildProcessWithoutNullStreams
let origin: string
let terminalId: string
const responses: Record<string, unknown>[] = []
let startupError = ''

async function message(predicate: (value: Record<string, unknown>) => boolean) {
  const deadline = Date.now() + 60_000
  while (Date.now() < deadline) {
    const index = responses.findIndex(predicate)
    if (index >= 0) return responses.splice(index, 1)[0]
    if (child.exitCode !== null) throw new Error(`Fixture exited ${child.exitCode}: ${startupError}`)
    await new Promise(resolve => setTimeout(resolve, 50))
  }
  throw new Error(`Fixture control timed out: ${startupError}`)
}
async function control(action: string, seconds?: number) {
  child.stdin.write(JSON.stringify({ action, seconds }) + '\n')
  await message(value => value.ack === action)
}
async function request(page: Page, path: string, method = 'GET', body?: unknown, extra?: Record<string, string>) {
  return page.evaluate(async ({ path, method, body, extra }) => {
    const response = await fetch(path, {
      method, credentials: 'same-origin', redirect: 'error',
      headers: { 'X-CAO-Browser': '1', ...(body ? { 'Content-Type': 'application/json' } : {}), ...extra },
      ...(body ? { body: JSON.stringify(body) } : {}),
    })
    const text = await response.text()
    return { status: response.status, body: text ? JSON.parse(text) : null,
      setCookie: response.headers.get('set-cookie'), retry: response.headers.get('retry-after') }
  }, { path, method, body, extra })
}
async function login(page: Page, remember = false) {
  await page.goto(origin)
  await page.getByLabel(/^Usuario$/i).fill('operator')
  await page.getByLabel(/^Contraseña$/i).fill(password)
  if (remember) await page.getByLabel(/Recordar este navegador/i).check()
  await page.getByRole('button', { name: /^Iniciar sesión$/i }).click()
  await expect(page.getByRole('button', { name: /^Cerrar sesión$/i })).toBeVisible()
  expect((await request(page, '/auth/session')).status).toBe(200)
}
async function cookie(context: BrowserContext) {
  const cookies = await context.cookies(origin)
  return cookies.find(value => value.name === 'cao_browser_acceptance')!
}

test.beforeAll(async () => {
  child = spawn('uv', ['run', 'python', 'test/fixtures/browser_auth_runtime.py'], {
    cwd: resolve(import.meta.dirname, '../..'), stdio: 'pipe',
  })
  child.stderr.on('data', data => { startupError = (startupError + data.toString()).slice(-4000) })
  createInterface({ input: child.stdout }).on('line', line => {
    try { responses.push(JSON.parse(line)) } catch { startupError = (startupError + '\n' + line).slice(-4000) }
  })
  const ready = await message(value => value.ready === true)
  origin = String(ready.origin)
  terminalId = String(ready.terminal_id)
})
test.afterAll(async () => {
  if (child && child.exitCode === null) {
    child.stdin.write('{"action":"stop"}\n')
    await new Promise<void>(resolve => {
      const timeout = setTimeout(() => { child.kill('SIGTERM'); resolve() }, 20_000)
      child.once('exit', () => { clearTimeout(timeout); resolve() })
    })
  }
})

test('clean login, generic credential failures, real HttpOnly cookie and no script secrets', async ({ page, context }) => {
  await page.goto(origin)
  await expect(page.getByLabel(/^Usuario$/i)).toBeVisible()
  expect((await request(page, '/sessions')).status).toBe(401)
  const wrong = await request(page, '/auth/login', 'POST', { username: 'operator', password: 'wrong', remember: false })
  const absent = await request(page, '/auth/login', 'POST', { username: 'absent', password: 'wrong', remember: false })
  expect(wrong.status).toBe(401)
  expect(absent.body).toEqual(wrong.body)
  await login(page)
  const actual = await cookie(context)
  expect(actual.httpOnly).toBe(true)
  expect(actual.sameSite).toBe('Strict')
  expect(actual.expires).toBe(-1)
  expect((await request(page, '/sessions')).status).toBe(200)
  const accessible = await page.evaluate(() => JSON.stringify({ cookie: document.cookie, local: { ...localStorage }, session: { ...sessionStorage }, url: location.href }))
  expect(accessible).not.toContain(actual.value)
  expect(accessible).not.toContain(password)
  expect(page.url()).not.toMatch(/access_token|bearer|token=/i)
  await page.reload()
  await expect(page.getByRole('button', { name: /^Cerrar sesión$/i })).toBeVisible()
})

test('eight hour continuity, three expired leases, concurrent tabs and server restart', async ({ context, page }) => {
  await login(page, true)
  const initial = (await request(page, '/auth/session')).body
  const tab = await context.newPage()
  await tab.goto(origin)
  for (let index = 0; index < 4; index++) {
    await control('advance', 2 * 3600)
    const renewals = await Promise.all([request(page, '/auth/renew', 'POST'), request(tab, '/auth/renew', 'POST')])
    for (const result of renewals) {
      expect(result.status).toBe(200)
      expect(result.body.session_id).toBe(initial.session_id)
      expect(result.setCookie).toBeNull()
    }
    expect((await request(tab, '/sessions')).status).toBe(200)
  }
  const before = (await cookie(context)).value
  await control('restart')
  const restarted = responses.findIndex(value => value.ready === true)
  if (restarted >= 0) terminalId = String(responses.splice(restarted, 1)[0].terminal_id)
  await page.reload()
  await expect(page.getByRole('button', { name: /^Cerrar sesión$/i })).toBeVisible()
  expect((await request(page, '/auth/session')).body.session_id).toBe(initial.session_id)
  expect((await cookie(context)).value).toBe(before)
})

test('remembered cookie survives actual Chromium process close and same profile reopen', async ({ playwright }) => {
  const directory = await mkdtemp(join(tmpdir(), 'cao-browser-profile-'))
  let context = await playwright.chromium.launchPersistentContext(directory, { headless: true })
  try {
    await login(context.pages()[0], true)
    const saved = await cookie(context)
    expect(saved.expires).toBeGreaterThan(Date.now() / 1000)
    await context.close()
    context = await playwright.chromium.launchPersistentContext(directory, { headless: true })
    const page = context.pages()[0]
    await page.goto(origin)
    await expect(page.getByRole('button', { name: /^Cerrar sesión$/i })).toBeVisible()
    expect((await cookie(context)).value).toBe(saved.value)
  } finally {
    await context.close()
    await rm(directory, { recursive: true, force: true })
  }
})

test('temporary cookie authorizes reload but new clean browser session requires login', async ({ browser }) => {
  let context = await browser.newContext()
  try {
    const page = await context.newPage()
    await login(page)
    expect((await cookie(context)).expires).toBe(-1)
    await page.reload()
    expect((await request(page, '/auth/session')).status).toBe(200)
    await context.close()
    context = await browser.newContext()
    const clean = await context.newPage()
    await clean.goto(origin)
    await expect(clean.getByLabel(/^Usuario$/i)).toBeVisible()
    expect((await request(clean, '/auth/session')).status).toBe(401)
  } finally { await context.close() }
})

test('current logout preserves other session; logout all and password change revoke peers', async ({ browser, page }) => {
  await login(page)
  const second = await browser.newContext()
  try {
    const peer = await second.newPage()
    await login(peer)
    const old = (await cookie(page.context())).value
    expect((await request(page, '/auth/logout', 'POST')).status).toBe(204)
    expect((await cookie(page.context())).value).toBe(old)
    expect((await request(page, '/sessions')).status).toBe(401)
    expect((await request(peer, '/sessions')).status).toBe(200)
    await login(page)
    expect((await request(page, '/auth/logout-all', 'POST')).status).toBe(204)
    expect((await request(peer, '/sessions')).status).toBe(401)
    await login(page)
    await login(peer)
    expect((await request(page, '/auth/password', 'POST', { current_password: password, new_password: password + 'new' })).status).toBe(204)
    expect((await request(peer, '/sessions')).status).toBe(401)
    const oldLogin = await request(page, '/auth/login', 'POST', { username: 'operator', password, remember: false })
    expect(oldLogin.status).toBe(401)
    await control('reset')
    await login(page)
  } finally { await second.close() }
})

test('offline preserves cookie and reconnect restores authority', async ({ page, context }) => {
  await login(page, true)
  const before = await cookie(context)
  await context.setOffline(true)
  const unavailable = await page.evaluate(async () => {
    try { await fetch('/auth/session'); return false } catch { return true }
  })
  expect(unavailable).toBe(true)
  expect((await cookie(context)).value).toBe(before.value)
  await context.setOffline(false)
  expect((await request(page, '/auth/session')).status).toBe(200)
})

test('foreign-origin effects and malformed credential bodies fail without disclosure', async ({ page }) => {
  await login(page)
  // APIRequestContext sends an explicit foreign origin while retaining real cookies.
  const denied = await page.context().request.post(origin + '/auth/logout', {
    headers: { Origin: 'http://127.0.0.1:1', 'X-CAO-Browser': '1' },
  })
  expect(denied.status()).toBe(403)
  expect((await request(page, '/auth/session')).status).toBe(200)
  const malformed = await request(page, '/auth/login', 'POST', { username: 'operator', password: { secret: password }, remember: false })
  expect(malformed.status).toBe(422)
  expect(JSON.stringify(malformed.body)).not.toContain(password)
  const invalidBearer = await request(page, '/sessions', 'GET', undefined, { Authorization: 'Bearer invalid' })
  expect(invalidBearer.status).toBe(401)
})

test('idle polling and renew cannot extend temporary lifetime; clock rollback fails closed', async ({ page }) => {
  await login(page)
  for (let index = 0; index < 4; index++) {
    await control('advance', 2 * 3600 - 1)
    expect((await request(page, '/auth/session')).status).toBe(200)
    expect((await request(page, '/auth/renew', 'POST')).status).toBe(200)
  }
  await control('advance', 5)
  expect((await request(page, '/auth/renew', 'POST')).status).toBe(401)
  await login(page)
  await control('advance', -10)
  expect((await request(page, '/auth/session')).status).toBe(503)
  await control('advance', 10)
  expect((await request(page, '/auth/session')).status).toBe(200)
})

test('silent real SSE stream closes within five seconds of committed logout', async ({ page }) => {
  await login(page)
  await page.evaluate(async () => {
    const response = await fetch('/agui/v1/stream', { credentials: 'same-origin' })
    if (!response.ok) throw new Error(`SSE HTTP ${response.status}`)
    const reader = response.body!.getReader()
    const target = window as unknown as { acceptanceStreamDone: boolean; acceptanceFrames: number }
    target.acceptanceStreamDone = false
    target.acceptanceFrames = 0
    void (async () => {
      try {
        while (true) {
          const value = await reader.read()
          if (value.done) break
          target.acceptanceFrames++
        }
      } finally { target.acceptanceStreamDone = true }
    })()
  })
  await expect.poll(() => page.evaluate(() => (window as unknown as { acceptanceFrames: number }).acceptanceFrames)).toBeGreaterThan(0)
  const committed = Date.now()
  expect((await request(page, '/auth/logout', 'POST')).status).toBe(204)
  await expect.poll(() => page.evaluate(() => (window as unknown as { acceptanceStreamDone: boolean }).acceptanceStreamDone), { timeout: 5000 }).toBe(true)
  expect(Date.now() - committed).toBeLessThanOrEqual(5000)
  expect((await request(page, '/sessions')).status).toBe(401)
})

test('stale logout cannot revoke or overwrite a newly logged-in cookie', async ({ page, context }) => {
  await login(page)
  const original = await cookie(context)
  expect((await request(page, '/auth/logout', 'POST')).status).toBe(204)
  await login(page)
  const replacement = await cookie(context)
  expect(replacement.value).not.toBe(original.value)
  const stale = await context.request.post(origin + '/auth/logout', {
    headers: { Origin: origin, 'X-CAO-Browser': '1', Cookie: `${original.name}=${original.value}` },
  })
  expect(stale.status()).toBe(204)
  expect(stale.headers()['set-cookie']).toBeUndefined()
  expect((await cookie(context)).value).toBe(replacement.value)
  expect((await request(page, '/sessions')).status).toBe(200)
})

test('concurrent bad logins hit server throttle and local recovery remains available', async ({ page }) => {
  await page.goto(origin)
  const failures = await Promise.all(Array.from({ length: 12 }, () =>
    request(page, '/auth/login', 'POST', { username: 'nonexistent', password: 'wrong', remember: false })))
  expect(failures.some(value => value.status === 429)).toBe(true)
  expect(failures.every(value => value.status === 401 || value.status === 429)).toBe(true)
  const throttled = await request(page, '/auth/login', 'POST', { username: 'operator', password, remember: false })
  expect(throttled.status).toBe(429)
  expect(Number(throttled.retry)).toBeGreaterThan(0)
  await control('advance', 61)
  await login(page)
  await control('reset')
  expect((await request(page, '/sessions')).status).toBe(401)
  await login(page)
})

test('real terminal WebSocket handles traffic then revokes within five seconds without stopping agent', async ({ page }) => {
  await login(page)
  await page.evaluate(async terminalId => {
    const target = window as unknown as { acceptanceSocket: WebSocket; acceptanceSocketClosed: number; acceptanceOutput: string }
    const socket = new WebSocket(`${location.origin.replace('http', 'ws')}/terminals/${terminalId}/ws`)
    socket.binaryType = 'arraybuffer'
    target.acceptanceSocket = socket
    target.acceptanceSocketClosed = 0
    target.acceptanceOutput = ''
    socket.onmessage = event => {
      target.acceptanceOutput += typeof event.data === 'string' ? event.data : new TextDecoder().decode(event.data)
    }
    socket.onclose = event => { target.acceptanceSocketClosed = event.code }
    await new Promise<void>((resolve, reject) => {
      socket.onopen = () => resolve()
      socket.onerror = () => reject(new Error('authenticated terminal WebSocket failed'))
    })
    socket.send(JSON.stringify({ type: 'input', data: 'browser-fixture-input\r' }))
  }, terminalId).catch(error => { throw new Error(`${error.message}\nFixture diagnostics: ${startupError}`) })
  await expect.poll(() => page.evaluate(() => (window as unknown as { acceptanceOutput: string }).acceptanceOutput)).toContain('browser-fixture-input')
  const committed = Date.now()
  expect((await request(page, '/auth/logout', 'POST')).status).toBe(204)
  await expect.poll(() => page.evaluate(() => (window as unknown as { acceptanceSocketClosed: number }).acceptanceSocketClosed), { timeout: 5000 }).toBe(4401)
  expect(Date.now() - committed).toBeLessThanOrEqual(5000)
  await control('terminal_alive')
})

test('real personal backup and restore invalidate remembered cookies and preserve account', async ({ page, context }) => {
  await login(page, true)
  const before = await cookie(context)
  await control('restore')
  expect((await request(page, '/auth/session')).status).toBe(401)
  expect((await cookie(context)).value).toBe(before.value)
  await page.reload()
  await expect(page.getByLabel(/^Usuario$/i)).toBeVisible()
  await login(page, true)
  expect((await cookie(context)).value).not.toBe(before.value)
  expect((await request(page, '/sessions')).status).toBe(200)
})

test('busy real SQLite fails closed without deleting the cookie and recovers after unlocking', async ({ page, context }) => {
  await login(page, true)
  const before = await cookie(context)
  await control('lock_storage')
  try {
    expect((await request(page, '/auth/session')).status).toBe(503)
    expect((await request(page, '/sessions')).status).toBe(503)
    expect((await cookie(context)).value).toBe(before.value)
  } finally { await control('unlock_storage') }
  expect((await request(page, '/auth/session')).status).toBe(200)
})

test('functional activity preserves idle while absolute deadline still terminates remembered session', async ({ page }) => {
  await login(page, true)
  const initial = (await request(page, '/auth/session')).body
  for (let index = 0; index < 4; index++) {
    await control('advance', 6 * 86400)
    expect((await request(page, '/auth/renew', 'POST')).status).toBe(200)
    expect((await request(page, '/agents/profiles')).status).toBe(200)
    const session = (await request(page, '/auth/session')).body
    expect(session.absolute_expires_at).toBe(initial.absolute_expires_at)
  }
  await control('advance', 6 * 86400 + 1)
  expect((await request(page, '/auth/renew', 'POST')).status).toBe(401)
})

test('cookie refusal at the browser network boundary explains failure without JavaScript secret fallback', async ({ page, context }) => {
  // Contact the real server, then simulate a browser/proxy that refuses Set-Cookie.
  // No API body or authentication result is fabricated.
  await context.route('**/auth/login', async route => {
    const response = await route.fetch()
    const headers = response.headers()
    delete headers['set-cookie']
    await context.clearCookies()
    await route.fulfill({ response, headers })
  })
  await page.goto(origin)
  await page.getByLabel(/^Usuario$/i).fill('operator')
  await page.getByLabel(/^Contraseña$/i).fill(password)
  await page.getByRole('button', { name: /^Iniciar sesión$/i }).click()
  await expect(page.getByRole('alert')).toContainText(/cookies.*bloqueadas|permite las cookies/i)
  expect(await cookie(context)).toBeUndefined()
  const storage = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }))
  expect(storage).not.toContain(password)
  await expect(page.getByRole('button', { name: /^Cerrar sesión$/i })).toHaveCount(0)
})

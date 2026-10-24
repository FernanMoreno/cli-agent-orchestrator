import assert from "node:assert/strict"
import fs from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import test from "node:test"
import { pathToFileURL } from "node:url"

const SOURCE = new URL("./response_hook_probe.js", import.meta.url)
const PROVIDER_ID = "t127fake"
const MODEL_ID = "toolarg-model"
const NONCE = "0123456789abcdef0123456789abcdef"
const MARKER = "t127-wire-control"

async function workspace(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "t127-response-probe-"))
  await fs.chmod(root, 0o700)
  const work = path.join(root, "work")
  const opencode = path.join(work, ".opencode")
  const plugins = path.join(opencode, "plugins")
  await fs.mkdir(plugins, { recursive: true, mode: 0o700 })
  for (const directory of [work, opencode, plugins]) await fs.chmod(directory, 0o700)
  const pluginPath = path.join(plugins, "response_hook_probe.js")
  await fs.writeFile(pluginPath, await fs.readFile(SOURCE), { flag: "wx", mode: 0o600 })
  await fs.chmod(pluginPath, 0o600)
  const ledgerPath = path.join(opencode, "http-response-probe")
  t.after(async () => fs.rm(root, { recursive: true, force: true }))
  const module = await import(pathToFileURL(pluginPath).href)
  return { root, work, opencode, pluginPath, ledgerPath, module }
}

function hookContext(captured, options = {}, dispose = async () => { captured.disposed += 1 }) {
  return {
    options,
    session: {
      hook: async (name, callback, hookOptions) => {
        captured.name = name
        captured.callback = callback
        captured.options = hookOptions
        return { dispose }
      },
    },
  }
}

async function activeProbe(t, dispose) {
  const fixture = await workspace(t)
  const captured = { disposed: 0 }
  const cleanup = await fixture.module.default.setup(hookContext(captured, {
    nonce: NONCE,
    providerID: PROVIDER_ID,
    modelID: MODEL_ID,
    ledgerPath: fixture.ledgerPath,
  }, dispose))
  assert.equal(captured.name, "http.response")
  assert.deepEqual(captured.options, { providerID: PROVIDER_ID })
  assert.equal(typeof cleanup, "function")
  return { ...fixture, captured, cleanup }
}

function responseFromChunks(chunks, delayMs = 0) {
  let index = 0
  const body = new ReadableStream({
    async pull(controller) {
      if (delayMs) await new Promise((resolve) => setTimeout(resolve, delayMs))
      if (index >= chunks.length) return controller.close()
      controller.enqueue(chunks[index++])
    },
  })
  return new Response(body, {
    status: 200,
    headers: { "content-type": "text/event-stream", "x-control": "fixed" },
  })
}

function eventFor(response, { providerID = PROVIDER_ID, modelID = MODEL_ID, kind = "primary" } = {}) {
  return {
    kind,
    model: { providerID, id: modelID },
    request: new Request("https://provider.invalid/v1/chat/completions", { method: "POST" }),
    response,
    sessionID: "private-test-session",
  }
}

async function records(ledgerPath) {
  return (await fs.readFile(path.join(ledgerPath, "events.jsonl"), "utf8"))
    .trimEnd().split("\n").map((line) => JSON.parse(line))
}

async function seal(ledgerPath) {
  return JSON.parse(await fs.readFile(path.join(ledgerPath, "seal.json"), "utf8"))
}

test("default and factory stay disabled without an explicit valid nonce", async (t) => {
  const fixture = await workspace(t)
  const captured = { disposed: 0 }
  const context = hookContext(captured, {
    providerID: PROVIDER_ID,
    modelID: MODEL_ID,
    ledgerPath: fixture.ledgerPath,
  })
  assert.equal(await fixture.module.default.setup(context), undefined)
  assert.equal(captured.callback, undefined)
  const factory = fixture.module.createHttpResponseProbe({
    moduleUrl: pathToFileURL(fixture.pluginPath).href,
    ledgerPath: fixture.ledgerPath,
    providerID: PROVIDER_ID,
    modelID: MODEL_ID,
  })
  assert.equal(await factory.setup(context), undefined)
  assert.equal(captured.callback, undefined)
  await assert.rejects(fs.stat(fixture.ledgerPath), { code: "ENOENT" })
})

test("response clone sees only the public marker and preserves the original response", async (t) => {
  const probe = await activeProbe(t)
  assert.equal((await fs.stat(probe.ledgerPath)).mode & 0o777, 0o700)
  const chunks = [
    Buffer.from('data: {"id":"t127-wire-', "ascii"),
    Buffer.from('control","choices":[]}\n\n', "ascii"),
  ]
  const expected = Buffer.concat(chunks)
  const response = responseFromChunks(chunks, 20)
  const headers = [...response.headers.entries()]
  const event = eventFor(response)
  assert.equal(probe.captured.callback(eventFor(response, { kind: "title" })), undefined)
  assert.equal(probe.captured.callback(eventFor(response, { modelID: "other-model" })), undefined)
  assert.equal(probe.captured.callback(event), undefined)
  assert.strictEqual(event.response, response)
  const cleanupStarted = performance.now()
  await probe.cleanup()
  assert.ok(performance.now() - cleanupStarted < 500)
  assert.equal(probe.captured.disposed, 1)
  assert.strictEqual(event.response, response)
  assert.equal(response.status, 200)
  assert.deepEqual([...response.headers.entries()], headers)
  const original = Buffer.from(await response.arrayBuffer())
  assert.equal(original.length, expected.length)
  assert.equal(Buffer.compare(original, expected), 0)

  const rows = await records(probe.ledgerPath)
  assert.deepEqual(rows.map((row) => row.event), ["hook", "response"])
  assert.deepEqual(rows.map((row) => row.status), ["registered", "clone_completed"])
  assert.equal(rows[1].synthetic_wire_marker_seen, true)
  assert.equal(rows[1].clone_completed, true)
  assert.equal(rows[1].original_not_replaced, true)
  const raw = await fs.readFile(path.join(probe.ledgerPath, "events.jsonl"), "utf8")
  assert.equal(raw.includes(MARKER), false)
  assert.equal(raw.includes("private-test-session"), false)
  assert.equal(raw.includes("provider.invalid"), false)
  const final = await seal(probe.ledgerPath)
  assert.equal(final.status, "sealed")
  assert.equal(final.cleanup_complete, true)
  assert.equal(final.selected_primary_response_seen, true)
  assert.equal(final.synthetic_wire_marker_seen, true)
  assert.equal(final.clone_completed, true)
  assert.equal(final.inspection_unavailable, false)
  for (const filename of ["events.jsonl", "seal.json"]) {
    assert.equal((await fs.stat(path.join(probe.ledgerPath, filename))).mode & 0o777, 0o600)
  }
  await assert.rejects(fs.stat(path.join(probe.ledgerPath, "seal.tmp")), { code: "ENOENT" })
})

test("seal creation does not replace a preexisting reservation", async (t) => {
  const probe = await activeProbe(t)
  const chunks = [Buffer.from('data: {"id":"t127-wire-control"}\n\n', "ascii")]
  const response = responseFromChunks(chunks)
  assert.equal(probe.captured.callback(eventFor(response)), undefined)
  const sealPath = path.join(probe.ledgerPath, "seal.json")
  const reserved = Buffer.from("preexisting-seal-reservation", "ascii")
  await fs.writeFile(sealPath, reserved, { flag: "wx", mode: 0o600 })
  await probe.cleanup()

  const actual = await fs.readFile(sealPath)
  assert.equal(actual.length, reserved.length)
  assert.equal(Buffer.compare(actual, reserved), 0)
  assert.throws(() => JSON.parse(actual.toString("utf8")))
  await assert.rejects(fs.stat(path.join(probe.ledgerPath, "seal.tmp")), { code: "ENOENT" })
  assert.equal((await records(probe.ledgerPath)).some((row) => row.event === "response"), true)
})

test("never-resolving hook disposal is bounded and leaves an incomplete seal", async (t) => {
  const probe = await activeProbe(t, () => new Promise(() => {}))
  const chunks = [Buffer.from('data: {"id":"t127-wire-control"}\n\n', "ascii")]
  const response = responseFromChunks(chunks)
  const headers = [...response.headers.entries()]
  const event = eventFor(response)
  assert.equal(probe.captured.callback(event), undefined)
  const started = performance.now()
  await probe.cleanup()
  assert.ok(performance.now() - started < 2500)
  assert.strictEqual(event.response, response)
  assert.equal(response.status, 200)
  assert.deepEqual([...response.headers.entries()], headers)
  const original = Buffer.from(await response.arrayBuffer())
  const expected = Buffer.concat(chunks)
  assert.equal(Buffer.compare(original, expected), 0)
  const final = await seal(probe.ledgerPath)
  assert.equal(final.status, "incomplete")
  assert.equal(final.hook_registered, true)
  assert.equal(final.cleanup_complete, false)
  assert.equal(final.original_not_replaced, true)
})

test("oversize and stalled clones fail closed within bounds without changing originals", async (t) => {
  const probe = await activeProbe(t)
  const oversized = Buffer.alloc(64 * 1024 + 1, 65)
  Buffer.from(MARKER, "ascii").copy(oversized)
  const largeResponse = responseFromChunks([oversized])
  const largeEvent = eventFor(largeResponse)
  const started = performance.now()
  assert.equal(probe.captured.callback(largeEvent), undefined)
  assert.ok(performance.now() - started < 100)
  assert.strictEqual(largeEvent.response, largeResponse)

  const stalledResponse = new Response(new ReadableStream({
    pull() { return new Promise(() => {}) },
  }), { status: 200, headers: { "content-type": "text/event-stream" } })
  const stalledEvent = eventFor(stalledResponse)
  const secondStarted = performance.now()
  assert.equal(probe.captured.callback(stalledEvent), undefined)
  assert.ok(performance.now() - secondStarted < 100)
  const cleanupStarted = performance.now()
  await probe.cleanup()
  assert.ok(performance.now() - cleanupStarted < 2500)
  assert.strictEqual(largeEvent.response, largeResponse)
  assert.strictEqual(stalledEvent.response, stalledResponse)
  const original = Buffer.from(await largeResponse.arrayBuffer())
  assert.equal(original.length, oversized.length)
  assert.equal(Buffer.compare(original, oversized), 0)

  const responseRows = (await records(probe.ledgerPath)).filter((row) => row.event === "response")
  assert.equal(responseRows.length, 2)
  assert.deepEqual(responseRows.map((row) => row.status), ["inspection_unavailable", "inspection_unavailable"])
  assert.deepEqual(responseRows.map((row) => row.synthetic_wire_marker_seen), [false, false])
  const final = await seal(probe.ledgerPath)
  assert.equal(final.status, "incomplete")
  assert.equal(final.cleanup_complete, true)
  assert.equal(final.clone_completed, false)
  assert.equal(final.inspection_unavailable, true)
  try { void stalledResponse.body.cancel().catch(() => {}) } catch {}
})

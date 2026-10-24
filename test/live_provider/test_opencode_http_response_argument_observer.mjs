import assert from "node:assert/strict"
import fs from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import test from "node:test"
import { pathToFileURL } from "node:url"
import defaultObserver, { createHttpResponseArgumentObserver } from "./opencode_http_response_argument_observer.js"

const NONCE = "0123456789abcdef0123456789abcdef"
const MODEL = { providerID: "opencode-go", id: "glm-5.3-flash" }
const MESSAGES = ["Antes: café, español y 日本語.", "Después: sesión continúa."]
const PROMPT = {
  schema_version: 1, nonce: NONCE,
  phase_prompts: ["source phase one LAST", "source phase two LAST"],
  receipt_prefix: "\nreceipt: ", receipt_suffix: "\nLAST", last_instruction: "LAST",
}

async function fixture(t, options = {}) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "cao-response-arguments-"))
  await fs.chmod(root, 0o700)
  const opencode = path.join(root, ".opencode")
  const plugins = path.join(opencode, "plugins")
  await fs.mkdir(plugins, { recursive: true, mode: 0o700 })
  await fs.chmod(opencode, 0o700)
  const pluginPath = path.join(plugins, "cao-http-response-argument-observer.js")
  await fs.writeFile(pluginPath, await fs.readFile(new URL("./opencode_http_response_argument_observer.js", import.meta.url)), { mode: 0o600 })
  await fs.writeFile(path.join(opencode, "http-prompt-expectations.json"), JSON.stringify(PROMPT), { mode: 0o600 })
  await fs.writeFile(path.join(opencode, "http-response-argument-expectations.json"), JSON.stringify({
    schema_version: 1, nonce: NONCE, phase_messages: MESSAGES,
  }), { mode: 0o600 })
  const hooks = new Map()
  const dispose = options.dispose ?? (async () => {})
  const observer = createHttpResponseArgumentObserver({
    nonce: NONCE, moduleUrl: pathToFileURL(pluginPath).href, ...options,
  })
  const cleanup = await observer.setup({ session: { hook: async (name, callback, filter) => {
    assert.deepEqual(filter, { providerID: "opencode-go" })
    hooks.set(name, callback)
    return { dispose }
  } } })
  t.after(async () => fs.rm(root, { recursive: true, force: true }))
  return { root, opencode, hooks, cleanup,
    ledger: path.join(opencode, "http-response-argument-observation") }
}

function requestFor(phase = 1) {
  return new Request("https://example.invalid/PRIVATE_URL", {
    method: "POST", headers: { Authorization: "Bearer PRIVATE_HEADER" },
    body: JSON.stringify({ messages: [{ role: "user", content:
      `${PROMPT.phase_prompts[phase - 1]}${PROMPT.receipt_prefix}CAO-TURN-RECEIPT-${"b".repeat(32)}${PROMPT.receipt_suffix}`,
    }] }),
  })
}

function responseFor(message) {
  return new Response(JSON.stringify({ choices: [{ message: { role: "assistant", tool_calls: [{
    id: "PRIVATE_CALL_ID", type: "function", function: { name: "cao-mcp-server_send_message",
      arguments: JSON.stringify({ message, receiver_id: "PRIVATE_RECEIVER" }) },
  }] }, finish_reason: "tool_calls" }] }), { headers: { "content-type": "application/json" } })
}

test("default observer remains disabled without an explicit binding", async () => {
  assert.equal(await defaultObserver.setup({}), undefined)
})

test("captures exact arguments while the client immediately consumes original request and response", async (t) => {
  const f = await fixture(t)
  for (const phase of [1, 2]) {
    const request = requestFor(phase)
    const event = { model: MODEL, kind: "primary", request }
    assert.equal(f.hooks.get("http.request")(event), undefined)
    const sent = await request.text()
    assert.ok(sent.includes("source phase"))
    const response = responseFor(MESSAGES[phase - 1])
    assert.equal(f.hooks.get("http.response")({ ...event, response }), undefined)
    const received = await response.text()
    assert.ok(received.includes("PRIVATE_RECEIVER"))
  }
  await f.cleanup()
  const seal = JSON.parse(await fs.readFile(path.join(f.ledger, "seal.json"), "utf8"))
  assert.equal(seal.trace_complete, true)
  assert.equal(seal.response_count, 2)
  for (const ordinal of [1, 2]) {
    const record = JSON.parse(await fs.readFile(path.join(f.ledger, `response-000${ordinal}.json`), "utf8"))
    assert.equal(record.status, "observed")
    assert.equal(record.phase_ordinal, ordinal)
    assert.equal(record.argument_exact, true)
    assert.equal(record.known_punctuation_variant_exact, false)
    assert.equal(record.original_not_replaced, true)
    assert.equal(record.clone_complete, true)
    const encoded = JSON.stringify(record)
    for (const secret of [...MESSAGES, "PRIVATE_RECEIVER", "PRIVATE_CALL_ID", "PRIVATE_HEADER", "PRIVATE_URL"]) {
      assert.equal(encoded.includes(secret), false)
    }
  }
})

test("ignores other models without consuming their bodies", async (t) => {
  const f = await fixture(t)
  const request = requestFor()
  const response = responseFor(MESSAGES[0])
  const event = { model: { ...MODEL, id: "other-model" }, kind: "primary", request, response }
  assert.equal(f.hooks.get("http.request")(event), undefined)
  assert.equal(f.hooks.get("http.response")(event), undefined)
  assert.equal(request.bodyUsed, false)
  assert.equal(response.bodyUsed, false)
  await f.cleanup()
  const seal = JSON.parse(await fs.readFile(path.join(f.ledger, "seal.json"), "utf8"))
  assert.equal(seal.request_count, 0)
  assert.equal(seal.response_count, 0)
})

test("failed hook disposal cannot produce a complete trace", async (t) => {
  const f = await fixture(t, { dispose: async () => { throw new Error("PRIVATE_DISPOSAL_ERROR") } })
  await f.cleanup()
  const encoded = await fs.readFile(path.join(f.ledger, "seal.json"), "utf8")
  const seal = JSON.parse(encoded)
  assert.equal(seal.hook_disposed, false)
  assert.equal(seal.trace_complete, false)
  assert.equal(encoded.includes("PRIVATE_DISPOSAL_ERROR"), false)
})

test("cleanup bounds a stalled response and prevents publication after returning", async (t) => {
  const f = await fixture(t)
  const request = requestFor()
  const event = { model: MODEL, kind: "primary", request }
  f.hooks.get("http.request")(event)
  await request.text()
  let streamController
  const response = new Response(new ReadableStream({ start(controller) { streamController = controller } }), {
    headers: { "content-type": "text/event-stream" },
  })
  f.hooks.get("http.response")({ ...event, response })
  const before = performance.now()
  const cleanup = f.cleanup()
  assert.equal(f.cleanup(), cleanup)
  await cleanup
  assert.ok(performance.now() - before < 3500)
  const files = (await fs.readdir(f.ledger)).filter((name) => !name.endsWith(".pending"))
  assert.equal(files.includes("seal.json"), false)
  // Completing the provider stream later must not retroactively publish evidence.
  streamController.close()
  await response.text()
  await new Promise((resolve) => setTimeout(resolve, 30))
  // In-flight temporary files may be removed during cancellation; no durable
  // response or seal may appear after the cleanup deadline.
  assert.deepEqual((await fs.readdir(f.ledger)).filter((name) => !name.endsWith(".pending")), files)
})

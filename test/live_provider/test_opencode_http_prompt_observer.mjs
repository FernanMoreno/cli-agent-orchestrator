import assert from "node:assert/strict"
import fs from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import test from "node:test"
import { pathToFileURL } from "node:url"

const SOURCE = new URL("./opencode_http_prompt_observer.js", import.meta.url)
const NONCE = "0123456789abcdef0123456789abcdef"
const MODEL = { providerID: "opencode-go", id: "glm-5.3-flash", variant: "balanced" }
const EXPECTED = {
  schema_version: 1,
  nonce: NONCE,
  phase_prompts: ["phase one source body LAST_SENTINEL", "phase two source body LAST_SENTINEL"],
  receipt_prefix: "\n\nreceipt: ",
  receipt_suffix: "\nLAST_SENTINEL",
  last_instruction: "LAST_SENTINEL",
}

function receipt(prompt, nonce = "a".repeat(32)) {
  return `${prompt}${EXPECTED.receipt_prefix}CAO-TURN-RECEIPT-${nonce}${EXPECTED.receipt_suffix}`
}

async function setupObserver(t, { expected = EXPECTED, nonce = NONCE } = {}) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "cao-http-prompt-"))
  await fs.chmod(root, 0o700)
  const working = path.join(root, "work")
  const opencode = path.join(working, ".opencode")
  const plugins = path.join(opencode, "plugins")
  await fs.mkdir(plugins, { recursive: true, mode: 0o700 })
  await fs.chmod(working, 0o700)
  await fs.chmod(opencode, 0o700)
  await fs.chmod(plugins, 0o700)
  const pluginPath = path.join(plugins, "observer.js")
  await fs.writeFile(pluginPath, await fs.readFile(SOURCE), { flag: "wx", mode: 0o600 })
  await fs.chmod(pluginPath, 0o600)
  if (expected !== null) {
    await fs.writeFile(
      path.join(opencode, "http-prompt-expectations.json"),
      JSON.stringify(expected),
      { flag: "wx", mode: 0o600 },
    )
    await fs.chmod(path.join(opencode, "http-prompt-expectations.json"), 0o600)
  }
  const source = await fs.readFile(pluginPath)
  const module = await import("data:text/javascript;base64," + source.toString("base64"))
  const plugin = module.createHttpPromptObserver({
    nonce,
    moduleUrl: pathToFileURL(pluginPath).href,
  })
  const captured = { callback: null, hookName: null, options: null, disposed: 0 }
  const cleanup = await plugin.setup({
    session: {
      hook: async (hookName, callback, options) => {
        captured.hookName = hookName
        captured.callback = callback
        captured.options = options
        return { dispose: async () => { captured.disposed += 1 } }
      },
    },
  })
  t.after(async () => fs.rm(root, { recursive: true, force: true }))
  const ledgerDirectory = path.join(opencode, "http-prompt-observation")
  return {
    root,
    working,
    opencode,
    plugins,
    plugin,
    pluginPath,
    captured,
    cleanup,
    ledgerDirectory,
    expectationPath: path.join(opencode, "http-prompt-expectations.json"),
  }
}

function requestFor(messages, extra = {}) {
  return new Request("https://example.invalid/SENSITIVE_URL", {
    method: "POST",
    headers: { Authorization: "Bearer SENSITIVE_HEADER", "content-type": "application/json" },
    body: JSON.stringify({ model: "untrusted-model", messages, ...extra }),
  })
}

function eventFor(request, { model = MODEL, kind = "primary" } = {}) {
  return { model, kind, request, sessionID: "PRIVATE_SESSION_ID", agent: "PRIVATE_AGENT" }
}

async function dispatch(observer, request, options = {}) {
  const event = eventFor(request, options)
  const originalRequest = event.request
  const originalBody = await originalRequest.clone().text()
  const returned = await observer.captured.callback(event)
  assert.equal(returned, undefined)
  assert.strictEqual(event.request, originalRequest)
  assert.deepEqual(event.model, options.model ?? MODEL)
  assert.equal(event.kind, options.kind ?? "primary")
  assert.equal(event.sessionID, "PRIVATE_SESSION_ID")
  assert.equal(event.agent, "PRIVATE_AGENT")
  assert.equal(await originalRequest.text(), originalBody)
  return event
}

async function readRecords(observer) {
  const names = (await fs.readdir(observer.ledgerDirectory))
    .filter((name) => /^request-[0-9]{4}\.json$/.test(name))
    .sort()
  return Promise.all(names.map(async (name) => JSON.parse(
    await fs.readFile(path.join(observer.ledgerDirectory, name), "utf8"),
  )))
}

function onlyUser(prompt) {
  return requestFor([{ role: "user", content: prompt }])
}

test("default export reads no nonce from the process when no derived binding exists", async () => {
  const previous = process.env.CAO_OPENCODE_HTTP_PROMPT_NONCE
  delete process.env.CAO_OPENCODE_HTTP_PROMPT_NONCE
  try {
    const source = await fs.readFile(SOURCE)
    const module = await import(
      "data:text/javascript;base64," + source.toString("base64") + "#default-no-nonce"
    )
    let hooks = 0
    const cleanup = await module.default.setup({
      session: { hook: async () => { hooks += 1 } },
    })
    assert.equal(cleanup, undefined)
    assert.equal(hooks, 0)
  } finally {
    if (previous === undefined) delete process.env.CAO_OPENCODE_HTTP_PROMPT_NONCE
    else process.env.CAO_OPENCODE_HTTP_PROMPT_NONCE = previous
  }
})

test("factory also stays disabled for an invalid nonce", async () => {
  const source = await fs.readFile(SOURCE)
  const module = await import("data:text/javascript;base64," + source.toString("base64"))
  const plugin = module.createHttpPromptObserver({ nonce: "" })
  let hooks = 0
  const cleanup = await plugin.setup({ session: { hook: async () => { hooks += 1 } } })
  assert.equal(cleanup, undefined)
  assert.equal(hooks, 0)
})

test("records exact latest-user source phases with and without one outer memory block", async (t) => {
  const observer = await setupObserver(t)
  assert.equal(observer.captured.hookName, "http.request")
  assert.deepEqual(observer.captured.options, { providerID: "opencode-go" })
  assert.equal((await fs.stat(observer.ledgerDirectory)).mode & 0o777, 0o700)

  const history = requestFor([
    { role: "system", content: "PRIVATE_SYSTEM_HISTORY" },
    { role: "user", content: receipt(EXPECTED.phase_prompts[0]) },
    { role: "assistant", content: "PRIVATE_OLD_ASSISTANT_TEXT" },
    { role: "user", content: receipt(EXPECTED.phase_prompts[1]) },
  ])
  await dispatch(observer, history)
  const memoryPrompt = `<cao-memory>\nPRIVATE_MEMORY_TEXT\n</cao-memory>\n\n${EXPECTED.phase_prompts[0]}`
  await dispatch(observer, onlyUser(receipt(memoryPrompt)))
  await observer.cleanup()

  assert.equal(observer.captured.disposed, 1)
  const records = await readRecords(observer)
  assert.equal(records.length, 2)
  assert.deepEqual(records.map((item) => item.phase_ordinal), [2, 1])
  for (const record of records) {
    assert.deepEqual(Object.keys(record).sort(), [
      "last_instruction_present",
      "nonce",
      "ordinal",
      "phase_ordinal",
      "receipt_envelope_shape_valid",
      "schema_version",
      "source_prompt_exact",
      "status",
    ])
    assert.equal(record.status, "observed")
    assert.equal(record.source_prompt_exact, true)
    assert.equal(record.last_instruction_present, true)
    assert.equal(record.receipt_envelope_shape_valid, true)
  }
  const raw = await fs.readFile(path.join(observer.ledgerDirectory, "request-0001.json"), "utf8")
  for (const secret of [
    "PRIVATE_SESSION_ID",
    "PRIVATE_AGENT",
    "PRIVATE_SYSTEM_HISTORY",
    "PRIVATE_OLD_ASSISTANT_TEXT",
    "PRIVATE_MEMORY_TEXT",
    EXPECTED.phase_prompts[0],
    "SENSITIVE_URL",
    "SENSITIVE_HEADER",
    "CAO-TURN-RECEIPT-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  ]) {
    assert.equal(raw.includes(secret), false)
  }
  const seal = JSON.parse(await fs.readFile(path.join(observer.ledgerDirectory, "seal.json"), "utf8"))
  assert.equal(seal.trace_complete, true)
  assert.equal(seal.request_count, 2)
  assert.equal(seal.durable_record_count, 2)
})

test("punctuation near miss is diagnostic only; valid receipt envelope never attests active identity", async (t) => {
  const observer = await setupObserver(t)
  const changed = EXPECTED.phase_prompts[0].replace("body", "body。")
  await dispatch(observer, onlyUser(receipt(changed)))
  await observer.cleanup()
  const [record] = await readRecords(observer)
  assert.equal(record.status, "observed")
  assert.equal(record.phase_ordinal, null)
  assert.equal(record.source_prompt_exact, false)
  assert.equal(record.last_instruction_present, true)
  assert.equal(record.receipt_envelope_shape_valid, true)
})

test("the LAST directive may occur inside the exact task before trailing receipt instructions", async (t) => {
  const lastInstruction = "the public result body must be exactly this line: LAST_RECEIPT_LINE"
  const expected = {
    ...EXPECTED,
    phase_prompts: [
      `${lastInstruction}\nPrint the CAO receipt afterwards. Do not add other result text.`,
      `${lastInstruction}\nPrint the CAO receipt afterwards. Do not add other result text. phase two`,
    ],
    last_instruction: lastInstruction,
  }
  const observer = await setupObserver(t, { expected })
  await dispatch(observer, onlyUser(receipt(expected.phase_prompts[0])))
  await observer.cleanup()
  const [record] = await readRecords(observer)
  assert.equal(record.status, "observed")
  assert.equal(record.phase_ordinal, 1)
  assert.equal(record.source_prompt_exact, true)
  assert.equal(record.last_instruction_present, true)
  assert.equal(record.receipt_envelope_shape_valid, true)
})

test("only the latest user text is inspected; assistant/history/tool text cannot satisfy LAST", async (t) => {
  const observer = await setupObserver(t)
  await dispatch(observer, requestFor([
    { role: "user", content: receipt(EXPECTED.phase_prompts[0]) },
    { role: "assistant", content: "LAST_SENTINEL PRIVATE_ASSISTANT" },
    { role: "tool", content: "LAST_SENTINEL PRIVATE_TOOL" },
    { role: "user", content: receipt("body with no last instruction") },
  ]))
  await observer.cleanup()
  const [record] = await readRecords(observer)
  assert.equal(record.phase_ordinal, null)
  assert.equal(record.source_prompt_exact, false)
  assert.equal(record.last_instruction_present, false)
  assert.equal(record.receipt_envelope_shape_valid, true)
})

test("one text content part is accepted while prompt-bearing sidecar duplicate keys are rejected", async (t) => {
  const observer = await setupObserver(t)
  await dispatch(observer, requestFor([
    {
      role: "user",
      content: [{ type: "text", text: receipt(EXPECTED.phase_prompts[0]) }],
    },
  ]))
  const raw = JSON.stringify(EXPECTED).replace(
    `"nonce":"${NONCE}"`,
    `"nonce":"${NONCE}","nonce":"${NONCE}"`,
  )
  await fs.writeFile(observer.expectationPath, raw, { mode: 0o600 })
  await dispatch(observer, onlyUser(receipt(EXPECTED.phase_prompts[1])))
  await observer.cleanup()
  const records = await readRecords(observer)
  assert.equal(records[0].status, "observed")
  assert.equal(records[0].phase_ordinal, 1)
  assert.equal(records[1].status, "expectations_unavailable")
  assert.equal(records[1].phase_ordinal, null)
})

test("request-time expectation reads reject symlink replacement and keep the request untouched", async (t) => {
  const observer = await setupObserver(t)
  const saved = path.join(observer.opencode, "saved-expectations.json")
  await fs.rename(observer.expectationPath, saved)
  await fs.symlink(saved, observer.expectationPath)
  const request = onlyUser(receipt(EXPECTED.phase_prompts[0]))
  await dispatch(observer, request)
  await observer.cleanup()
  const [record] = await readRecords(observer)
  assert.equal(record.status, "expectations_unavailable")
  assert.equal(record.source_prompt_exact, false)
  assert.equal(record.phase_ordinal, null)
})

test("ambiguous, malformed, multipart, duplicated-key and unavailable expectations fail closed without touching request", async (t) => {
  const observer = await setupObserver(t)
  await dispatch(observer, requestFor([
    { role: "user", content: receipt(EXPECTED.phase_prompts[0]) },
    { role: "user", content: [{ type: "text", text: receipt(EXPECTED.phase_prompts[1]) }, { type: "text", text: "extra" }] },
  ]))
  await dispatch(observer, requestFor([
    { role: "unknown_role_sentinel", content: receipt(EXPECTED.phase_prompts[0]) },
    { role: "user", content: receipt(EXPECTED.phase_prompts[1]) },
  ]))
  const duplicateKeyRequest = new Request("https://example.invalid/", {
    method: "POST",
    body: `{"messages":[],"messages":[{"role":"user","content":${JSON.stringify(receipt(EXPECTED.phase_prompts[0]))}}]}`,
  })
  await dispatch(observer, duplicateKeyRequest)
  await dispatch(observer, onlyUser(receipt(`${EXPECTED.phase_prompts[0]} <cao-memory>nested</cao-memory>`)))
  await fs.unlink(observer.expectationPath)
  await dispatch(observer, onlyUser(receipt(EXPECTED.phase_prompts[0])))
  await observer.cleanup()
  const records = await readRecords(observer)
  assert.deepEqual(records.map((item) => item.status), [
    "message_shape_unavailable",
    "message_shape_unavailable",
    "body_invalid",
    "receipt_envelope_unavailable",
    "expectations_unavailable",
  ])
  assert.ok(records.every((item) => item.phase_ordinal === null))
  assert.ok(records.every((item) => item.source_prompt_exact === false))
  assert.ok(records.every((item) => item.last_instruction_present === false))
  assert.ok(records.every((item) => item.receipt_envelope_shape_valid === false))
})

test("non-primary and other-model requests are classified without reading their bodies", async (t) => {
  const observer = await setupObserver(t)
  const unconsumable = new Request("https://example.invalid/", {
    method: "POST",
    body: "BODY_SENTINEL",
  })
  let cloneCalls = 0
  unconsumable.clone = () => {
    cloneCalls += 1
    throw new Error("BODY_READ_SENTINEL")
  }
  const event = eventFor(unconsumable, { kind: "compaction" })
  assert.equal(await observer.captured.callback(event), undefined)
  assert.strictEqual(event.request, unconsumable)
  assert.equal(cloneCalls, 0)
  const otherModelEvent = eventFor(unconsumable, {
    model: { providerID: "opencode-go", id: "other-model" },
  })
  assert.equal(await observer.captured.callback(otherModelEvent), undefined)
  assert.strictEqual(otherModelEvent.request, unconsumable)
  assert.equal(cloneCalls, 0)
  await observer.cleanup()
  const records = await readRecords(observer)
  assert.deepEqual(records.map((item) => item.status), ["non_primary", "model_mismatch"])
  assert.ok(records.every((item) => item.phase_ordinal === null))
})

test("oversized body and request overflow produce a non-success seal without changing HTTP requests", async (t) => {
  const observer = await setupObserver(t)
  const oversized = requestFor([{ role: "user", content: "x".repeat(1024 * 1024) }])
  await dispatch(observer, oversized)
  for (let index = 0; index < 16; index += 1) {
    await dispatch(observer, onlyUser(receipt(EXPECTED.phase_prompts[index % 2])))
  }
  await observer.cleanup()
  const seal = JSON.parse(await fs.readFile(path.join(observer.ledgerDirectory, "seal.json"), "utf8"))
  assert.equal(seal.overflow, true)
  assert.equal(seal.trace_complete, false)
  assert.equal(seal.request_count, 16)
  const records = await readRecords(observer)
  assert.equal(records.length, 16)
  assert.equal(records[0].status, "body_oversized")
})

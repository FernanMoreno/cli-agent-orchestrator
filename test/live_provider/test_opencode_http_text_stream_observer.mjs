import assert from "node:assert/strict"
import { createHash } from "node:crypto"
import fs from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import test from "node:test"
import { pathToFileURL } from "node:url"
import { createHttpTextStreamObserver } from "./opencode_http_text_stream_observer.js"

const NONCE = "a".repeat(32)
const TOKEN = "CAO-TURN-RECEIPT-" + "b".repeat(32)
const MODEL = { providerID: "opencode-go", id: "glm-5.3-flash" }
const EXPECT = {
  schema_version: 1,
  nonce: NONCE,
  phase_prompts: ["first END", "second END"],
  receipt_prefix: "\n\nreceipt: ",
  receipt_suffix: "\nEND",
  last_instruction: "END",
}

async function waitFor(check) {
  const end = Date.now() + 2000
  while (Date.now() < end) {
    if (await check()) return
    await new Promise((resolve) => setTimeout(resolve, 5))
  }
  assert.fail("observer condition not reached")
}

async function setup(t, terminalId, fsApi = fs) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "cao-http-stream-"))
  const opencode = path.join(root, ".opencode")
  const plugins = path.join(opencode, "plugins")
  await fs.mkdir(plugins, { recursive: true, mode: 0o700 })
  for (const dir of [root, opencode, plugins]) await fs.chmod(dir, 0o700)
  const modulePath = path.join(plugins, "stream.js")
  await fs.writeFile(
    modulePath,
    await fs.readFile(new URL("./opencode_http_text_stream_observer.js", import.meta.url)),
    { mode: 0o600 },
  )
  await fs.writeFile(path.join(opencode, "http-prompt-expectations.json"), JSON.stringify(EXPECT), {
    mode: 0o600,
  })
  const hooks = new Map()
  let disposed = 0
  const plugin = createHttpTextStreamObserver({
    nonce: NONCE,
    moduleUrl: pathToFileURL(modulePath).href,
    terminalId,
    fsApi,
  })
  const cleanup = await plugin.setup({
    session: {
      hook: async (name, callback) => {
        hooks.set(name, callback)
        return {
          dispose: async () => {
            disposed += 1
          },
        }
      },
    },
  })
  assert.equal(typeof cleanup, "function")
  t.after(async () => {
    await cleanup()
    await fs.rm(root, { recursive: true, force: true })
  })
  const directory = path.join(
    opencode,
    "http-text-stream-observation",
    ...(terminalId ? [terminalId] : []),
  )
  const records = async () => {
    const names = (await fs.readdir(directory))
      .filter((name) => /^sample-\d{4}\.json$/.test(name))
      .sort()
    return Promise.all(
      names.map(async (name) => JSON.parse(await fs.readFile(path.join(directory, name)))),
    )
  }
  return { root, hooks, cleanup, directory, records, disposed: () => disposed }
}

function request(phase = 2) {
  return new Request("https://example.invalid/private", {
    method: "POST",
    body: JSON.stringify({
      messages: [
        {
          role: "user",
          content:
            EXPECT.phase_prompts[phase - 1] + EXPECT.receipt_prefix + TOKEN + EXPECT.receipt_suffix,
        },
      ],
    }),
  })
}

function frame(text, finish = null) {
  return Buffer.from(
    `data: ${JSON.stringify({ id: "synthetic", model: "wire-model", choices: [{ index: 0, delta: text === null ? {} : { content: text }, finish_reason: finish }] })}\n\n`,
  )
}

test("installed hooks publish bound live counts while original response progresses", async (t) => {
  const observer = await setup(t)
  const req = request()
  const cloneRequest = req.clone.bind(req)
  let requestCloneCancelled = 0
  req.clone = () => {
    const copy = cloneRequest()
    const bodyGetter = Object.getOwnPropertyDescriptor(Request.prototype, "body").get
    const patched = new WeakSet()
    Object.defineProperty(copy, "body", {
      get() {
        const body = bodyGetter.call(copy)
        if (body && !patched.has(body)) {
          patched.add(body)
          const cancel = body.cancel.bind(body)
          body.cancel = (...args) => {
            requestCloneCancelled += 1
            return cancel(...args)
          }
        }
        return body
      },
    })
    return copy
  }
  observer.hooks.get("http.request")({ kind: "primary", model: MODEL, request: req })
  assert.ok((await req.text()).includes(EXPECT.phase_prompts[1]))
  let controller
  const original = new Response(
    new ReadableStream({
      start(value) {
        controller = value
      },
    }),
    { headers: { "content-type": "text/event-stream" } },
  )
  const event = { kind: "primary", model: MODEL, request: req, response: original }
  assert.equal(observer.hooks.get("http.response")(event), undefined)
  const consumption = original.text()
  const first = frame("partial one")
  controller.enqueue(first)
  await waitFor(async () => (await observer.records()).some((x) => x.state === "active"))
  assert.equal(requestCloneCancelled, 1)
  const second = frame(" partial two")
  controller.enqueue(second)
  await waitFor(async () => (await observer.records()).some((x) => x.text_events === 2))
  const final = frame(null, "stop")
  controller.enqueue(final)
  controller.close()
  assert.equal(await consumption, Buffer.concat([first, second, final]).toString())
  assert.strictEqual(event.response, original)
  await waitFor(async () => (await observer.records()).some((x) => x.state === "finished"))
  const records = await observer.records()
  assert.ok(records.length >= 3)
  assert.ok(
    records.every((x) => x.receipt_sha256 === createHash("sha256").update(TOKEN).digest("hex")),
  )
  assert.ok(
    records.every((x) => x.request_bound && x.phase_ordinal === 2 && x.original_not_replaced),
  )
  assert.equal(JSON.stringify(records).includes("partial one"), false)
  assert.equal(JSON.stringify(records).includes(TOKEN), false)
  const names = (await fs.readdir(observer.directory)).filter((x) => x.startsWith("sample-"))
  for (const name of names)
    assert.equal((await fs.stat(path.join(observer.directory, name))).mode & 0o777, 0o600)
  await observer.cleanup()
  assert.equal(observer.disposed(), 2)
})

test("a response with a different Request identity cannot become bound activity", async (t) => {
  const observer = await setup(t)
  observer.hooks.get("http.request")({ kind: "primary", model: MODEL, request: request() })
  const response = new Response(frame("must not bind"), {
    headers: { "content-type": "text/event-stream" },
  })
  observer.hooks.get("http.response")({
    kind: "primary",
    model: MODEL,
    request: request(),
    response,
  })
  await observer.cleanup()
  assert.equal(
    (await observer.records()).some((x) => x.state === "active"),
    false,
  )
  assert.ok(JSON.parse(await fs.readFile(path.join(observer.directory, "invalid.json"))))
})

test("terminal namespaces segregate two workers before either task is sent", async (t) => {
  const a = await setup(t, "worker-a")
  const b = await setup(t, "worker-b")
  assert.equal(path.basename(a.directory), "worker-a")
  assert.equal(path.basename(b.directory), "worker-b")
  assert.equal((await fs.stat(a.directory)).mode & 0o777, 0o700)
})

test("title, wrong model, other phase and unmatched task cannot publish activity", async (t) => {
  for (const variant of ["title", "model", "phase", "task"]) {
    await t.test(variant, async (t) => {
      const observer = await setup(t)
      const req =
        variant === "phase"
          ? request(1)
          : variant === "task"
            ? new Request("https://example.invalid", {
                method: "POST",
                body: JSON.stringify({
                  messages: [
                    {
                      role: "user",
                      content:
                        "different END" + EXPECT.receipt_prefix + TOKEN + EXPECT.receipt_suffix,
                    },
                  ],
                }),
              })
            : request()
      const event = {
        kind: variant === "title" ? "title" : "primary",
        model: variant === "model" ? { ...MODEL, id: "other" } : MODEL,
        request: req,
      }
      observer.hooks.get("http.request")(event)
      const bytes = Buffer.concat([frame("unbound text"), frame(null, "stop")])
      const response = new Response(bytes, { headers: { "content-type": "text/event-stream" } })
      observer.hooks.get("http.response")({ ...event, response })
      assert.equal(await response.text(), bytes.toString())
      await observer.cleanup()
      assert.equal((await observer.records()).length, 0)
    })
  }
})

test("multiple eligible primary requests invalidate the complete observation", async (t) => {
  const observer = await setup(t)
  for (let i = 0; i < 2; i += 1)
    observer.hooks.get("http.request")({ kind: "primary", model: MODEL, request: request() })
  await waitFor(async () =>
    fs.access(path.join(observer.directory, "invalid.json")).then(
      () => true,
      () => false,
    ),
  )
  await observer.cleanup()
  assert.equal(
    (await observer.records()).some((x) => x.state === "active"),
    false,
  )
  const seal = JSON.parse(await fs.readFile(path.join(observer.directory, "seal.json")))
  assert.equal(seal.trace_complete, false)
})

test("cleanup aborts the clone reader while the original remains readable", async (t) => {
  const observer = await setup(t)
  const req = request()
  observer.hooks.get("http.request")({ kind: "primary", model: MODEL, request: req })
  let source
  const original = new Response(
    new ReadableStream({
      start(controller) {
        source = controller
      },
    }),
    { headers: { "content-type": "text/event-stream" } },
  )
  observer.hooks.get("http.response")({
    kind: "primary",
    model: MODEL,
    request: req,
    response: original,
  })
  await observer.cleanup()
  assert.equal(original.bodyUsed, false)
  const bytes = frame(null, "stop")
  source.enqueue(bytes)
  source.close()
  assert.equal(await original.text(), bytes.toString())
  assert.equal(
    (await observer.records()).some((x) => x.state === "active"),
    false,
  )
  await observer.cleanup()
  const seal = JSON.parse(await fs.readFile(path.join(observer.directory, "seal.json")))
  assert.equal(seal.sample_count, (await observer.records()).length)
})

test("heartbeat bursts coalesce instead of exhausting the evidence budget before TEXT", async (t) => {
  const observer = await setup(t)
  const req = request()
  observer.hooks.get("http.request")({ kind: "primary", model: MODEL, request: req })
  let source
  const original = new Response(
    new ReadableStream({
      start(value) {
        source = value
      },
    }),
    {
      headers: { "content-type": "text/event-stream" },
    },
  )
  t.after(() => {
    try {
      source.close()
    } catch {}
  })
  observer.hooks.get("http.response")({
    kind: "primary",
    model: MODEL,
    request: req,
    response: original,
  })
  const consumed = original.text()
  for (let i = 0; i < 140; i += 1) {
    source.enqueue(Buffer.from(": heartbeat\n\n"))
    await new Promise((resolve) => setTimeout(resolve, 5))
  }
  assert.equal(
    await fs.access(path.join(observer.directory, "invalid.json")).then(
      () => true,
      () => false,
    ),
    false,
  )
  source.enqueue(frame("real TEXT after heartbeats"))
  await waitFor(async () => (await observer.records()).some((x) => x.state === "active"))
  source.enqueue(frame(null, "stop"))
  source.close()
  await consumed
  await observer.cleanup()
  const records = await observer.records()
  assert.ok(records.length < 30)
})

test("terminal evidence bypasses a pending nonterminal sampling timer", async (t) => {
  const observer = await setup(t)
  const req = request()
  observer.hooks.get("http.request")({ kind: "primary", model: MODEL, request: req })
  let source
  const original = new Response(
    new ReadableStream({
      start(controller) {
        source = controller
      },
    }),
    {
      headers: { "content-type": "text/event-stream" },
    },
  )
  observer.hooks.get("http.response")({
    kind: "primary",
    model: MODEL,
    request: req,
    response: original,
  })
  const consumed = original.text()
  source.enqueue(frame("first"))
  await waitFor(async () => (await observer.records()).some((x) => x.state === "active"))
  source.enqueue(frame("second"))
  await new Promise((resolve) => setTimeout(resolve, 10))
  source.enqueue(frame(null, "stop"))
  source.close()
  await consumed
  let timeout
  try {
    await Promise.race([
      waitFor(async () => (await observer.records()).some((x) => x.state === "finished")),
      new Promise((_, reject) => {
        timeout = setTimeout(() => reject(new Error("terminal evidence delayed")), 250)
      }),
    ])
  } finally {
    clearTimeout(timeout)
  }
})

test("blocked evidence publication does not hold the original and cannot publish stale activity", async (t) => {
  let release,
    entered = false
  const gate = new Promise((resolve) => {
    release = resolve
  })
  t.after(() => release())
  const fsApi = {
    ...fs,
    async link(...args) {
      entered = true
      await gate
      return fs.link(...args)
    },
  }
  const observer = await setup(t, undefined, fsApi)
  const req = request()
  observer.hooks.get("http.request")({ kind: "primary", model: MODEL, request: req })
  let source
  const original = new Response(
    new ReadableStream({
      start(controller) {
        source = controller
      },
    }),
    { headers: { "content-type": "text/event-stream" } },
  )
  observer.hooks.get("http.response")({
    kind: "primary",
    model: MODEL,
    request: req,
    response: original,
  })
  const consumption = original.text()
  const first = frame("live text")
  source.enqueue(first)
  await waitFor(() => entered)
  const final = frame(null, "stop")
  source.enqueue(final)
  source.close()
  assert.equal(await consumption, Buffer.concat([first, final]).toString())
  release()
  await waitFor(async () => (await observer.records()).some((x) => x.state === "finished"))
  assert.equal(
    (await observer.records()).some((x) => x.state === "active"),
    false,
  )
  await observer.cleanup()
  const seal = JSON.parse(await fs.readFile(path.join(observer.directory, "seal.json")))
  assert.equal(seal.sample_count, (await observer.records()).length)
})

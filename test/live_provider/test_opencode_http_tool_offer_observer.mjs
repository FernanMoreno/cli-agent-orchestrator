import assert from "node:assert/strict"
import fs from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import test from "node:test"
import { pathToFileURL } from "node:url"

const SOURCE = new URL("./opencode_http_tool_offer_observer.js", import.meta.url)
const TARGET_MODEL = { providerID: "opencode-go", id: "longcat-2.5-preview-free" }
const OTHER_MODEL = { providerID: "opencode-go", id: "some-other-model" }
const TOOL_NAMES = [
  "cao-mcp-server_cao_ask_user",
  "cao-mcp-server_cao_wait_for_user_answers",
  "cao-mcp-server_cao_acknowledge_user_answers",
]
const NONCE = "0123456789abcdef0123456789abcdef"

async function setupObserver(
  t,
  { nonce = NONCE, fsApi, disposeFailure = false, registrationFailure = false } = {},
) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "cao-observer-"))
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
  const source = await fs.readFile(pluginPath)
  const module = await import("data:text/javascript;base64," + source.toString("base64"))
  const plugin = module.createToolOfferObserver({
    nonce,
    moduleUrl: pathToFileURL(pluginPath).href,
    fsApi,
  })
  const captured = { callback: null, hookName: null, options: null, disposed: 0 }
  const cleanup = await plugin.setup({
    session: {
      hook: async (hookName, callback, options) => {
        if (registrationFailure) throw new Error("REGISTRATION_ERROR_SENTINEL")
        captured.hookName = hookName
        captured.callback = callback
        captured.options = options
        return {
          dispose: async () => {
            captured.disposed += 1
            if (disposeFailure) throw new Error("DISPOSE_ERROR_SENTINEL")
          },
        }
      },
    },
  })
  t.after(async () => {
    await fs.rm(root, { recursive: true, force: true })
  })
  const ledgerDirectory = path.join(opencode, "http-tool-offer-observation")
  return {
    root,
    working,
    opencode,
    pluginPath,
    ledgerDirectory,
    plugin,
    captured,
    cleanup,
    sealPath: path.join(ledgerDirectory, "seal.json"),
  }
}

function functionTool(name, sentinel = "SCHEMA_SENTINEL") {
  return {
    type: "function",
    function: {
      name,
      description: sentinel,
      parameters: { type: "object", properties: { secret: { const: sentinel } } },
    },
  }
}

function requestFor(body, { headers = {}, url = "https://example.invalid/SENTINEL_URL" } = {}) {
  return new Request(url, {
    method: "POST",
    headers: { Authorization: "Bearer HEADER_SECRET_SENTINEL", ...headers },
    body,
  })
}

async function send(observer, { request, model = TARGET_MODEL, kind = "primary" }) {
  const event = { sessionID: "private-id-sentinel", agent: "private-agent-sentinel", model, kind, request }
  const sameRequest = event.request
  const returned = await observer.captured.callback(event)
  assert.equal(returned, undefined)
  assert.strictEqual(event.request, sameRequest)
  assert.equal(event.sessionID, "private-id-sentinel")
  assert.equal(event.agent, "private-agent-sentinel")
  assert.strictEqual(event.model, model)
  assert.equal(event.kind, kind)
  return event
}

async function records(observer) {
  const names = (await fs.readdir(observer.ledgerDirectory))
    .filter((name) => /^request-[0-9]{4}\.json$/.test(name))
    .sort()
  return Promise.all(
    names.map(async (name) => ({
      file: name,
      raw: await fs.readFile(path.join(observer.ledgerDirectory, name), "utf8"),
    })),
  )
}

test("the real Request clone is durable before return and only fixed allowlisted counts persist", async (t) => {
  const order = []
  let ledgerDirectory
  const io = {
    mkdir: fs.mkdir.bind(fs),
    open: async (...args) => {
      const handle = await fs.open(...args)
      return {
        chmod: handle.chmod.bind(handle),
        writeFile: async (...writeArgs) => {
          order.push("write")
          return handle.writeFile(...writeArgs)
        },
        sync: async () => {
          order.push(args[0] === ledgerDirectory ? "directory_sync" : "sync")
          return handle.sync()
        },
        close: async () => {
          order.push(args[0] === ledgerDirectory ? "directory_close" : "close")
          return handle.close()
        },
      }
    },
    link: async (...args) => {
      order.push("publish")
      return fs.link(...args)
    },
    unlink: async (...args) => {
      order.push("unlink")
      return fs.unlink(...args)
    },
  }
  const observer = await setupObserver(t, { fsApi: io })
  ledgerDirectory = observer.ledgerDirectory
  assert.equal(observer.captured.hookName, "http.request")
  assert.deepEqual(observer.captured.options, { providerID: "opencode-go" })
  const unknownName = "UNRECOGNIZED_TOOL_NAME_SENTINEL"
  const body = JSON.stringify({
    model: "body-model-is-not-trusted",
    messages: [
      { role: "user", content: "PROMPT_SENTINEL" },
      {
        role: "assistant",
        tool_calls: [
          {
            id: "CALL_ID_SENTINEL",
            function: { name: "private-tool-name-sentinel", arguments: "ARGUMENT_SENTINEL" },
          },
        ],
      },
    ],
    tools: [...TOOL_NAMES.map((name) => functionTool(name)), functionTool(unknownName)],
  })
  const request = requestFor(body)
  await send(observer, { request })
  assert.deepEqual(order, [
    "write",
    "sync",
    "close",
    "publish",
    "unlink",
    "directory_sync",
    "directory_close",
  ])
  const published = await records(observer)
  assert.equal(published.length, 1)
  assert.equal(
    (await fs.stat(path.join(observer.ledgerDirectory, published[0].file))).mode & 0o777,
    0o600,
  )
  assert.equal(await request.text(), body)
  const raw = published[0].raw
  for (const secret of [
    "PROMPT_SENTINEL",
    "HEADER_SECRET_SENTINEL",
    "SENTINEL_URL",
    "SCHEMA_SENTINEL",
    unknownName,
    "CALL_ID_SENTINEL",
    "ARGUMENT_SENTINEL",
    "private-tool-name-sentinel",
  ]) {
    assert.equal(raw.includes(secret), false)
  }
  const record = JSON.parse(raw)
  assert.equal(record.nonce, NONCE)
  assert.equal(record.ordinal, 1)
  assert.equal(record.status, "observed")
  assert.deepEqual(record.name_counts, {
    [TOOL_NAMES[0]]: 1,
    [TOOL_NAMES[1]]: 1,
    [TOOL_NAMES[2]]: 1,
  })
  assert.equal(record.other_name_count, 1)
  assert.equal(record.tool_count, 4)
  await observer.cleanup()
  assert.equal(observer.captured.disposed, 1)
  const seal = JSON.parse(await fs.readFile(observer.sealPath, "utf8"))
  assert.equal(seal.hook_disposed, true)
  assert.equal(seal.trace_complete, true)
  assert.equal(seal.record_count, 1)
  assert.equal(seal.durable_record_count, 1)
})

test("valid empty tools is observed; missing, malformed and unsupported fields stay explicit", async (t) => {
  const observer = await setupObserver(t)
  await send(observer, { request: requestFor(JSON.stringify({ tools: [] })) })
  await send(observer, { request: requestFor(JSON.stringify({ model: "x" })) })
  await send(observer, { request: requestFor("{ malformed") })
  await send(observer, { request: requestFor(JSON.stringify({ tools: {} })) })
  await observer.cleanup()
  const values = await records(observer)
  const parsed = values.map(({ raw }) => JSON.parse(raw))
  assert.deepEqual(parsed.map((item) => item.status), [
    "valid_empty",
    "tools_absent",
    "body_invalid",
    "tools_invalid",
  ])
  assert.equal(parsed[0].tool_count, 0)
  const seal = JSON.parse(await fs.readFile(observer.sealPath, "utf8"))
  assert.equal(seal.trace_complete, true)
})

test("oversized, non-primary, wrong-model and unknown-kind requests never become false empty offers", async (t) => {
  const observer = await setupObserver(t)
  const oversized = JSON.stringify({ tools: [], pad: "x".repeat(1024 * 1024) })
  await send(observer, { request: requestFor(oversized) })
  let pulls = 0
  const request = new Request("https://example.invalid/", {
    method: "POST",
    body: new ReadableStream({
      pull(controller) {
        pulls += 1
        controller.enqueue(new TextEncoder().encode('{"tools":[]}'))
        controller.close()
      },
    }),
    duplex: "half",
  })
  await new Promise((resolve) => setImmediate(resolve))
  pulls = 0
  await send(observer, { request, kind: "title" })
  await send(observer, { request: requestFor('{"tools":[]}'), model: OTHER_MODEL })
  await send(observer, { request: requestFor('{"tools":[]}'), kind: "future-kind" })
  assert.equal(pulls, 0)
  await observer.cleanup()
  const parsed = (await records(observer)).map(({ raw }) => JSON.parse(raw))
  assert.deepEqual(parsed.map((item) => item.status), [
    "body_oversized",
    "non_primary",
    "model_mismatch",
    "request_kind_unknown",
  ])
  assert.equal(parsed[0].tool_count, null)
})

test("the pinned OpenCode Model.Ref shape matches id and providerID", async (t) => {
  const observer = await setupObserver(t)
  const model = {
    providerID: "opencode-go",
    id: "longcat-2.5-preview-free",
    variant: "balanced",
  }
  await send(observer, {
    request: requestFor(JSON.stringify({ tools: [functionTool(TOOL_NAMES[0])] })),
    model,
  })
  await observer.cleanup()
  const parsed = (await records(observer)).map(({ raw }) => JSON.parse(raw))
  assert.equal(parsed.length, 1)
  assert.equal(parsed[0].status, "observed")
  assert.equal(parsed[0].model_match, true)
  assert.equal(parsed[0].tool_count, 1)
  assert.deepEqual(parsed[0].name_counts, {
    [TOOL_NAMES[0]]: 1,
    [TOOL_NAMES[1]]: 0,
    [TOOL_NAMES[2]]: 0,
  })
  assert.equal(JSON.stringify(parsed).includes(model.id), false)
  assert.equal(JSON.stringify(parsed).includes(model.providerID), false)
  assert.equal(JSON.stringify(parsed).includes(model.variant), false)
})

test("wrong provider, wrong id, and modelID-only lookalikes fail closed", async (t) => {
  const observer = await setupObserver(t)
  const request = requestFor('{"tools":[]}')
  await send(observer, {
    request,
    model: { providerID: "other-provider", id: "longcat-2.5-preview-free" },
  })
  await send(observer, {
    request,
    model: { providerID: "opencode-go", id: "some-other-model" },
  })
  await send(observer, {
    request,
    model: { providerID: "opencode-go", modelID: "longcat-2.5-preview-free" },
  })
  await observer.cleanup()
  const parsed = (await records(observer)).map(({ raw }) => JSON.parse(raw))
  assert.deepEqual(parsed.map((record) => record.status), [
    "model_mismatch",
    "model_mismatch",
    "model_mismatch",
  ])
  assert.deepEqual(parsed.map((record) => record.tool_count), [null, null, null])
  const serialized = JSON.stringify(parsed)
  for (const privateModelValue of [
    "other-provider",
    "some-other-model",
    "longcat-2.5-preview-free",
  ]) {
    assert.equal(serialized.includes(privateModelValue), false)
  }
})

test("an unreadable Request is recorded safely without escaping or altering the event", async (t) => {
  const observer = await setupObserver(t)
  const request = {
    clone() {
      throw new Error("REQUEST_ERROR_SENTINEL")
    },
  }
  await send(observer, { request })
  await observer.cleanup()
  const published = await records(observer)
  assert.equal(JSON.parse(published[0].raw).status, "body_unreadable")
  assert.equal(published[0].raw.includes("REQUEST_ERROR_SENTINEL"), false)
})

test("a durable request fact survives omitted cleanup but the global seal is absent", async (t) => {
  const observer = await setupObserver(t)
  const request = requestFor(JSON.stringify({ tools: [functionTool(TOOL_NAMES[0])] }))
  await send(observer, { request })

  const published = await records(observer)
  assert.equal(published.length, 1)
  assert.equal(JSON.parse(published[0].raw).status, "observed")
  await assert.rejects(fs.lstat(observer.sealPath), { code: "ENOENT" })
  assert.equal(await request.text(), JSON.stringify({ tools: [functionTool(TOOL_NAMES[0])] }))
})

test("exclusive-create failure preserves a preexisting pending path", async (t) => {
  const observer = await setupObserver(t)
  const pendingPath = path.join(observer.ledgerDirectory, ".request-0001.json.pending")
  const sentinel = "PREEXISTING_PENDING_SENTINEL"
  await fs.writeFile(pendingPath, sentinel, { flag: "wx", mode: 0o600 })
  await fs.chmod(pendingPath, 0o600)
  const before = await fs.stat(pendingPath)

  await send(observer, { request: requestFor(JSON.stringify({ tools: [] })) })
  await observer.cleanup()

  const after = await fs.stat(pendingPath)
  assert.equal(after.ino, before.ino)
  assert.equal(await fs.readFile(pendingPath, "utf8"), sentinel)
  assert.deepEqual(await records(observer), [])
  const seal = JSON.parse(await fs.readFile(observer.sealPath, "utf8"))
  assert.equal(seal.trace_complete, false)
  assert.equal(seal.record_count, 1)
  assert.equal(seal.durable_record_count, 0)
})

test("exclusive-create failure preserves a preexisting global seal", async (t) => {
  const observer = await setupObserver(t)
  const request = requestFor(JSON.stringify({ tools: [] }))
  await send(observer, { request })

  const sentinel = "PREEXISTING_SEAL_SENTINEL"
  await fs.writeFile(observer.sealPath, sentinel, { flag: "wx", mode: 0o600 })
  await fs.chmod(observer.sealPath, 0o600)
  const before = await fs.stat(observer.sealPath)

  await observer.cleanup()

  const after = await fs.stat(observer.sealPath)
  assert.equal(after.ino, before.ino)
  assert.equal(await fs.readFile(observer.sealPath, "utf8"), sentinel)
  assert.equal(JSON.stringify(await records(observer)).includes(sentinel), false)
  await assert.rejects(fs.lstat(path.join(observer.ledgerDirectory, ".seal.json.pending")), {
    code: "ENOENT",
  })
})

test("invalid nonce opts out and produces no ledger or observer proof", async (t) => {
  const observer = await setupObserver(t, { nonce: "bad-nonce" })
  assert.equal(observer.cleanup, undefined)
  assert.equal(observer.captured.callback, null)
  await assert.rejects(fs.lstat(observer.ledgerDirectory), { code: "ENOENT" })
})

for (const failure of ["sync", "close"]) {
  test("a per-request " + failure + " failure publishes neither that record nor a success seal", async (t) => {
    const io = {
      mkdir: fs.mkdir.bind(fs),
      open: async (...args) => {
        const handle = await fs.open(...args)
        return {
          chmod: handle.chmod.bind(handle),
          writeFile: handle.writeFile.bind(handle),
          sync:
            failure === "sync"
              ? async () => {
                  throw new Error("FS_ERROR_SENTINEL")
                }
              : handle.sync.bind(handle),
          close: async () => {
            await handle.close()
            if (failure === "close") throw new Error("FS_ERROR_SENTINEL")
          },
        }
      },
      link: fs.link.bind(fs),
      unlink: fs.unlink.bind(fs),
    }
    const observer = await setupObserver(t, { fsApi: io })
    const body = JSON.stringify({ tools: [] })
    const request = requestFor(body)
    await send(observer, { request })
    assert.equal(await request.text(), body)
    await observer.cleanup()
    assert.deepEqual(await records(observer), [])
    await assert.rejects(fs.lstat(observer.sealPath), { code: "ENOENT" })
  })
}

test("a directory sync failure removes its apparent record and keeps the offer incomplete", async (t) => {
  let ledgerDirectory
  let failed = false
  const io = {
    mkdir: fs.mkdir.bind(fs),
    open: async (...args) => {
      const handle = await fs.open(...args)
      return {
        chmod: handle.chmod.bind(handle),
        writeFile: handle.writeFile.bind(handle),
        sync: async () => {
          if (args[0] === ledgerDirectory && !failed) {
            failed = true
            throw new Error("DIRECTORY_SYNC_SENTINEL")
          }
          return handle.sync()
        },
        close: handle.close.bind(handle),
      }
    },
    link: fs.link.bind(fs),
    unlink: fs.unlink.bind(fs),
  }
  const observer = await setupObserver(t, { fsApi: io })
  ledgerDirectory = observer.ledgerDirectory
  await send(observer, { request: requestFor(JSON.stringify({ tools: [] })) })
  const visible = await records(observer)
  assert.equal(failed, true)
  assert.deepEqual(visible, [])

  await observer.cleanup()
  const seal = JSON.parse(await fs.readFile(observer.sealPath, "utf8"))
  assert.equal(seal.trace_complete, false)
  assert.equal(seal.record_count, 1)
  assert.equal(seal.durable_record_count, 0)
  assert.equal(JSON.stringify(seal).includes("DIRECTORY_SYNC_SENTINEL"), false)
})

test("a global seal directory sync failure removes the apparent success seal", async (t) => {
  let ledgerDirectory
  let directorySyncCount = 0
  const io = {
    mkdir: fs.mkdir.bind(fs),
    open: async (...args) => {
      const handle = await fs.open(...args)
      return {
        chmod: handle.chmod.bind(handle),
        writeFile: handle.writeFile.bind(handle),
        sync: async () => {
          if (args[0] === ledgerDirectory) {
            directorySyncCount += 1
            if (directorySyncCount === 2) throw new Error("SEAL_DIRECTORY_SYNC_SENTINEL")
          }
          return handle.sync()
        },
        close: handle.close.bind(handle),
      }
    },
    link: fs.link.bind(fs),
    unlink: fs.unlink.bind(fs),
  }
  const observer = await setupObserver(t, { fsApi: io })
  ledgerDirectory = observer.ledgerDirectory
  await send(observer, { request: requestFor(JSON.stringify({ tools: [] })) })
  await observer.cleanup()

  assert.equal(directorySyncCount, 3)
  await assert.rejects(fs.lstat(observer.sealPath), { code: "ENOENT" })
  assert.equal((await records(observer)).length, 1)
})

test("disposal failure preserves request fact but cannot seal aggregate completion", async (t) => {
  const observer = await setupObserver(t, { disposeFailure: true })
  await send(observer, { request: requestFor(JSON.stringify({ tools: [] })) })
  await observer.cleanup()
  const published = await records(observer)
  assert.equal(JSON.parse(published[0].raw).status, "valid_empty")
  const seal = JSON.parse(await fs.readFile(observer.sealPath, "utf8"))
  assert.equal(seal.hook_disposed, false)
  assert.equal(seal.trace_complete, false)
  assert.equal(JSON.stringify(seal).includes("DISPOSE_ERROR_SENTINEL"), false)
})

test("registration failure writes only a non-success global seal", async (t) => {
  const observer = await setupObserver(t, { registrationFailure: true })
  assert.equal(observer.captured.callback, null)
  await observer.cleanup()
  assert.deepEqual(await records(observer), [])
  const seal = JSON.parse(await fs.readFile(observer.sealPath, "utf8"))
  assert.equal(seal.hook_disposed, false)
  assert.equal(seal.trace_complete, false)
  assert.equal(JSON.stringify(seal).includes("REGISTRATION_ERROR_SENTINEL"), false)
})

test("the ledger caps records and marks overflow without claiming a complete trace", async (t) => {
  const observer = await setupObserver(t)
  for (let index = 0; index < 17; index += 1) {
    await send(observer, { request: requestFor(JSON.stringify({ tools: [] })) })
  }
  await observer.cleanup()
  assert.equal((await records(observer)).length, 16)
  const seal = JSON.parse(await fs.readFile(observer.sealPath, "utf8"))
  assert.equal(seal.record_count, 16)
  assert.equal(seal.durable_record_count, 16)
  assert.equal(seal.overflow, true)
  assert.equal(seal.trace_complete, false)
})

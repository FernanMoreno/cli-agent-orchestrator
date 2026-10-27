import assert from "node:assert/strict"
import test from "node:test"
import { observeTextResponse } from "./opencode_http_text_response_monitor.js"

const encoder = new TextEncoder()
const headers = { "content-type": "text/event-stream; charset=utf-8" }

function sse(delta, finish = null) {
  return encoder.encode(
    `data: ${JSON.stringify({
      id: "synthetic-id",
      model: "synthetic-model",
      choices: [{ index: 0, delta, finish_reason: finish }],
    })}\n\n`,
  )
}

function doneFrame() {
  return encoder.encode("data: [DONE]\n\n")
}

function concatenate(...parts) {
  return Buffer.concat(parts.map((part) => Buffer.from(part)))
}

function responseFromBytes(bytes, { status = 200, contentType = headers["content-type"] } = {}) {
  return new Response(bytes, { status, headers: { "content-type": contentType } })
}

test("an invalid clone read result still cancels the clone and releases its lock", async () => {
  const calls = { cancel: 0, release: 0 }
  const original = {
    status: 200,
    headers: new Headers(headers),
    clone: () => ({
      body: {
        getReader: () => ({
          read: async () => undefined,
          cancel: () => {
            calls.cancel += 1
            return Promise.resolve()
          },
          releaseLock: () => {
            calls.release += 1
          },
        }),
      },
    }),
  }
  const result = await observeTextResponse(original)
  assert.equal(result.state, "unavailable")
  assert.equal(calls.cancel, 1)
  assert.equal(calls.release, 1)
})

async function collect(reader) {
  const parts = []
  while (true) {
    const part = await reader.read()
    if (part.done) return Buffer.concat(parts)
    parts.push(Buffer.from(part.value))
  }
}

async function waitFor(predicate, timeoutMs = 1000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (predicate()) return
    await new Promise((resolve) => setTimeout(resolve, 2))
  }
  assert.fail("condition was not reached before the test deadline")
}

function controlledResponse(first, next, state) {
  let releaseGate
  const gate = new Promise((resolve) => {
    releaseGate = resolve
  })
  let nextStarted = false
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(first)
    },
    async pull(controller) {
      if (nextStarted) return
      nextStarted = true
      await gate
      controller.enqueue(next)
      controller.close()
    },
    cancel() {
      state.sourceCancelled = true
    },
  })
  return { response: new Response(body, { status: 200, headers }), release: releaseGate }
}

test("clone observation sees text before finish and original drains exact bytes", async () => {
  const first = sse({ role: "assistant", content: "part one" })
  const finish = concatenate(sse({ content: "part two" }), sse({}, "stop"), doneFrame())
  const expectedOriginal = concatenate(first, finish)
  const state = { sourceCancelled: false }
  const { response, release } = controlledResponse(first, finish, state)
  const originalObject = response
  const snapshots = []
  const observation = observeTextResponse(response, {
    onSnapshot(snapshot) {
      snapshots.push(snapshot)
    },
  })

  const originalReader = response.body.getReader()
  const firstOriginalRead = await originalReader.read()
  assert.equal(firstOriginalRead.done, false)
  assert.deepEqual(Buffer.from(firstOriginalRead.value), Buffer.from(first))
  await waitFor(() => snapshots.some((item) => item.state === "active" && item.text_bytes > 0))
  assert.equal(response, originalObject)
  assert.equal(
    snapshots.some((item) => item.state === "finished"),
    false,
  )

  release()
  const remainingOriginal = await collect(originalReader)
  const result = await observation

  assert.deepEqual(
    Buffer.concat([Buffer.from(firstOriginalRead.value), remainingOriginal]),
    expectedOriginal,
  )
  assert.equal(state.sourceCancelled, false)
  assert.equal(result.state, "finished")
  assert.equal(result.text_bytes, Buffer.byteLength("part onepart two", "utf8"))
  assert.equal(result.original_not_replaced, true)
  assert.equal(result.cleanup_observed.reader_lock_released, true)
  assert.deepEqual(Object.keys(snapshots[0]).sort(), [
    "event_count",
    "original_not_replaced",
    "reason",
    "state",
    "text_bytes",
    "text_events",
    "wire_bytes",
  ])
  assert.equal(JSON.stringify(snapshots).includes("part one"), false)
  assert.equal(JSON.stringify(snapshots).includes("synthetic-id"), false)
})

test("TEXT and finish in one chunk emits no active snapshot", async () => {
  const bytes = concatenate(sse({ content: "all at once" }), sse({}, "stop"), doneFrame())
  const response = responseFromBytes(bytes)
  const snapshots = []
  const result = await observeTextResponse(response, {
    onSnapshot(snapshot) {
      snapshots.push(snapshot)
    },
  })

  assert.equal(result.state, "finished")
  assert.equal(result.text_bytes, Buffer.byteLength("all at once", "utf8"))
  assert.equal(
    snapshots.some((item) => item.state === "active"),
    false,
  )
  assert.equal(snapshots.at(-1).state, "finished")
})

test("abort during a pending clone read is bounded and leaves original readable", async () => {
  const first = sse({ content: "visible while held" })
  const finish = concatenate(sse({}, "stop"), doneFrame())
  const state = { sourceCancelled: false }
  const { response, release } = controlledResponse(first, finish, state)
  const controller = new AbortController()
  const snapshots = []
  const observation = observeTextResponse(response, {
    signal: controller.signal,
    onSnapshot(snapshot) {
      snapshots.push(snapshot)
    },
  })
  const originalReader = response.body.getReader()

  await waitFor(() => snapshots.some((item) => item.state === "active" && item.text_bytes > 0))
  controller.abort()
  const result = await Promise.race([
    observation,
    new Promise((_, reject) =>
      setTimeout(() => reject(new Error("abort did not finish promptly")), 250),
    ),
  ])

  assert.equal(result.state, "unavailable")
  assert.equal(result.reason, "aborted")
  assert.equal(result.cleanup_observed.clone_cancel_requested, true)
  assert.equal(result.cleanup_observed.reader_lock_released, true)
  assert.equal(state.sourceCancelled, false)

  const firstOriginalRead = await originalReader.read()
  assert.equal(firstOriginalRead.done, false)
  assert.deepEqual(Buffer.from(firstOriginalRead.value), Buffer.from(first))
  release()
  assert.deepEqual(await collect(originalReader), finish)
  assert.equal(state.sourceCancelled, false)
})

test("malformed SSE and over-budget bodies terminate with counters only", async (t) => {
  await t.test("malformed JSON", async () => {
    const result = await observeTextResponse(
      responseFromBytes(encoder.encode("data: {bad json}\n\n")),
    )
    assert.equal(result.state, "unavailable")
    assert.equal(result.reason, "json_invalid")
  })
  await t.test("body cap", async () => {
    const result = await observeTextResponse(responseFromBytes(new Uint8Array(1024 * 1024 + 1)))
    assert.equal(result.state, "unavailable")
    assert.equal(result.reason, "body_limit")
    assert.equal(result.wire_bytes, 1024 * 1024 + 1)
  })
})

test("no body, non-2xx, and non-SSE content type are unavailable", async (t) => {
  await t.test("clone without body", async () => {
    const response = new Response(null, { status: 200, headers })
    const snapshots = []
    const result = await observeTextResponse(response, {
      onSnapshot(snapshot) {
        snapshots.push(snapshot)
      },
    })
    assert.equal(result.reason, "response_unreadable")
    assert.equal(snapshots.length, 1)
    assert.equal(snapshots[0].reason, "response_unreadable")
  })
  await t.test("HTTP error", async () => {
    const result = await observeTextResponse(
      responseFromBytes(sse({ content: "ignored" }), { status: 503 }),
    )
    assert.equal(result.reason, "http_error")
    assert.equal(result.cleanup_observed.clone_cancel_requested, false)
  })
  await t.test("wrong media type", async () => {
    const result = await observeTextResponse(
      responseFromBytes(sse({ content: "ignored" }), {
        contentType: "application/json",
      }),
    )
    assert.equal(result.reason, "content_type_invalid")
    assert.equal(result.cleanup_observed.clone_cancel_requested, false)
  })
})

test("clone read errors do not expose source error details", async () => {
  const body = new ReadableStream({
    start(controller) {
      controller.error(new Error("private_source_secret"))
    },
  })
  const response = new Response(body, { status: 200, headers })
  const result = await observeTextResponse(response)
  assert.equal(result.state, "unavailable")
  assert.equal(result.reason, "response_unreadable")
  assert.equal(JSON.stringify(result).includes("private_source_secret"), false)
})

test("async snapshot callback is rejected without awaiting it", async () => {
  let resolveCallback
  const callbackPromise = new Promise((resolve) => {
    resolveCallback = resolve
  })
  const response = responseFromBytes(sse({ content: "callback probe" }))
  const started = Date.now()
  const result = await observeTextResponse(response, {
    onSnapshot() {
      return callbackPromise
    },
  })
  resolveCallback()
  assert.equal(result.reason, "observer_callback_invalid")
  assert.ok(Date.now() - started < 250)
})

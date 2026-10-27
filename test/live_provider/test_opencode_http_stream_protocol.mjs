import assert from "node:assert/strict"
import test from "node:test"

import { createTextStreamParser } from "./opencode_http_stream_protocol.js"

const encoder = new TextEncoder()
const RESPONSE_ID = "private-response-id-canary"
const MODEL = "private-model-canary"
const CONTENT = "José 🧭 private-text-canary"
const OUTPUT_KEYS = [
  "event_count",
  "reason",
  "state",
  "text_bytes",
  "text_events",
  "wire_bytes",
].sort()
const STATES = new Set(["waiting", "active", "finished", "unavailable"])

function parser() {
  const value = createTextStreamParser()
  assert.equal(typeof value.feed, "function")
  assert.equal(typeof value.end, "function")
  return value
}

function bytes(value) {
  return typeof value === "string" ? encoder.encode(value) : value
}

function concat(...parts) {
  return Buffer.concat(parts.map((part) => Buffer.from(bytes(part))))
}

function publicSnapshot(value, forbidden = []) {
  assert.deepEqual(Object.keys(value).sort(), OUTPUT_KEYS)
  assert.equal(Object.isFrozen(value), true)
  assert.ok(STATES.has(value.state))
  assert.ok(value.reason === null || typeof value.reason === "string")
  for (const key of ["event_count", "text_bytes", "text_events", "wire_bytes"]) {
    assert.ok(Number.isSafeInteger(value[key]), `${key} must be a safe integer`)
    assert.ok(value[key] >= 0, `${key} must be nonnegative`)
  }
  const serialized = JSON.stringify(value)
  for (const secret of forbidden) {
    if (secret) assert.equal(serialized.includes(secret), false, `leaked ${secret}`)
  }
  return value
}

function feed(instance, chunk, forbidden = []) {
  assert.ok(chunk instanceof Uint8Array)
  return publicSnapshot(instance.feed(chunk), forbidden)
}

function end(instance, forbidden = []) {
  return publicSnapshot(instance.end(), forbidden)
}

function response({
  delta = {},
  index = 0,
  finishReason = null,
  id = RESPONSE_ID,
  model = MODEL,
  choices,
} = {}) {
  return {
    id,
    model,
    choices: choices ?? [{ index, delta, finish_reason: finishReason }],
  }
}

function dataFrame(value, eventName = null) {
  const payload = typeof value === "string" ? value : JSON.stringify(value)
  const eventLine = eventName === null ? "" : `event: ${eventName}\r\n`
  return encoder.encode(`${eventLine}data: ${payload}\r\n\r\n`)
}

function textFrame(content = CONTENT, extra = {}) {
  return dataFrame(response({ delta: { content, ...extra } }))
}

function finishFrame(reason = "stop") {
  return dataFrame(response({ delta: {}, finishReason: reason }))
}

function commentFrame(text = "heartbeat") {
  return encoder.encode(`: ${text}\r\n\r\n`)
}

test("publishes only a frozen count projection for one complete text event", () => {
  const instance = parser()
  const result = feed(instance, textFrame(), [CONTENT, RESPONSE_ID, MODEL])

  assert.equal(result.state, "active")
  assert.equal(result.wire_bytes, textFrame().byteLength)
  assert.equal(result.text_bytes, Buffer.byteLength(CONTENT, "utf8"))
  assert.equal(result.text_events, 1)
  assert.equal(result.event_count, 1)
})

test("waits for a complete frame across split UTF-8 bytes and split CRLF", () => {
  const instance = parser()
  const frame = Buffer.from(textFrame("🧭"))
  const emojiOffset = frame.indexOf(Buffer.from("🧭"))
  const frameEndOffset = frame.indexOf(Buffer.from("\r\n\r\n"))
  assert.ok(emojiOffset > 0)
  assert.ok(frameEndOffset > emojiOffset)

  const beforeCompleteScalar = feed(instance, frame.subarray(0, emojiOffset + 1), ["🧭"])
  assert.equal(beforeCompleteScalar.state, "waiting")

  // Ends on the first CR of the empty-line delimiter; the LF arrives later.
  const beforeBlankLine = feed(instance, frame.subarray(emojiOffset + 1, frameEndOffset + 1), [
    "🧭",
  ])
  assert.equal(beforeBlankLine.state, "waiting")

  const complete = feed(instance, frame.subarray(frameEndOffset + 1), ["🧭"])
  assert.equal(complete.state, "active")
  assert.equal(complete.text_bytes, Buffer.byteLength("🧭", "utf8"))
})

test("joins multiline SSE data fields with a newline before strict JSON parsing", () => {
  const instance = parser()
  const payload = JSON.stringify(response({ delta: { content: "multiline-canary" } }))
  const splitAt = payload.indexOf(',"choices"') + 1
  assert.ok(splitAt > 0)
  const frame = encoder.encode(
    `data: ${payload.slice(0, splitAt)}\r\ndata: ${payload.slice(splitAt)}\r\n\r\n`,
  )

  const result = feed(instance, frame, ["multiline-canary", RESPONSE_ID, MODEL])
  assert.equal(result.state, "active")
  assert.equal(result.text_bytes, Buffer.byteLength("multiline-canary", "utf8"))
  assert.equal(result.text_events, 1)
})

test("does not report text while a frame is split across feed calls", () => {
  const instance = parser()
  const frame = Buffer.from(textFrame("fragmented-data-canary"))
  const splitAt = frame.indexOf(Buffer.from("fragmented-data-canary")) + 7

  const partial = feed(instance, frame.subarray(0, splitAt), ["fragmented-data-canary"])
  assert.equal(partial.state, "waiting")
  const complete = feed(instance, frame.subarray(splitAt), ["fragmented-data-canary"])
  assert.equal(complete.state, "active")
  assert.equal(complete.text_bytes, Buffer.byteLength("fragmented-data-canary", "utf8"))
})

test("role, reasoning, tool-only, and null-content deltas do not count as text", () => {
  const instance = parser()
  const frame = dataFrame(
    response({
      delta: {
        role: "assistant",
        reasoning: "private-reasoning-canary",
        tool_calls: [{ index: 0, function: { arguments: "private-tool-arguments-canary" } }],
      },
    }),
  )
  const metadata = feed(instance, frame, [
    "private-reasoning-canary",
    "private-tool-arguments-canary",
    RESPONSE_ID,
    MODEL,
  ])
  assert.equal(metadata.state, "waiting")
  assert.equal(metadata.text_bytes, 0)
  assert.equal(metadata.text_events, 0)

  const empty = feed(instance, dataFrame(response({ delta: { content: null } })))
  assert.equal(empty.state, "waiting")
  assert.equal(empty.text_bytes, 0)
  const emptyString = feed(instance, dataFrame(response({ delta: { content: "" } })))
  assert.equal(emptyString.state, "waiting")
  assert.equal(emptyString.text_bytes, 0)
})

test("rejects non-string content without exposing its value", () => {
  const instance = parser()
  const result = feed(
    instance,
    dataFrame(response({ delta: { content: { private: "private-object-canary" } } })),
    ["private-object-canary", RESPONSE_ID, MODEL],
  )
  assert.equal(result.state, "unavailable")
})

test("requires exactly one explicitly indexed choice at index zero", () => {
  const invalidPayloads = [
    response({ index: 1, delta: { content: "wrong-index-canary" } }),
    response({
      choices: [
        { index: 0, delta: { content: "first-choice-canary" }, finish_reason: null },
        { index: 0, delta: { content: "second-choice-canary" }, finish_reason: null },
      ],
    }),
    response({ choices: [{ delta: { content: "missing-index-canary" }, finish_reason: null }] }),
  ]

  for (const payload of invalidPayloads) {
    const instance = parser()
    const result = feed(instance, dataFrame(payload))
    assert.equal(result.state, "unavailable")
  }
})

test("requires first-event response identity and rejects id or model changes", () => {
  for (const missingKey of ["id", "model"]) {
    const instance = parser()
    const first = response({ delta: { role: "assistant" } })
    delete first[missingKey]
    assert.equal(feed(instance, dataFrame(first)).state, "unavailable")
  }

  for (const changed of [
    { id: "different-private-response-id", model: MODEL },
    { id: RESPONSE_ID, model: "different-private-model" },
  ]) {
    const instance = parser()
    const identityOnly = feed(instance, dataFrame(response({ delta: { role: "assistant" } })), [
      RESPONSE_ID,
      MODEL,
    ])
    assert.equal(identityOnly.state, "waiting")
    const mismatch = feed(
      instance,
      dataFrame(response({ delta: { content: "must-not-count-canary" }, ...changed })),
      ["must-not-count-canary", RESPONSE_ID, MODEL, ...Object.values(changed)],
    )
    assert.equal(mismatch.state, "unavailable")
  }
})

test("rejects duplicate JSON keys, including escaped spellings of the same key", () => {
  const instance = parser()
  const raw =
    '{"id":"private-response-id-canary","i\\u0064":"duplicate-canary",' +
    '"model":"private-model-canary","choices":[{"index":0,' +
    '"delta":{"content":"must-not-count-canary"},"finish_reason":null}]}'
  const result = feed(instance, dataFrame(raw), [
    "duplicate-canary",
    "must-not-count-canary",
    RESPONSE_ID,
    MODEL,
  ])
  assert.equal(result.state, "unavailable")
})

test("does not publish a text delta when finish, DONE, or error occurs in the same feed", () => {
  const finish = finishFrame("stop")
  const done = "[DONE]"
  const error = { error: { message: "private-error-detail-canary" } }
  const inputs = [
    concat(textFrame("terminal-text-canary"), finish),
    concat(textFrame("terminal-text-canary"), dataFrame(done)),
    concat(textFrame("terminal-text-canary"), dataFrame(error, "error")),
  ]

  for (const input of inputs) {
    const instance = parser()
    const result = feed(instance, input, [
      "terminal-text-canary",
      "private-error-detail-canary",
      RESPONSE_ID,
      MODEL,
    ])
    assert.notEqual(result.state, "active")
  }
})

test("withholds activity if a complete text frame is followed by a partial frame in the same feed", () => {
  const instance = parser()
  const result = feed(
    instance,
    concat(textFrame("trailing-partial-canary"), encoder.encode('data: {"id":')),
    ["trailing-partial-canary", RESPONSE_ID, MODEL],
  )
  assert.notEqual(result.state, "active")
})

test("EOF without finish or DONE is unavailable", () => {
  const instance = parser()
  const active = feed(instance, textFrame("unfinished-canary"), ["unfinished-canary"])
  assert.equal(active.state, "active")
  const ended = end(instance, ["unfinished-canary", RESPONSE_ID, MODEL])
  assert.equal(ended.state, "unavailable")
})

test("rejects invalid UTF-8 instead of replacing it", () => {
  const instance = parser()
  const prefix = encoder.encode('data: {"id":"id","model":"model","choices":[')
  const result = feed(instance, concat(prefix, Uint8Array.of(0xff), encoder.encode("]}\n\n")))
  assert.equal(result.state, "unavailable")
})

test("enforces the event, body, text, and event-count limits", () => {
  const eventLimit = 64 * 1024
  const eventAtLimit = encoder.encode(`:${"x".repeat(eventLimit - 3)}\n\n`)
  const atLimit = feed(parser(), eventAtLimit)
  assert.equal(atLimit.state, "waiting")
  assert.equal(atLimit.event_count, 1)

  const eventTooLarge = feed(parser(), encoder.encode(`:${"x".repeat(eventLimit - 2)}\n\n`))
  assert.equal(eventTooLarge.state, "unavailable")

  const bodyAtLimit = concat(...Array.from({ length: 16 }, () => eventAtLimit))
  assert.equal(bodyAtLimit.byteLength, 1024 * 1024)
  const exactBody = feed(parser(), bodyAtLimit)
  assert.equal(exactBody.state, "waiting")
  assert.equal(exactBody.wire_bytes, 1024 * 1024)

  const bodyTooLarge = feed(parser(), concat(bodyAtLimit, Uint8Array.of(0x3a)))
  assert.equal(bodyTooLarge.state, "unavailable")

  const exactTextParser = parser()
  const half = "a".repeat(32 * 1024)
  assert.equal(feed(exactTextParser, textFrame(half)).state, "active")
  const exactText = feed(exactTextParser, textFrame(half))
  assert.equal(exactText.state, "active")
  assert.equal(exactText.text_bytes, 64 * 1024)

  const overTextParser = parser()
  assert.equal(feed(overTextParser, textFrame(half)).state, "active")
  assert.equal(feed(overTextParser, textFrame(half)).state, "active")
  assert.equal(feed(overTextParser, textFrame("b")).state, "unavailable")

  const eventCountParser = parser()
  const exactEventCount = feed(eventCountParser, encoder.encode(": heartbeat\n\n".repeat(4096)))
  assert.equal(exactEventCount.state, "waiting")
  assert.equal(exactEventCount.event_count, 4096)
  assert.equal(feed(eventCountParser, commentFrame()).state, "unavailable")
})

function event(delta, finish = null) {
  return Buffer.from(
    `data: ${JSON.stringify({ id: "stream-one", model: "synthetic-model", choices: [{ index: 0, delta, finish_reason: finish }] })}\n\n`,
  )
}

test("separate live TEXT feeds expose counts without contents", () => {
  const parser = createTextStreamParser()
  const first = parser.feed(event({ content: "ñ" }))
  const second = parser.feed(event({ content: "中文" }))
  assert.equal(first.state, "active")
  assert.equal(first.text_bytes, 2)
  assert.equal(second.state, "active")
  assert.equal(second.text_bytes, 8)
  assert.equal(second.text_events, 2)
  assert.deepEqual(Object.keys(second).sort(), [
    "event_count",
    "reason",
    "state",
    "text_bytes",
    "text_events",
    "wire_bytes",
  ])
  assert.equal(JSON.stringify(second).includes("中文"), false)
})

test("TEXT plus finish in the same received chunk never publishes activity", () => {
  const parser = createTextStreamParser()
  const result = parser.feed(Buffer.concat([event({ content: "text" }), event({}, "stop")]))
  assert.equal(result.state, "finished")
})

test("interrupted SSE framing cannot become activity", () => {
  const parser = createTextStreamParser()
  const result = parser.feed(Buffer.concat([event({ content: "text" }), Buffer.from("data: {")]))
  assert.equal(result.state, "waiting")
  assert.equal(parser.end().state, "unavailable")
})

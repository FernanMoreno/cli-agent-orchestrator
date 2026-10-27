// Diagnostic-only incremental TEXT evidence. No payload leaves this parser.
import { parseJsonWithoutDuplicateKeys } from "./opencode_http_prompt_observer.js"

const BODY_CAP = 1024 * 1024
const EVENT_CAP = 64 * 1024
const TEXT_CAP = 64 * 1024
const EVENT_COUNT_CAP = 4096
const FINISH = new Set(["stop", "length", "tool_calls", "content_filter", "function_call"])
const record = (value) => value !== null && typeof value === "object" && !Array.isArray(value)

function scalar(value) {
  if (typeof value !== "string") return false
  for (let i = 0; i < value.length; i += 1) {
    const c = value.charCodeAt(i)
    if (c >= 0xd800 && c <= 0xdbff) {
      const next = value.charCodeAt(++i)
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false
    } else if (c >= 0xdc00 && c <= 0xdfff) return false
  }
  return true
}

export function createTextStreamParser() {
  let wireBytes = 0,
    textBytes = 0,
    textEvents = 0,
    eventCount = 0
  let line = [],
    data = [],
    frameBytes = 0,
    pendingCR = false,
    eventType = ""
  let streamId = null,
    streamModel = null,
    finished = false,
    done = false,
    failure = null
  const decoder = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true })

  function fail(reason) {
    failure ??= reason
  }

  function snapshot(active = false) {
    return Object.freeze({
      state: failure ? "unavailable" : finished ? "finished" : active ? "active" : "waiting",
      reason: failure ?? (finished ? "stream_finished" : "none"),
      wire_bytes: wireBytes,
      text_bytes: textBytes,
      text_events: textEvents,
      event_count: eventCount,
    })
  }

  function applyFrame() {
    eventCount += 1
    if (eventCount > EVENT_COUNT_CAP) return fail("event_count_limit")
    if (eventType && eventType !== "message") return fail("event_type_invalid")
    if (!data.length) return
    const body = data.join("\n")
    if (done) return fail("terminal_order_invalid")
    if (body === "[DONE]") {
      done = true
      finished = true
      return
    }
    let event
    try {
      event = parseJsonWithoutDuplicateKeys(body)
    } catch {
      return fail("json_invalid")
    }
    if (!record(event) || Object.hasOwn(event, "error") || !Array.isArray(event.choices)) {
      return fail("event_invalid")
    }
    if (event.choices.length === 0) return
    if (event.choices.length !== 1 || finished) return fail("terminal_or_choice_invalid")
    if (
      ![event.id, event.model].every((v) => scalar(v) && v && Buffer.byteLength(v, "utf8") <= 256)
    ) {
      return fail("stream_identity_invalid")
    }
    if (streamId === null) {
      streamId = event.id
      streamModel = event.model
    } else if (event.id !== streamId || event.model !== streamModel) {
      return fail("stream_identity_mismatch")
    }
    const choice = event.choices[0]
    if (!record(choice) || choice.index !== 0) return fail("choice_invalid")
    const delta = choice.delta
    if (delta !== null && delta !== undefined && !record(delta)) return fail("delta_invalid")
    if (choice.finish_reason !== undefined && choice.finish_reason !== null) {
      if (!FINISH.has(choice.finish_reason)) return fail("finish_invalid")
      finished = true
    }
    if (!record(delta)) return
    if (delta.role !== undefined && delta.role !== null && delta.role !== "assistant") {
      return fail("role_invalid")
    }
    if (
      delta.tool_calls !== undefined &&
      delta.tool_calls !== null &&
      (!Array.isArray(delta.tool_calls) || delta.tool_calls.length > 32)
    )
      return fail("tool_delta_invalid")
    if (delta.content === undefined || delta.content === null) return
    if (!scalar(delta.content)) return fail("text_invalid")
    if (!delta.content) return
    if (Array.isArray(delta.tool_calls) && delta.tool_calls.length)
      return fail("mixed_output_invalid")
    textBytes += Buffer.byteLength(delta.content, "utf8")
    textEvents += 1
    if (textBytes > TEXT_CAP) fail("text_limit")
  }

  function finishLine() {
    let value
    try {
      value = decoder.decode(Uint8Array.from(line))
    } catch {
      return fail("utf8_invalid")
    }
    line = []
    if (value === "") {
      applyFrame()
      data = []
      eventType = ""
      frameBytes = 0
      return
    }
    if (value.startsWith(":")) return
    const colon = value.indexOf(":")
    const field = colon < 0 ? value : value.slice(0, colon)
    let content = colon < 0 ? "" : value.slice(colon + 1)
    if (content.startsWith(" ")) content = content.slice(1)
    if (field === "data") data.push(content)
    if (field === "event") eventType = content
  }

  return {
    feed(bytes) {
      if (failure) return snapshot()
      if (!(bytes instanceof Uint8Array)) {
        fail("input_invalid")
        return snapshot()
      }
      const previousText = textBytes
      wireBytes += bytes.byteLength
      if (wireBytes > BODY_CAP) {
        fail("body_limit")
        return snapshot()
      }
      for (const byte of bytes) {
        if (pendingCR) {
          pendingCR = false
          if (byte === 10) {
            frameBytes += 1
            if (frameBytes > EVENT_CAP) fail("event_limit")
            else finishLine()
            if (failure) break
            continue
          }
          finishLine()
          if (failure) break
        }
        frameBytes += 1
        if (frameBytes > EVENT_CAP) {
          fail("event_limit")
          break
        }
        if (byte === 13) pendingCR = true
        else if (byte === 10) finishLine()
        else line.push(byte)
        if (failure) break
      }
      return snapshot(!pendingCR && !frameBytes && textBytes > previousText)
    },
    end() {
      if (!failure && pendingCR) {
        pendingCR = false
        finishLine()
      }
      if (!failure && (frameBytes || data.length || line.length || !finished))
        fail("truncated_stream")
      return snapshot()
    },
  }
}

// Diagnostic-only: bounded, count-only observation of a cloned OpenCode HTTP SSE response.
// This never returns or replaces `original`, and never exposes response text, IDs, or headers.

import { createTextStreamParser } from "./opencode_http_stream_protocol.js"

const OBSERVATION_BUDGET_MS = 75_000
const ACCEPTED_CONTENT_TYPE = /^text\/event-stream(?:\s*;\s*charset\s*=\s*(?:utf-8|"utf-8")\s*)?$/i
const COUNTER_KEYS = Object.freeze([
  "state",
  "reason",
  "wire_bytes",
  "text_bytes",
  "text_events",
  "event_count",
])

function cleanCounters(value) {
  const counters = {}
  for (const key of COUNTER_KEYS) counters[key] = value?.[key]
  return Object.freeze({ ...counters, original_not_replaced: true })
}

function terminalCounters(reason, previous) {
  return cleanCounters({
    state: "unavailable",
    reason,
    wire_bytes: previous?.wire_bytes ?? 0,
    text_bytes: previous?.text_bytes ?? 0,
    text_events: previous?.text_events ?? 0,
    event_count: previous?.event_count ?? 0,
  })
}

function isSseContentType(value) {
  return typeof value === "string" && ACCEPTED_CONTENT_TYPE.test(value.trim())
}

function ignoreThenable(value) {
  try {
    if (
      value !== null &&
      (typeof value === "object" || typeof value === "function") &&
      typeof value.then === "function"
    ) {
      void Promise.resolve(value).catch(() => undefined)
    }
  } catch {}
}

/**
 * Observe only safe counters from a response's cloned SSE body.
 * `onSnapshot` is called synchronously and must return undefined. Its return value is never awaited.
 * The returned object is diagnostic only and carries no PG binding or generation authority.
 */
export async function observeTextResponse(original, { signal, onSnapshot } = {}) {
  const parser = createTextStreamParser()
  let previous = null
  let lastEmitted = null
  let terminal = null
  let reader
  let cloneCancelRequested = false
  let readerLockReleased = false
  let callbackReason = null
  let callbackUsable = typeof onSnapshot === "function"
  let stopReason = null
  let abortResolve
  const abortGate = new Promise((resolve) => {
    abortResolve = resolve
  })

  const cancelCloneReader = () => {
    if (!reader || cloneCancelRequested) return
    cloneCancelRequested = true
    try {
      const cancellation = reader.cancel()
      if (cancellation && typeof cancellation.catch === "function") {
        void cancellation.catch(() => undefined)
      }
    } catch {}
  }

  const stop = (reason) => {
    if (stopReason !== null) return
    stopReason = reason
    cancelCloneReader()
    abortResolve({ kind: "aborted" })
  }

  const emit = (counters) => {
    if (!callbackUsable) return true
    if (counters === lastEmitted) return true
    try {
      const returned = onSnapshot(counters)
      if (returned !== undefined) {
        ignoreThenable(returned)
        callbackUsable = false
        callbackReason = "observer_callback_invalid"
        return false
      }
      lastEmitted = counters
      return true
    } catch {
      callbackUsable = false
      callbackReason = "observer_callback_error"
      return false
    }
  }

  let timer
  let externalAbortListener
  try {
    if (
      signal !== undefined &&
      (!signal ||
        typeof signal.addEventListener !== "function" ||
        typeof signal.removeEventListener !== "function" ||
        typeof signal.aborted !== "boolean")
    ) {
      terminal = terminalCounters("observer_input_invalid", previous)
    } else {
      externalAbortListener = () => stop("aborted")
      if (signal) {
        signal.addEventListener("abort", externalAbortListener, { once: true })
        if (signal.aborted) externalAbortListener()
      }
      timer = setTimeout(() => stop("inspection_timeout"), OBSERVATION_BUDGET_MS)
      timer.unref?.()

      if (stopReason !== null) {
        terminal = terminalCounters(stopReason, previous)
      } else if (
        !original ||
        !Number.isInteger(original.status) ||
        original.status < 200 ||
        original.status >= 300
      ) {
        terminal = terminalCounters("http_error", previous)
      } else {
        let contentType
        try {
          contentType = original.headers?.get?.("content-type")
        } catch {}
        if (!isSseContentType(contentType)) {
          terminal = terminalCounters("content_type_invalid", previous)
        } else {
          let clone
          try {
            clone = original.clone()
          } catch {}
          if (!clone?.body) {
            terminal = terminalCounters("response_unreadable", previous)
          } else {
            try {
              reader = clone.body.getReader()
            } catch {}
            if (!reader) {
              terminal = terminalCounters("response_unreadable", previous)
            } else {
              while (terminal === null) {
                if (stopReason !== null) {
                  terminal = terminalCounters(stopReason, previous)
                  break
                }
                let readPromise
                try {
                  readPromise = reader.read()
                } catch {
                  terminal = terminalCounters("response_unreadable", previous)
                  break
                }
                const safeRead = Promise.resolve(readPromise).then(
                  (value) => ({ kind: "read", value }),
                  () => ({ kind: "read_error" }),
                )
                const outcome = await Promise.race([safeRead, abortGate])
                if (outcome.kind === "aborted") {
                  terminal = terminalCounters(stopReason ?? "aborted", previous)
                  break
                }
                if (outcome.kind === "read_error") {
                  terminal = terminalCounters("response_unreadable", previous)
                  break
                }
                if (outcome.value.done) {
                  previous = cleanCounters(parser.end())
                  terminal = previous
                  break
                }
                if (!(outcome.value.value instanceof Uint8Array)) {
                  terminal = terminalCounters("response_unreadable", previous)
                  break
                }
                previous = cleanCounters(parser.feed(outcome.value.value))
                if (!emit(previous)) {
                  stop(callbackReason)
                  terminal = terminalCounters(callbackReason, previous)
                  break
                }
                if (previous.state === "unavailable") {
                  cancelCloneReader()
                  terminal = previous
                  break
                }
                if (previous.state === "finished") {
                  cancelCloneReader()
                  terminal = previous
                  break
                }
              }
            }
          }
        }
      }
    }
  } catch {
    terminal = terminalCounters("observer_error", previous)
  } finally {
    if (signal && externalAbortListener) {
      try {
        signal.removeEventListener("abort", externalAbortListener)
      } catch {}
    }
    if (timer !== undefined) clearTimeout(timer)
    if (reader) {
      cancelCloneReader()
      // Releasing the clone lock rejects a still-pending clone read after abort. Do not await
      // reader.cancel(): tee cancellation can remain pending while the original branch is alive.
      try {
        reader.releaseLock()
        readerLockReleased = true
      } catch {}
    }
  }

  if (terminal === null) terminal = terminalCounters("observer_error", previous)
  if (callbackUsable && !emit(terminal)) {
    terminal = terminalCounters(callbackReason, terminal)
  }
  return Object.freeze({
    ...terminal,
    cleanup_observed: Object.freeze({
      clone_cancel_requested: cloneCancelRequested,
      reader_lock_released: readerLockReleased,
    }),
  })
}

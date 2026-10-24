import fs from "node:fs/promises"
import fsSync from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { inspectRequest, parseJsonWithoutDuplicateKeys, publishRecord } from "./opencode_http_prompt_observer.js"
import { parseResponseArguments } from "./opencode_http_response_protocol.js"

const PROVIDER_ID = "opencode-go"
const MODEL_ID = "glm-5.3-flash"
const NONCE_RE = /^[0-9a-f]{32}$/
const MAX_BODY_BYTES = 1024 * 1024
const MAX_EXPECTATION_BYTES = 64 * 1024
const MAX_RECORD_BYTES = 4096
const MAX_SEAL_BYTES = 2048
const MAX_LEDGER_BYTES = 72 * 1024
const MAX_RESPONSES = 16
const PHASE_MS = 75000
const CLEANUP_MS = 2000
const STATUSES = new Set(["observed", "no_target_tool", "unavailable"])
const PARSER_REASONS = new Set([
  "none", "unsupported_content_type", "body_oversized", "malformed_response",
  "ambiguous_response", "incomplete_response", "malformed_arguments",
  "arguments_oversized", "invalid_expectation",
  "json_syntax_or_duplicate", "json_shape", "sse_event_envelope", "sse_choice",
  "sse_delta", "sse_tool_fragment", "sse_initial_identity",
  "sse_continuation_identity", "sse_arguments_fragment", "sse_terminal_order",
  "sse_finish_reason",
])

function identity(info) {
  return [info.dev, info.ino, info.uid, info.gid, info.nlink, info.size,
    info.mtimeMs, info.ctimeMs, info.mode].join(":")
}

function privateDirectory(value) {
  try {
    const info = fsSync.lstatSync(value)
    return info.isDirectory() && !info.isSymbolicLink() &&
      info.uid === process.getuid() && (info.mode & 0o777) === 0o700 &&
      fsSync.realpathSync(value) === value
  } catch { return false }
}

function privateFile(info) {
  return info.isFile() && !info.isSymbolicLink() && info.uid === process.getuid() &&
    info.nlink === 1 && (info.mode & 0o777) === 0o600
}

function validText(value) {
  if (typeof value !== "string" || !value || Buffer.byteLength(value, "utf8") > 8192) return false
  for (let i = 0; i < value.length; i += 1) {
    const code = value.charCodeAt(i)
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(++i)
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false
    } else if (code >= 0xdc00 && code <= 0xdfff) return false
  }
  return true
}

async function phaseMessages(fsApi, opencodeDirectory, nonce) {
  let handle
  try {
    if (!privateDirectory(opencodeDirectory) || !privateDirectory(path.dirname(opencodeDirectory))) return null
    const file = path.join(opencodeDirectory, "http-response-argument-expectations.json")
    const before = fsSync.lstatSync(file)
    if (!privateFile(before) || before.size > MAX_EXPECTATION_BYTES) return null
    handle = await fsApi.open(file, fsSync.constants.O_RDONLY | (fsSync.constants.O_NOFOLLOW ?? 0))
    const opened = await handle.stat()
    if (!privateFile(opened) || identity(before) !== identity(opened)) return null
    const bytes = Buffer.alloc(MAX_EXPECTATION_BYTES + 1)
    let used = 0
    while (used < bytes.length) {
      const part = await handle.read(bytes, used, bytes.length - used, null)
      if (!part.bytesRead) break
      used += part.bytesRead
    }
    if (used > MAX_EXPECTATION_BYTES || used !== opened.size ||
      identity(await handle.stat()) !== identity(opened) ||
      identity(fsSync.lstatSync(file)) !== identity(opened)) return null
    const document = parseJsonWithoutDuplicateKeys(
      new TextDecoder("utf-8", { fatal: true }).decode(bytes.subarray(0, used)),
    )
    if (!document || Array.isArray(document) ||
      Object.keys(document).sort().join(",") !== "nonce,phase_messages,schema_version" ||
      document.schema_version !== 1 || document.nonce !== nonce ||
      !Array.isArray(document.phase_messages) || document.phase_messages.length !== 2 ||
      !document.phase_messages.every(validText)) return null
    return document.phase_messages
  } catch { return null }
  finally { if (handle) await handle.close().catch(() => undefined) }
}

function selected(event) {
  try {
    return event?.kind === "primary" && event?.model?.providerID === PROVIDER_ID &&
      event?.model?.id === MODEL_ID
  } catch { return false }
}

function waitBounded(promise, milliseconds) {
  let timer
  return Promise.race([
    Promise.resolve(promise).then(() => true, () => false),
    new Promise((resolve) => { timer = setTimeout(() => resolve(false), milliseconds) }),
  ]).finally(() => clearTimeout(timer))
}

async function stopAware(promise, signal) {
  if (signal.aborted) throw new Error("stopped")
  let stop
  const cancelled = new Promise((_, reject) => {
    stop = () => reject(new Error("stopped"))
    signal.addEventListener("abort", stop, { once: true })
    if (signal.aborted) stop()
  })
  try { return await Promise.race([promise, cancelled]) }
  finally { signal.removeEventListener("abort", stop) }
}

async function cloneText(copy, signal) {
  let reader
  const cancel = () => {
    try { void reader?.cancel().catch(() => undefined) } catch {}
  }
  try {
    if (signal.aborted) return { reason: "inspection_timeout", complete: false }
    reader = copy.body?.getReader()
    if (!reader) return { reason: "response_unreadable", complete: false }
    signal.addEventListener("abort", cancel, { once: true })
    if (signal.aborted) cancel()
    const chunks = []
    let total = 0
    while (true) {
      const part = await stopAware(reader.read(), signal)
      if (part.done) break
      if (!(part.value instanceof Uint8Array)) return { reason: "response_unreadable", complete: false }
      total += part.value.byteLength
      if (total > MAX_BODY_BYTES) {
        cancel()
        return { reason: "body_oversized", complete: false }
      }
      chunks.push(part.value)
    }
    const bytes = new Uint8Array(total)
    let at = 0
    for (const part of chunks) { bytes.set(part, at); at += part.byteLength }
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes)
    return { text, complete: true, reason: "none" }
  } catch { return { reason: "response_unreadable", complete: false } }
  finally {
    signal.removeEventListener("abort", cancel)
    try { reader?.releaseLock() } catch {}
  }
}

function record(nonce, ordinal) {
  return {
    schema_version: 1, nonce, ordinal, phase_ordinal: null, request_bound: false,
    status: "unavailable", reason: "unbound_request", target_tool_call_count: null,
    argument_exact: false, known_punctuation_variant_exact: false,
    expected_utf8_bytes: null, observed_utf8_bytes: null,
    clone_complete: false, original_not_replaced: false,
  }
}

function protocolResult(value, expectedBytes) {
  if (!value || !STATUSES.has(value.status) || !PARSER_REASONS.has(value.reason) ||
    typeof value.argument_exact !== "boolean" ||
    typeof value.known_punctuation_variant_exact !== "boolean" ||
    value.expected_utf8_bytes !== expectedBytes ||
    (value.argument_exact && value.known_punctuation_variant_exact)) return null
  if (value.status === "observed") {
    if (value.reason !== "none" || value.target_tool_call_count !== 1 ||
      !Number.isInteger(value.observed_utf8_bytes) || value.observed_utf8_bytes < 0 ||
      value.observed_utf8_bytes > 65536) return null
  } else if (value.status === "no_target_tool") {
    if (value.reason !== "none" || value.target_tool_call_count !== 0 ||
      value.argument_exact || value.known_punctuation_variant_exact || value.observed_utf8_bytes !== null) return null
  } else if (value.reason === "none" || value.target_tool_call_count !== null ||
    value.argument_exact || value.known_punctuation_variant_exact || value.observed_utf8_bytes !== null) return null
  return {
    status: value.status, reason: value.reason,
    target_tool_call_count: value.target_tool_call_count,
    argument_exact: value.argument_exact,
    known_punctuation_variant_exact: value.known_punctuation_variant_exact,
    expected_utf8_bytes: expectedBytes, observed_utf8_bytes: value.observed_utf8_bytes,
  }
}

export function createHttpResponseArgumentObserver({
  nonce, moduleUrl = import.meta.url, fsApi = fs,
} = {}) {
  return {
    id: "cao-opencode-http-response-argument-observer",
    async setup(context) {
      if (typeof nonce !== "string" || !NONCE_RE.test(nonce)) return undefined
      let pluginDirectory, opencodeDirectory, directory
      try {
        const url = new URL(moduleUrl)
        if (url.protocol !== "file:" || url.search || url.hash) return undefined
        const modulePath = fileURLToPath(url)
        pluginDirectory = path.dirname(modulePath)
        opencodeDirectory = path.dirname(pluginDirectory)
        if (path.basename(pluginDirectory) !== "plugins" ||
          path.basename(opencodeDirectory) !== ".opencode" ||
          !privateDirectory(path.dirname(opencodeDirectory)) ||
          !privateDirectory(opencodeDirectory) || !privateDirectory(pluginDirectory) ||
          !privateFile(fsSync.lstatSync(modulePath))) return undefined
        directory = path.join(opencodeDirectory, "http-response-argument-observation")
        await fsApi.mkdir(directory, { mode: 0o700 })
        if (!privateDirectory(directory)) return undefined
      } catch { return undefined }

      const state = {
        requests: new Set(), byRequest: new WeakMap(), pending: new Set(),
        requestCount: 0, responseCount: 0, durableCount: 0, totalBytes: 0,
        integrity: true, overflow: false, closing: false, sealed: false,
      }
      const registrations = []
      let cleanupPromise

      const onRequest = (event) => {
        try {
          if (!selected(event)) return undefined
          if (state.closing || state.requestCount >= MAX_RESPONSES) {
            state.overflow = true; state.integrity = false; return undefined
          }
          const ordinal = ++state.requestCount
          const request = event.request
          if (!request || typeof request !== "object" || state.byRequest.has(request)) {
            state.integrity = false; return undefined
          }
          const snapshot = request.clone()
          const entry = {
            controller: new AbortController(), responded: false, timedOut: false,
            deadline: Date.now() + PHASE_MS,
          }
          entry.timer = setTimeout(() => {
            entry.timedOut = true
            entry.controller.abort()
          }, PHASE_MS)
          entry.timer.unref?.()
          const cancelSnapshot = () => {
            try { void snapshot.body?.cancel().catch(() => undefined) } catch {}
          }
          entry.controller.signal.addEventListener("abort", cancelSnapshot, { once: true })
          entry.classification = inspectRequest({
            model: { providerID: PROVIDER_ID, id: MODEL_ID }, kind: "primary", request: snapshot,
          }, {
            fsApi, opencodeDirectory, nonce, ordinal, signal: entry.controller.signal,
          }).catch(() => null).finally(() => {
            entry.controller.signal.removeEventListener("abort", cancelSnapshot)
            cancelSnapshot()
          })
          state.byRequest.set(request, entry)
          state.requests.add(entry)
        } catch { state.integrity = false }
        return undefined
      }

      const processResponse = (event) => {
        if (!selected(event)) return undefined
        if (state.closing || state.responseCount >= MAX_RESPONSES) {
          state.overflow = true; state.integrity = false; return undefined
        }
        const ordinal = ++state.responseCount
        const original = event.response
        const entry = event.request && typeof event.request === "object"
          ? state.byRequest.get(event.request) : undefined
        const boundEntry = entry && !entry.responded ? entry : undefined
        if (boundEntry) boundEntry.responded = true
        else state.integrity = false
        let copy
        if (boundEntry && original && original.status >= 200 && original.status < 300) {
          try { copy = original.clone() } catch {}
        }
        const cancelCopy = () => {
          try { void copy?.body?.cancel().catch(() => undefined) } catch {}
        }
        boundEntry?.controller.signal.addEventListener("abort", cancelCopy, { once: true })
        const operation = (async () => {
          let item = record(nonce, ordinal)
          item.original_not_replaced = original !== undefined && event.response === original
          try {
            if (boundEntry) {
              const signal = boundEntry.controller.signal
              const classified = await stopAware(boundEntry.classification, signal)
              if (classified?.status === "observed" && classified.source_prompt_exact === true &&
                classified.receipt_envelope_shape_valid === true && classified.last_instruction_present === true &&
                [1, 2].includes(classified.phase_ordinal)) {
                item.phase_ordinal = classified.phase_ordinal
                item.request_bound = true
                const messages = await stopAware(phaseMessages(fsApi, opencodeDirectory, nonce), signal)
                if (messages === null) item.reason = "expectations_unavailable"
                else {
                  const expected = messages[item.phase_ordinal - 1]
                  item.expected_utf8_bytes = Buffer.byteLength(expected, "utf8")
                  if (!original || typeof original.status !== "number" || original.status < 200 || original.status >= 300) {
                    item.reason = "http_error"
                  } else {
                    const clone = copy ? await cloneText(copy, signal)
                      : { reason: "response_unreadable", complete: false }
                    item.clone_complete = clone.complete
                    if (!clone.complete) item.reason = clone.reason
                    else {
                      const parsed = protocolResult(parseResponseArguments({
                        bodyText: clone.text, contentType: original.headers?.get("content-type") ?? "",
                        expectedMessage: expected, expectedToolName: "cao-mcp-server_send_message",
                      }), item.expected_utf8_bytes)
                      if (parsed === null) item.reason = "observer_error"
                      else item = { ...item, ...parsed }
                    }
                  }
                }
              }
              if (boundEntry.timedOut || Date.now() > boundEntry.deadline) {
                item = { ...record(nonce, ordinal), phase_ordinal: item.phase_ordinal,
                  request_bound: item.request_bound, expected_utf8_bytes: item.expected_utf8_bytes,
                  reason: "inspection_timeout" }
              }
            }
          } catch {
            item.status = "unavailable"
            item.reason = boundEntry?.timedOut ? "inspection_timeout" : "response_unreadable"
            item.target_tool_call_count = null
            item.argument_exact = false
            item.known_punctuation_variant_exact = false
            item.observed_utf8_bytes = null
          } finally {
            if (boundEntry) {
              clearTimeout(boundEntry.timer)
              boundEntry.controller.signal.removeEventListener("abort", cancelCopy)
            }
            cancelCopy()
          }
          item.original_not_replaced = original !== undefined && event.response === original
          if (!item.original_not_replaced) {
            item.status = "unavailable"; item.reason = "observer_error"
            item.target_tool_call_count = null; item.observed_utf8_bytes = null
            item.argument_exact = false; item.known_punctuation_variant_exact = false
          }
          if (state.sealed) return
          const size = Buffer.byteLength(JSON.stringify(item), "utf8")
          if (state.totalBytes + size + MAX_SEAL_BYTES > MAX_LEDGER_BYTES) {
            state.overflow = true; state.integrity = false; return
          }
          const published = await publishRecord({ fsApi, directory,
            fileName: `response-${String(ordinal).padStart(4, "0")}.json`,
            payload: item, maximum: MAX_RECORD_BYTES, shouldPublish: () => !state.sealed })
          if (published === null) state.integrity = false
          else { state.totalBytes += published; state.durableCount += 1 }
        })().catch(() => { state.integrity = false })
        state.pending.add(operation)
        void operation.finally(() => state.pending.delete(operation))
        return undefined
      }

      const onResponse = (event) => {
        try { return processResponse(event) }
        catch { state.integrity = false; return undefined }
      }

      try {
        registrations.push(await context.session.hook("http.request", onRequest, { providerID: PROVIDER_ID }))
        registrations.push(await context.session.hook("http.response", onResponse, { providerID: PROVIDER_ID }))
      } catch { state.integrity = false }

      return () => {
        if (cleanupPromise) return cleanupPromise
        cleanupPromise = (async () => {
          state.closing = true
          const deadline = Date.now() + CLEANUP_MS
          const disposed = registrations.length === 2 && await waitBounded(
            Promise.all(registrations.map(async (registration) => {
              if (typeof registration?.dispose !== "function") throw new Error("missing_dispose")
              await registration.dispose()
            })), Math.max(0, deadline - Date.now()),
          )
          let settled = await waitBounded(Promise.all([...state.pending]),
            Math.max(0, deadline - Date.now()))
          for (const entry of state.requests) {
            clearTimeout(entry.timer)
            entry.controller.abort()
          }
          if (!settled) settled = await waitBounded(Promise.all([...state.pending]),
            Math.max(0, deadline - Date.now()))
          const classified = await waitBounded(Promise.all(
            [...state.requests].map((entry) => entry.classification),
          ), Math.max(0, deadline - Date.now()))
          state.sealed = true
          if (!settled || state.pending.size || Date.now() >= deadline) return
          const traceComplete = disposed && settled && classified && state.integrity && !state.overflow &&
            state.requestCount === state.responseCount && state.responseCount === state.durableCount &&
            [...state.requests].every((entry) => entry.responded)
          const seal = {
            schema_version: 1, nonce, hook_disposed: disposed, trace_complete: traceComplete,
            request_count: state.requestCount, response_count: state.responseCount,
            durable_record_count: state.durableCount, overflow: state.overflow,
          }
          let expired = false
          const published = await waitBounded(publishRecord({ fsApi, directory,
            fileName: "seal.json", payload: seal, maximum: MAX_SEAL_BYTES,
            shouldPublish: () => !expired && Date.now() < deadline }),
          Math.max(0, deadline - Date.now()))
          if (!published) expired = true
        })()
        return cleanupPromise
      }
    },
  }
}

export default createHttpResponseArgumentObserver()

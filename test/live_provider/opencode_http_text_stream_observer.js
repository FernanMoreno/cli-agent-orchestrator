// Ephemeral diagnostic evidence only. PostgreSQL remains the turn authority.
import fs from "node:fs/promises"
import fsSync from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { inspectRequest, publishRecord } from "./opencode_http_prompt_observer.js"
import { observeTextResponse } from "./opencode_http_text_response_monitor.js"

const PROVIDER_ID = "opencode-go"
const MODEL_ID = "glm-5.3-flash"
const NONCE = /^[a-f0-9]{32}$/
const TERMINAL = /^[A-Za-z0-9_-]{1,64}$/
const REQUEST_CAP = 16
const SAMPLE_CAP = 128
const LEDGER_CAP = 72 * 1024
const PHASE_MS = 75000
const CLEANUP_MS = 2000
// At most101 regular attempts in75s, leaving slots for terminal/cleanup evidence.
const SAMPLE_INTERVAL_MS = 750

function privateDirectory(value) {
  try {
    const info = fsSync.lstatSync(value)
    return (
      info.isDirectory() &&
      !info.isSymbolicLink() &&
      info.uid === process.getuid() &&
      (info.mode & 0o777) === 0o700 &&
      fsSync.realpathSync(value) === value
    )
  } catch {
    return false
  }
}

function privateFile(value) {
  try {
    const info = fsSync.lstatSync(value)
    return (
      info.isFile() &&
      !info.isSymbolicLink() &&
      info.uid === process.getuid() &&
      info.nlink === 1 &&
      (info.mode & 0o777) === 0o600
    )
  } catch {
    return false
  }
}

function selected(event) {
  return (
    event?.kind === "primary" &&
    event?.model?.providerID === PROVIDER_ID &&
    event?.model?.id === MODEL_ID
  )
}

async function bounded(promise, milliseconds) {
  let timer
  try {
    return await Promise.race([
      Promise.resolve(promise).then(
        () => true,
        () => false,
      ),
      new Promise((resolve) => {
        timer = setTimeout(() => resolve(false), milliseconds)
      }),
    ])
  } finally {
    clearTimeout(timer)
  }
}

export function createHttpTextStreamObserver({
  nonce,
  moduleUrl = import.meta.url,
  fsApi = fs,
  terminalId,
} = {}) {
  return {
    id: "cao-opencode-http-text-stream-observer",
    async setup(context) {
      if (
        typeof nonce !== "string" ||
        !NONCE.test(nonce) ||
        (terminalId !== undefined && (typeof terminalId !== "string" || !TERMINAL.test(terminalId)))
      )
        return undefined
      let directory, opencode
      try {
        const url = new URL(moduleUrl)
        if (url.protocol !== "file:" || url.search || url.hash) return undefined
        const modulePath = fileURLToPath(url)
        const plugins = path.dirname(modulePath)
        opencode = path.dirname(plugins)
        if (
          path.basename(plugins) !== "plugins" ||
          path.basename(opencode) !== ".opencode" ||
          !privateDirectory(plugins) ||
          !privateDirectory(opencode) ||
          !privateDirectory(path.dirname(opencode)) ||
          !privateFile(modulePath)
        )
          return undefined
        directory = path.join(opencode, "http-text-stream-observation")
        try {
          await fsApi.mkdir(directory, { mode: 0o700 })
        } catch (error) {
          if (!terminalId || error?.code !== "EEXIST") throw error
        }
        if (!privateDirectory(directory)) return undefined
        if (terminalId) {
          directory = path.join(directory, terminalId)
          await fsApi.mkdir(directory, { mode: 0o700 })
          if (!privateDirectory(directory)) return undefined
        }
      } catch {
        return undefined
      }

      const state = {
        requests: new Set(),
        byRequest: new WeakMap(),
        pending: new Set(),
        registrations: [],
        requestCount: 0,
        eligible: 0,
        sequence: 0,
        durableCount: 0,
        bytes: 0,
        integrity: true,
        closing: false,
        sealed: false,
        invalidStarted: false,
      }
      let cleanupPromise
      const track = (operation) => {
        state.pending.add(operation)
        void operation.finally(() => state.pending.delete(operation)).catch(() => undefined)
        return operation
      }

      const invalidate = () => {
        state.integrity = false
        for (const entry of state.requests) entry.controller.abort()
        if (state.invalidStarted || state.sealed) return
        state.invalidStarted = true
        track(
          publishRecord({
            fsApi,
            directory,
            fileName: "invalid.json",
            maximum: 1024,
            payload: {
              schema_version: 1,
              nonce,
              status: "unavailable",
              reason: "observer_integrity",
            },
            shouldPublish: () => !state.sealed,
          }).catch(() => null),
        )
      }

      const schedule = (entry) => {
        if (
          entry.latest &&
          ["finished", "unavailable"].includes(entry.latest.state) &&
          entry.publishTimer
        ) {
          clearTimeout(entry.publishTimer)
          entry.publishTimer = null
        }
        if (
          !entry.bound ||
          !entry.latest ||
          entry.publishing ||
          entry.publishTimer ||
          state.sealed ||
          !state.integrity
        )
          return
        const current = entry.latest
        const terminal = current.state === "finished" || current.state === "unavailable"
        const wait =
          !terminal && entry.lastAttempt !== null
            ? SAMPLE_INTERVAL_MS - (Date.now() - entry.lastAttempt)
            : 0
        if (wait > 0) {
          entry.publishTimer = setTimeout(() => {
            entry.publishTimer = null
            schedule(entry)
          }, wait)
          entry.publishTimer.unref?.()
          return
        }
        if (state.sequence >= SAMPLE_CAP || state.bytes + 4096 + 2048 > LEDGER_CAP) {
          invalidate()
          return
        }
        const sequence = ++state.sequence
        entry.lastAttempt = Date.now()
        const item = {
          schema_version: 1,
          nonce,
          sequence,
          request_ordinal: entry.ordinal,
          request_bound: true,
          phase_ordinal: 2,
          receipt_sha256: entry.bound.receipt_sha256,
          state: current.state,
          reason: current.reason,
          wire_bytes: current.wire_bytes,
          text_bytes: current.text_bytes,
          text_events: current.text_events,
          event_count: current.event_count,
          original_not_replaced: current.original_not_replaced,
        }
        entry.publishing = true
        const operation = publishRecord({
          fsApi,
          directory,
          fileName: `sample-${String(sequence).padStart(4, "0")}.json`,
          maximum: 4096,
          payload: item,
          shouldPublish: () =>
            !state.sealed &&
            state.integrity &&
            entry.latest === current &&
            entry.event?.response === entry.original,
        })
          .then((size) => {
            if (size === null) {
              if (entry.latest === current && !state.sealed && state.integrity) invalidate()
            } else {
              state.bytes += size
              state.durableCount += 1
            }
          })
          .catch(invalidate)
          .finally(() => {
            entry.publishing = false
            if (entry.latest !== current) schedule(entry)
          })
        track(operation)
      }

      const onRequest = (event) => {
        try {
          if (!selected(event)) return undefined
          if (
            state.closing ||
            ++state.requestCount > REQUEST_CAP ||
            !event.request ||
            state.byRequest.has(event.request)
          ) {
            invalidate()
            return undefined
          }
          const entry = {
            ordinal: state.requestCount,
            controller: new AbortController(),
            responded: false,
            bound: null,
            latest: null,
            publishing: false,
            publishTimer: null,
            lastAttempt: null,
          }
          state.byRequest.set(event.request, entry)
          state.requests.add(entry)
          entry.timer = setTimeout(() => entry.controller.abort(), PHASE_MS)
          entry.timer.unref?.()
          const snapshot = event.request.clone()
          const cancelSnapshot = () => {
            try {
              void snapshot.body?.cancel().catch(() => undefined)
            } catch {}
          }
          entry.controller.signal.addEventListener("abort", cancelSnapshot, { once: true })
          entry.classification = track(
            inspectRequest(
              { ...event, request: snapshot },
              {
                fsApi,
                opencodeDirectory: opencode,
                nonce,
                ordinal: entry.ordinal,
                signal: entry.controller.signal,
                includeReceiptDigest: true,
                terminalId,
              },
            )
              .then((result) => {
                if (
                  result?.status === "observed" &&
                  result.phase_ordinal === 2 &&
                  result.source_prompt_exact === true &&
                  result.last_instruction_present === true &&
                  result.receipt_envelope_shape_valid === true &&
                  typeof result.receipt_sha256 === "string" &&
                  /^[a-f0-9]{64}$/.test(result.receipt_sha256)
                ) {
                  entry.bound = result
                  if (++state.eligible > 1) invalidate()
                  else schedule(entry)
                }
              })
              .catch(invalidate)
              .finally(() => {
                entry.controller.signal.removeEventListener("abort", cancelSnapshot)
                cancelSnapshot()
              }),
          )
        } catch {
          invalidate()
        }
        return undefined
      }

      const onResponse = (event) => {
        try {
          if (!selected(event)) return undefined
          const entry = event.request && state.byRequest.get(event.request)
          if (state.closing || !entry || entry.responded) {
            invalidate()
            return undefined
          }
          entry.responded = true
          entry.event = event
          entry.original = event.response
          // Start reading immediately; request classification and filesystem publication
          // must never hold the original response or the clone's read loop.
          track(
            observeTextResponse(entry.original, {
              signal: entry.controller.signal,
              onSnapshot(snapshot) {
                entry.latest = {
                  ...snapshot,
                  original_not_replaced:
                    snapshot.original_not_replaced && event.response === entry.original,
                }
                schedule(entry)
              },
            })
              .then(() => entry.classification)
              .catch(invalidate)
              .finally(() => clearTimeout(entry.timer)),
          )
        } catch {
          invalidate()
        }
        return undefined
      }

      try {
        state.registrations.push(
          await context.session.hook("http.request", onRequest, { providerID: PROVIDER_ID }),
        )
        state.registrations.push(
          await context.session.hook("http.response", onResponse, { providerID: PROVIDER_ID }),
        )
      } catch {
        invalidate()
      }

      return () => {
        if (cleanupPromise) return cleanupPromise
        cleanupPromise = (async () => {
          state.closing = true
          const deadline = Date.now() + CLEANUP_MS
          const disposed = await bounded(
            Promise.all(
              state.registrations.map(async (registration) => {
                if (typeof registration?.dispose !== "function") throw new Error("dispose_missing")
                await registration.dispose()
              }),
            ),
            Math.max(0, deadline - Date.now()),
          )
          for (const entry of state.requests) {
            clearTimeout(entry.timer)
            clearTimeout(entry.publishTimer)
            entry.publishTimer = null
            entry.controller.abort()
          }
          let settled = true
          while (state.pending.size && settled && Date.now() < deadline) {
            settled = await bounded(
              Promise.all([...state.pending]),
              Math.max(0, deadline - Date.now()),
            )
          }
          const complete =
            disposed &&
            state.registrations.length === 2 &&
            settled &&
            !state.pending.size &&
            state.integrity
          state.sealed = true
          if (Date.now() >= deadline) return
          await bounded(
            publishRecord({
              fsApi,
              directory,
              fileName: "seal.json",
              maximum: 2048,
              payload: {
                schema_version: 1,
                nonce,
                trace_complete: complete,
                hook_disposed: disposed,
                request_count: state.requestCount,
                eligible_requests: state.eligible,
                sample_count: state.durableCount,
                sample_attempts: state.sequence,
              },
              shouldPublish: () => Date.now() < deadline,
            }),
            Math.max(0, deadline - Date.now()),
          )
        })()
        return cleanupPromise
      }
    },
  }
}

export default createHttpTextStreamObserver()

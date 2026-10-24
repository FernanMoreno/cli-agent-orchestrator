import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const NONCE_RE = /^[0-9a-f]{32}$/
const ID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/
const MODULE_NAME = "response_hook_probe.js"
const LEDGER_NAME = "http-response-probe"
const MARKER = Buffer.from("t127-wire-control", "ascii")
const MAX_CLONE_BYTES = 64 * 1024
const MAX_RESPONSES = 16
const MAX_INSPECTION_MS = 2000
const MAX_LEDGER_BYTES = 16 * 1024
const MAX_RECORD_BYTES = 512
const MAX_SEAL_BYTES = 2048

function privateDirectory(value) {
  try {
    const target = path.resolve(value)
    const info = fs.lstatSync(target)
    return info.isDirectory() && !info.isSymbolicLink() && info.uid === process.getuid() &&
      (info.mode & 0o777) === 0o700 && fs.realpathSync(target) === target
  } catch { return false }
}

function privateFile(value) {
  try {
    const target = path.resolve(value)
    const info = fs.lstatSync(target)
    return info.isFile() && !info.isSymbolicLink() && info.uid === process.getuid() &&
      info.nlink === 1 && (info.mode & 0o777) === 0o600 && fs.realpathSync(target) === target
  } catch { return false }
}

function binding(options) {
  if (
    typeof options.nonce !== "string" || !NONCE_RE.test(options.nonce) ||
    typeof options.moduleUrl !== "string" || typeof options.ledgerPath !== "string" ||
    !path.isAbsolute(options.ledgerPath) || typeof options.providerID !== "string" ||
    !ID_RE.test(options.providerID) || typeof options.modelID !== "string" || !ID_RE.test(options.modelID)
  ) return null
  try {
    const url = new URL(options.moduleUrl)
    if (url.protocol !== "file:" || url.search || url.hash) return null
    const modulePath = path.resolve(fileURLToPath(url))
    const pluginDirectory = path.dirname(modulePath)
    const opencodeDirectory = path.dirname(pluginDirectory)
    const workDirectory = path.dirname(opencodeDirectory)
    const ledgerDirectory = path.join(opencodeDirectory, LEDGER_NAME)
    if (
      path.basename(modulePath) !== MODULE_NAME || path.basename(pluginDirectory) !== "plugins" ||
      path.basename(opencodeDirectory) !== ".opencode" || path.resolve(options.ledgerPath) !== ledgerDirectory ||
      !privateDirectory(workDirectory) || !privateDirectory(opencodeDirectory) ||
      !privateDirectory(pluginDirectory) || !privateFile(modulePath)
    ) return null
    return { ledgerDirectory }
  } catch { return null }
}

function record(state, event, status, ordinal, flags = {}) {
  const item = {
    schema_version: 1,
    event,
    status,
    ordinal,
    hook_registered: state.hookRegistered,
    selected_primary_response_seen: flags.selected === true,
    synthetic_wire_marker_seen: flags.marker === true,
    clone_completed: flags.complete === true,
    inspection_unavailable: flags.unavailable === true,
    original_not_replaced: flags.originalUnchanged === true,
    cleanup_complete: flags.cleanup === true,
  }
  if (Buffer.byteLength(JSON.stringify(item), "utf8") > MAX_RECORD_BYTES) state.overflow = true
  else state.records.push(item)
}

function cancelReader(reader) {
  try { void reader.cancel().catch(() => {}) } catch {}
}

function boundedWait(promise, milliseconds) {
  let timer
  const timeout = new Promise((resolve) => {
    timer = setTimeout(() => resolve(false), milliseconds)
  })
  return Promise.race([
    Promise.resolve(promise).then(() => true, () => false),
    timeout,
  ]).finally(() => clearTimeout(timer))
}

function inspectResponse(response) {
  let reader
  try {
    const clone = response?.clone?.()
    if (!clone?.body || typeof clone.body.getReader !== "function") {
      return { promise: Promise.resolve({ complete: false, marker: false }), cancel() {} }
    }
    reader = clone.body.getReader()
  } catch {
    return { promise: Promise.resolve({ complete: false, marker: false }), cancel() {} }
  }
  const cancel = () => cancelReader(reader)
  let timer
  let timedOut = false
  const read = (async () => {
    let total = 0
    let marker = false
    let tail = Buffer.alloc(0)
    while (true) {
      const item = await reader.read()
      if (item.done) return { complete: true, marker }
      if (!(item.value instanceof Uint8Array) || total + item.value.byteLength > MAX_CLONE_BYTES) {
        cancel()
        return { complete: false, marker: false }
      }
      total += item.value.byteLength
      const chunk = Buffer.from(item.value.buffer, item.value.byteOffset, item.value.byteLength)
      const window = tail.length ? Buffer.concat([tail, chunk]) : chunk
      if (window.indexOf(MARKER) !== -1) marker = true
      tail = window.subarray(Math.max(0, window.length - MARKER.length + 1))
    }
  })().catch(() => ({ complete: false, marker: false }))
  const deadline = new Promise((resolve) => {
    timer = setTimeout(() => {
      timedOut = true
      resolve({ complete: false, marker: false })
    }, MAX_INSPECTION_MS)
  })
  const promise = Promise.race([read, deadline]).then((result) => {
    if (timedOut) cancel()
    return result.complete === true
      ? { complete: true, marker: result.marker === true }
      : { complete: false, marker: false }
  }).finally(() => clearTimeout(timer))
  return { promise, cancel }
}

function writeNew(filePath, bytes, maximum) {
  let descriptor
  try {
    if (bytes.length > maximum) return false
    const flags = fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL |
      (fs.constants.O_NOFOLLOW ?? 0) | (fs.constants.O_CLOEXEC ?? 0)
    descriptor = fs.openSync(filePath, flags, 0o600)
    const info = fs.fstatSync(descriptor)
    if (!info.isFile() || info.uid !== process.getuid() || info.nlink !== 1 || (info.mode & 0o777) !== 0o600) return false
    let offset = 0
    while (offset < bytes.length) {
      const written = fs.writeSync(descriptor, bytes, offset, bytes.length - offset)
      if (written <= 0) return false
      offset += written
    }
    fs.fsyncSync(descriptor)
    fs.closeSync(descriptor)
    descriptor = undefined
    return privateFile(filePath)
  } catch { return false }
  finally { if (descriptor !== undefined) { try { fs.closeSync(descriptor) } catch {} } }
}

function saveEvidence(state, seal) {
  try {
    if (!privateDirectory(state.ledgerDirectory)) return false
    const events = Buffer.from(state.records.map((item) => JSON.stringify(item)).join("\n") + "\n", "utf8")
    if (!writeNew(path.join(state.ledgerDirectory, "events.jsonl"), events, MAX_LEDGER_BYTES)) return false
    const sealBytes = Buffer.from(JSON.stringify(seal) + "\n", "utf8")
    const temporary = path.join(state.ledgerDirectory, "seal.tmp")
    const target = path.join(state.ledgerDirectory, "seal.json")
    if (!writeNew(temporary, sealBytes, MAX_SEAL_BYTES)) return false
    try {
      fs.linkSync(temporary, target)
    } catch {
      try { fs.unlinkSync(temporary) } catch {}
      return false
    }
    try { fs.unlinkSync(temporary) } catch {}
    if (!privateFile(target)) return false
    const dirFd = fs.openSync(state.ledgerDirectory, fs.constants.O_RDONLY | (fs.constants.O_DIRECTORY ?? 0) | (fs.constants.O_NOFOLLOW ?? 0))
    try { fs.fsyncSync(dirFd) } finally { fs.closeSync(dirFd) }
    return true
  } catch { return false }
}

function matches(event, providerID, modelID) {
  try {
    return event?.kind === "primary" && event.model?.providerID === providerID && event.model?.id === modelID
  } catch { return false }
}

export function createHttpResponseProbe(options = {}) {
  return {
    id: "spec025-t127-http-response-probe",
    async setup(context) {
      const config = {
        nonce: options.nonce,
        moduleUrl: options.moduleUrl ?? import.meta.url,
        ledgerPath: options.ledgerPath,
        providerID: options.providerID,
        modelID: options.modelID,
      }
      const paths = binding(config)
      if (!paths || typeof context?.session?.hook !== "function") return undefined
      try { fs.mkdirSync(paths.ledgerDirectory, { mode: 0o700 }) } catch { return undefined }
      if (!privateDirectory(paths.ledgerDirectory)) return undefined

      const state = {
        ledgerDirectory: paths.ledgerDirectory,
        hookRegistered: false,
        hookDisposed: false,
        responseCount: 0,
        cloneCount: 0,
        unavailableCount: 0,
        markerSeen: false,
        originalUnchanged: true,
        overflow: false,
        closing: false,
        sealed: false,
        cleanupPromise: null,
        pending: new Set(),
        cancel: new Set(),
        records: [],
      }
      let registration
      try {
        registration = await context.session.hook("http.response", (event) => {
          if (!matches(event, config.providerID, config.modelID)) return undefined
          if (state.closing || state.responseCount >= MAX_RESPONSES) {
            state.overflow = true
            return undefined
          }
          const ordinal = ++state.responseCount
          const originalResponse = event.response
          const observed = inspectResponse(originalResponse)
          const task = observed.promise.then((result) => {
            if (state.sealed) return
            const unchanged = event.response === originalResponse
            state.originalUnchanged = state.originalUnchanged && unchanged
            if (result.complete) state.cloneCount += 1
            else state.unavailableCount += 1
            state.markerSeen = state.markerSeen || (result.complete && result.marker)
            record(state, "response", result.complete ? "clone_completed" : "inspection_unavailable", ordinal, {
              selected: true,
              marker: result.complete && result.marker,
              complete: result.complete,
              unavailable: !result.complete,
              originalUnchanged: unchanged,
            })
          }).catch(() => {
            if (state.sealed) return
            state.unavailableCount += 1
            record(state, "response", "inspection_unavailable", ordinal, {
              selected: true, unavailable: true, originalUnchanged: event.response === originalResponse,
            })
          }).finally(() => {
            state.pending.delete(task)
            state.cancel.delete(observed.cancel)
          })
          state.pending.add(task)
          state.cancel.add(observed.cancel)
          return undefined
        }, { providerID: config.providerID })
        state.hookRegistered = registration !== null && typeof registration?.dispose === "function"
      } catch { state.hookRegistered = false }
      record(state, "hook", state.hookRegistered ? "registered" : "registration_unavailable", 0, {
        unavailable: !state.hookRegistered,
        originalUnchanged: true,
      })

      return () => {
        if (state.cleanupPromise) return state.cleanupPromise
        state.closing = true
        state.cleanupPromise = (async () => {
          if (state.hookRegistered) {
            state.hookDisposed = await boundedWait(
              Promise.resolve().then(() => registration.dispose()),
              MAX_INSPECTION_MS,
            )
            if (!state.hookDisposed) state.overflow = true
          }
          let pendingComplete = state.pending.size === 0
          if (!pendingComplete) {
            pendingComplete = await boundedWait(Promise.all([...state.pending]), MAX_INSPECTION_MS)
            pendingComplete = pendingComplete && state.pending.size === 0
            if (!pendingComplete) {
              state.overflow = true
              for (const cancel of state.cancel) cancel()
            }
          }
          state.closing = true
          state.sealed = true
          const cleanupComplete = state.hookDisposed && pendingComplete
          const clonesComplete = state.responseCount > 0 && state.cloneCount === state.responseCount && !state.overflow
          record(state, "seal", cleanupComplete && !state.overflow && state.unavailableCount === 0 ? "sealed" : "incomplete", state.responseCount, {
            selected: state.responseCount > 0,
            marker: state.markerSeen,
            complete: clonesComplete,
            unavailable: state.unavailableCount > 0 || state.overflow,
            originalUnchanged: state.responseCount > 0 && state.originalUnchanged,
            cleanup: cleanupComplete,
          })
          const seal = state.records.pop()
          saveEvidence(state, seal)
          return undefined
        })()
        return state.cleanupPromise
      }
    },
  }
}

const defaultProbe = {
  id: "spec025-t127-http-response-probe",
  setup(context) {
    const options = context?.options
    if (options === null || typeof options !== "object" || Array.isArray(options)) return undefined
    return createHttpResponseProbe({
      nonce: options.nonce,
      ledgerPath: options.ledgerPath,
      providerID: options.providerID,
      modelID: options.modelID,
      moduleUrl: import.meta.url,
    }).setup(context)
  },
}

export default defaultProbe

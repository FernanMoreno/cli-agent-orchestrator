import fs from "node:fs/promises"
import fsSync from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const PROVIDER_ID = "opencode-go"
const MODEL_ID = "longcat-2.5-preview-free"
const MAX_BODY_BYTES = 1024 * 1024
const MAX_RECORD_BYTES = 4096
const MAX_SEAL_BYTES = 2048
const MAX_REQUESTS = 16
const MAX_TOOLS = 256
const NONCE_RE = /^[0-9a-f]{32}$/
const KINDS = new Set(["primary", "compaction", "title", "generate"])
const TOOL_NAMES = Object.freeze([
  "cao-mcp-server_cao_ask_user",
  "cao-mcp-server_cao_wait_for_user_answers",
  "cao-mcp-server_cao_acknowledge_user_answers",
])
const STATUSES = new Set([
  "observed",
  "valid_empty",
  "model_mismatch",
  "non_primary",
  "request_kind_unknown",
  "tools_absent",
  "tools_invalid",
  "body_invalid",
  "body_oversized",
  "body_unreadable",
  "observer_error",
])

function blankRecord({ nonce, ordinal, kind, modelMatch, status }) {
  return {
    schema_version: 1,
    nonce,
    ordinal,
    kind,
    model_match: modelMatch,
    status,
    tool_count: null,
    name_counts: null,
    other_name_count: null,
  }
}

function safeKind(value) {
  return KINDS.has(value) ? value : "unknown"
}

function safeTargetModel(model) {
  return (
    model !== null &&
    typeof model === "object" &&
    model.providerID === PROVIDER_ID &&
    model.id === MODEL_ID
  )
}

async function readBoundedClone(request) {
  if (!(request instanceof Request)) throw new TypeError("request")
  const body = request.clone().body
  if (body === null) throw new TypeError("body")
  const reader = body.getReader()
  const chunks = []
  let size = 0
  try {
    while (true) {
      const part = await reader.read()
      if (part.done) break
      if (!(part.value instanceof Uint8Array)) throw new TypeError("chunk")
      size += part.value.byteLength
      if (size > MAX_BODY_BYTES) {
        void reader.cancel().catch(() => undefined)
        return { status: "body_oversized" }
      }
      chunks.push(part.value)
    }
  } finally {
    reader.releaseLock()
  }
  const bodyBytes = new Uint8Array(size)
  let offset = 0
  for (const chunk of chunks) {
    bodyBytes.set(chunk, offset)
    offset += chunk.byteLength
  }
  let document
  try {
    document = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bodyBytes))
  } catch {
    return { status: "body_invalid" }
  }
  if (document === null || typeof document !== "object" || Array.isArray(document)) {
    return { status: "body_invalid" }
  }
  if (!Object.hasOwn(document, "tools")) return { status: "tools_absent" }
  if (!Array.isArray(document.tools) || document.tools.length > MAX_TOOLS) {
    return { status: "tools_invalid" }
  }
  const nameCounts = Object.fromEntries(TOOL_NAMES.map((name) => [name, 0]))
  let otherNameCount = 0
  for (const entry of document.tools) {
    if (
      entry === null ||
      typeof entry !== "object" ||
      Array.isArray(entry) ||
      entry.type !== "function" ||
      entry.function === null ||
      typeof entry.function !== "object" ||
      Array.isArray(entry.function) ||
      typeof entry.function.name !== "string" ||
      entry.function.name.length === 0 ||
      entry.function.name.length > 256
    ) {
      return { status: "tools_invalid" }
    }
    if (Object.hasOwn(nameCounts, entry.function.name)) nameCounts[entry.function.name] += 1
    else otherNameCount += 1
  }
  return {
    status: document.tools.length === 0 ? "valid_empty" : "observed",
    tool_count: document.tools.length,
    name_counts: nameCounts,
    other_name_count: otherNameCount,
  }
}

function privateDirectory(directory) {
  const info = fsSync.lstatSync(directory)
  const uid = typeof process.getuid === "function" ? process.getuid() : -1
  return (
    info.isDirectory() &&
    !info.isSymbolicLink() &&
    info.uid === uid &&
    (info.mode & 0o077) === 0 &&
    (info.mode & 0o700) === 0o700
  )
}

async function publishRecord({ fsApi, directory, fileName, payload, maximum }) {
  let handle
  let pendingCreated = false
  let finalPublished = false
  const pendingPath = path.join(directory, `.${fileName}.pending`)
  const finalPath = path.join(directory, fileName)
  try {
    if (!privateDirectory(directory)) return false
    const encoded = Buffer.from(JSON.stringify(payload), "utf8")
    if (encoded.byteLength > maximum) return false
    handle = await fsApi.open(pendingPath, "wx", 0o600)
    pendingCreated = true
    await handle.chmod(0o600)
    await handle.writeFile(encoded)
    await handle.sync()
    await handle.close()
    handle = undefined
    await fsApi.link(pendingPath, finalPath)
    finalPublished = true
    await fsApi.unlink(pendingPath)
    await syncDirectory(fsApi, directory)
    return true
  } catch {
    if (handle !== undefined) await handle.close().catch(() => undefined)
    if (pendingCreated) await fsApi.unlink(pendingPath).catch(() => undefined)
    if (finalPublished) {
      await fsApi.unlink(finalPath).catch(() => undefined)
      await syncDirectory(fsApi, directory).catch(() => undefined)
    }
    return false
  }
}

async function syncDirectory(fsApi, directory) {
  const handle = await fsApi.open(directory, fsSync.constants.O_RDONLY)
  try {
    await handle.sync()
  } finally {
    await handle.close()
  }
}

function ledgerDirectory(moduleUrl) {
  const pluginDirectory = path.dirname(fileURLToPath(moduleUrl))
  const opencodeDirectory = path.dirname(pluginDirectory)
  return path.join(opencodeDirectory, "http-tool-offer-observation")
}

async function inspectEvent(event, { nonce, ordinal }) {
  let kind = "unknown"
  let modelMatch = false
  try {
    kind = safeKind(event?.kind)
    modelMatch = safeTargetModel(event?.model)
    if (!modelMatch) return blankRecord({ nonce, ordinal, kind, modelMatch, status: "model_mismatch" })
    if (kind === "unknown") {
      return blankRecord({ nonce, ordinal, kind, modelMatch, status: "request_kind_unknown" })
    }
    if (kind !== "primary") {
      return blankRecord({ nonce, ordinal, kind, modelMatch, status: "non_primary" })
    }
    let result
    try {
      result = await readBoundedClone(event.request)
    } catch {
      result = { status: "body_unreadable" }
    }
    if (result.status !== "observed" && result.status !== "valid_empty") {
      return blankRecord({ nonce, ordinal, kind, modelMatch, status: result.status })
    }
    return {
      ...blankRecord({ nonce, ordinal, kind, modelMatch, status: result.status }),
      tool_count: result.tool_count,
      name_counts: result.name_counts,
      other_name_count: result.other_name_count,
    }
  } catch {
    return blankRecord({ nonce, ordinal, kind, modelMatch, status: "observer_error" })
  }
}

export function createToolOfferObserver({
  nonce = process.env.CAO_OPENCODE_HTTP_TOOL_OFFER_NONCE,
  moduleUrl = import.meta.url,
  fsApi = fs,
} = {}) {
  return {
    id: "cao-opencode-http-tool-offer-observer",
    async setup(context) {
      if (typeof nonce !== "string" || !NONCE_RE.test(nonce)) return undefined
      const directory = ledgerDirectory(moduleUrl)
      const state = {
        hookAvailable: false,
        traceIntegrity: true,
        requestCount: 0,
        durableRecordCount: 0,
        overflow: false,
        pending: new Set(),
      }
      let registration
      try {
        await fsApi.mkdir(directory, { mode: 0o700 })
        if (!privateDirectory(directory)) throw new Error("private_ledger_required")
        registration = await context.session.hook(
          "http.request",
          (event) => {
            if (state.requestCount >= MAX_REQUESTS) {
              state.overflow = true
              state.traceIntegrity = false
              return undefined
            }
            const ordinal = ++state.requestCount
            const operation = (async () => {
              const record = await inspectEvent(event, { nonce, ordinal })
              const published = await publishRecord({
                fsApi,
                directory,
                fileName: `request-${String(ordinal).padStart(4, "0")}.json`,
                payload: record,
                maximum: MAX_RECORD_BYTES,
              })
              if (published) state.durableRecordCount += 1
              else state.traceIntegrity = false
            })().catch(() => {
              state.traceIntegrity = false
            })
            state.pending.add(operation)
            return operation.finally(() => state.pending.delete(operation))
          },
          { providerID: PROVIDER_ID },
        )
        state.hookAvailable = true
      } catch {
        state.traceIntegrity = false
      }
      return async () => {
        let hookDisposed = false
        if (registration !== undefined) {
          try {
            await registration.dispose()
            hookDisposed = true
          } catch {
            state.traceIntegrity = false
          }
        }
        await Promise.all([...state.pending])
        const traceComplete =
          state.hookAvailable &&
          hookDisposed &&
          state.traceIntegrity &&
          !state.overflow &&
          state.durableRecordCount === state.requestCount
        await publishRecord({
          fsApi,
          directory,
          fileName: "seal.json",
          maximum: MAX_SEAL_BYTES,
          payload: {
            schema_version: 1,
            nonce,
            hook_disposed: hookDisposed,
            trace_complete: traceComplete,
            record_count: state.requestCount,
            durable_record_count: state.durableRecordCount,
            overflow: state.overflow,
          },
        })
      }
    },
  }
}

export default createToolOfferObserver()

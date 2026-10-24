import fs from "node:fs/promises"
import fsSync from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const PROVIDER_ID = "opencode-go"
const MODEL_ID = "glm-5.3-flash"
const MAX_BODY_BYTES = 1024 * 1024
const MAX_EXPECTATION_BYTES = 64 * 1024
const MAX_RECORD_BYTES = 4096
const MAX_SEAL_BYTES = 2048
const MAX_LEDGER_BYTES = 72 * 1024
const MAX_REQUESTS = 16
const MAX_MESSAGES = 256
const MAX_JSON_DEPTH = 64
const MAX_JSON_NODES = 20000
const NONCE_RE = /^[0-9a-f]{32}$/
const RECEIPT_RE = /^CAO-TURN-RECEIPT-[0-9a-f]{32}$/
const MESSAGE_ROLES = new Set(["system", "developer", "user", "assistant", "tool"])
const EXPECTATION_FIELDS = new Set([
  "schema_version",
  "nonce",
  "phase_prompts",
  "receipt_prefix",
  "receipt_suffix",
  "last_instruction",
])
const STATUSES = new Set([
  "observed",
  "model_mismatch",
  "non_primary",
  "request_kind_unknown",
  "expectations_unavailable",
  "body_invalid",
  "body_oversized",
  "body_unreadable",
  "message_shape_unavailable",
  "receipt_envelope_unavailable",
  "observer_error",
])

function identity(info) {
  return [
    info.dev,
    info.ino,
    info.uid,
    info.gid,
    info.nlink,
    info.size,
    info.mode,
    info.mtimeMs,
    info.ctimeMs,
  ].join(":")
}

function isPrivateDirectory(directory) {
  try {
    const info = fsSync.lstatSync(directory)
    const uid = typeof process.getuid === "function" ? process.getuid() : -1
    return (
      info.isDirectory() &&
      !info.isSymbolicLink() &&
      info.uid === uid &&
      (info.mode & 0o777) === 0o700 &&
      fsSync.realpathSync(directory) === path.resolve(directory)
    )
  } catch {
    return false
  }
}

function isPrivateRegularFile(info) {
  const uid = typeof process.getuid === "function" ? process.getuid() : -1
  return (
    info.isFile() &&
    info.uid === uid &&
    info.nlink === 1 &&
    (info.mode & 0o777) === 0o600
  )
}

function parseJsonWithoutDuplicateKeys(text) {
  let offset = 0
  let nodes = 0

  function whitespace() {
    while (offset < text.length && /[\u0009\u000a\u000d\u0020]/.test(text[offset])) offset += 1
  }

  function stringToken() {
    const start = offset
    if (text[offset] !== '"') throw new Error("json_string")
    offset += 1
    while (offset < text.length) {
      const character = text[offset]
      if (character === '"') {
        offset += 1
        return JSON.parse(text.slice(start, offset))
      }
      if (character === "\\") {
        offset += 2
      } else {
        if (character.charCodeAt(0) < 0x20) throw new Error("json_control")
        offset += 1
      }
    }
    throw new Error("json_string_end")
  }

  function value(depth) {
    nodes += 1
    if (nodes > MAX_JSON_NODES || depth > MAX_JSON_DEPTH) throw new Error("json_bound")
    whitespace()
    const current = text[offset]
    if (current === "{") {
      offset += 1
      whitespace()
      const keys = new Set()
      if (text[offset] === "}") {
        offset += 1
        return
      }
      while (offset < text.length) {
        whitespace()
        const key = stringToken()
        if (keys.has(key)) throw new Error("json_duplicate_key")
        keys.add(key)
        whitespace()
        if (text[offset] !== ":") throw new Error("json_object_colon")
        offset += 1
        value(depth + 1)
        whitespace()
        if (text[offset] === "}") {
          offset += 1
          return
        }
        if (text[offset] !== ",") throw new Error("json_object_separator")
        offset += 1
      }
      throw new Error("json_object_end")
    }
    if (current === "[") {
      offset += 1
      whitespace()
      if (text[offset] === "]") {
        offset += 1
        return
      }
      while (offset < text.length) {
        value(depth + 1)
        whitespace()
        if (text[offset] === "]") {
          offset += 1
          return
        }
        if (text[offset] !== ",") throw new Error("json_array_separator")
        offset += 1
      }
      throw new Error("json_array_end")
    }
    if (current === '"') {
      stringToken()
      return
    }
    const start = offset
    while (
      offset < text.length &&
      !/[\u0009\u000a\u000d\u0020,\]}]/.test(text[offset])
    ) {
      offset += 1
    }
    if (offset === start) throw new Error("json_value")
  }

  value(0)
  whitespace()
  if (offset !== text.length) throw new Error("json_trailing_data")
  return JSON.parse(text)
}

function byteLength(value) {
  return Buffer.byteLength(value, "utf8")
}

function validateExpectations(value, nonce) {
  if (
    value === null ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.keys(value).length !== EXPECTATION_FIELDS.size ||
    Object.keys(value).some((key) => !EXPECTATION_FIELDS.has(key)) ||
    !Object.hasOwn(value, "schema_version") ||
    value.schema_version !== 1 ||
    value.nonce !== nonce ||
    !Array.isArray(value.phase_prompts) ||
    value.phase_prompts.length !== 2 ||
    typeof value.receipt_prefix !== "string" ||
    typeof value.receipt_suffix !== "string" ||
    typeof value.last_instruction !== "string"
  ) {
    return null
  }
  const [first, second] = value.phase_prompts
  if (
    typeof first !== "string" ||
    typeof second !== "string" ||
    first === second ||
    byteLength(first) === 0 ||
    byteLength(second) === 0 ||
    byteLength(first) > 8192 ||
    byteLength(second) > 8192 ||
    byteLength(value.receipt_prefix) === 0 ||
    byteLength(value.receipt_prefix) > 8192 ||
    byteLength(value.receipt_suffix) === 0 ||
    byteLength(value.receipt_suffix) > 16384 ||
    byteLength(value.last_instruction) === 0 ||
    byteLength(value.last_instruction) > 4096 ||
    !first.includes(value.last_instruction) ||
    !second.includes(value.last_instruction)
  ) {
    return null
  }
  return {
    phase_prompts: [first, second],
    receipt_prefix: value.receipt_prefix,
    receipt_suffix: value.receipt_suffix,
    last_instruction: value.last_instruction,
  }
}

async function readExpectations(fsApi, opencodeDirectory, nonce) {
  const filePath = path.join(opencodeDirectory, "http-prompt-expectations.json")
  let handle
  try {
    if (!isPrivateDirectory(path.dirname(opencodeDirectory)) || !isPrivateDirectory(opencodeDirectory)) {
      return null
    }
    const before = fsSync.lstatSync(filePath)
    if (!isPrivateRegularFile(before) || before.size > MAX_EXPECTATION_BYTES) return null
    const flags = fsSync.constants.O_RDONLY | (fsSync.constants.O_NOFOLLOW ?? 0)
    handle = await fsApi.open(filePath, flags)
    const opened = await handle.stat()
    if (!isPrivateRegularFile(opened) || identity(opened) !== identity(before)) return null
    const bytes = Buffer.alloc(MAX_EXPECTATION_BYTES + 1)
    let used = 0
    while (used < bytes.length) {
      const result = await handle.read(bytes, used, bytes.length - used, null)
      if (result.bytesRead === 0) break
      used += result.bytesRead
    }
    const afterHandle = await handle.stat()
    const afterPath = fsSync.lstatSync(filePath)
    if (
      used > MAX_EXPECTATION_BYTES ||
      used !== opened.size ||
      identity(afterHandle) !== identity(opened) ||
      identity(afterPath) !== identity(opened)
    ) {
      return null
    }
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes.subarray(0, used))
    const value = parseJsonWithoutDuplicateKeys(text)
    return validateExpectations(value, nonce)
  } catch {
    return null
  } finally {
    if (handle !== undefined) await handle.close().catch(() => undefined)
  }
}

async function readBoundedBody(request) {
  if (!(request instanceof Request)) return { status: "body_unreadable" }
  let reader
  try {
    const body = request.clone().body
    if (body === null) return { status: "body_invalid" }
    reader = body.getReader()
    const chunks = []
    let size = 0
    while (true) {
      const part = await reader.read()
      if (part.done) break
      if (!(part.value instanceof Uint8Array)) return { status: "body_unreadable" }
      size += part.value.byteLength
      if (size > MAX_BODY_BYTES) {
        void reader.cancel().catch(() => undefined)
        return { status: "body_oversized" }
      }
      chunks.push(part.value)
    }
    const bytes = new Uint8Array(size)
    let offset = 0
    for (const chunk of chunks) {
      bytes.set(chunk, offset)
      offset += chunk.byteLength
    }
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes)
    try {
      return { status: "parsed", document: parseJsonWithoutDuplicateKeys(text) }
    } catch {
      return { status: "body_invalid" }
    }
  } catch {
    return { status: "body_unreadable" }
  } finally {
    try {
      reader?.releaseLock()
    } catch {
      // The observer must never change the dispatch result.
    }
  }
}

function latestUserContent(document) {
  if (
    document === null ||
    typeof document !== "object" ||
    Array.isArray(document) ||
    !Array.isArray(document.messages) ||
    document.messages.length === 0 ||
    document.messages.length > MAX_MESSAGES
  ) {
    return null
  }
  let latest = null
  for (const message of document.messages) {
    if (message === null || typeof message !== "object" || Array.isArray(message)) return null
    if (typeof message.role !== "string" || !MESSAGE_ROLES.has(message.role)) return null
    if (message.role === "user") latest = message
  }
  if (latest === null) return null
  if (typeof latest.content === "string") return latest.content
  if (
    Array.isArray(latest.content) &&
    latest.content.length === 1 &&
    latest.content[0] !== null &&
    typeof latest.content[0] === "object" &&
    !Array.isArray(latest.content[0]) &&
    Object.keys(latest.content[0]).length === 2 &&
    latest.content[0].type === "text" &&
    typeof latest.content[0].text === "string"
  ) {
    return latest.content[0].text
  }
  return null
}

function occurrenceCount(value, needle) {
  let count = 0
  let offset = 0
  while (true) {
    const found = value.indexOf(needle, offset)
    if (found < 0) return count
    count += 1
    offset = found + needle.length
  }
}

function removeOuterMemory(value) {
  const opening = "<cao-memory>"
  const closing = "</cao-memory>"
  const openingCount = occurrenceCount(value, opening)
  const closingCount = occurrenceCount(value, closing)
  if (openingCount === 0 && closingCount === 0) return { valid: true, value }
  if (
    openingCount !== 1 ||
    closingCount !== 1 ||
    !value.startsWith(`${opening}\n`)
  ) {
    return { valid: false, value: "" }
  }
  const closeAt = value.indexOf(closing)
  if (
    closeAt <= opening.length ||
    value[closeAt - 1] !== "\n" ||
    value.slice(closeAt + closing.length, closeAt + closing.length + 2) !== "\n\n"
  ) {
    return { valid: false, value: "" }
  }
  return {
    valid: true,
    value: value.slice(closeAt + closing.length + 2),
  }
}

function comparePrompt(content, expectations) {
  const memory = removeOuterMemory(content)
  if (!memory.valid) return null
  const prompt = memory.value
  const { receipt_prefix: prefix, receipt_suffix: suffix } = expectations
  if (occurrenceCount(prompt, prefix) !== 1) return null
  const prefixAt = prompt.indexOf(prefix)
  const task = prompt.slice(0, prefixAt)
  const tokenStart = prefixAt + prefix.length
  const tokenLength = "CAO-TURN-RECEIPT-".length + 32
  const token = prompt.slice(tokenStart, tokenStart + tokenLength)
  if (
    !RECEIPT_RE.test(token) ||
    prompt.slice(tokenStart + tokenLength) !== suffix
  ) {
    return null
  }
  const phaseIndex = expectations.phase_prompts.indexOf(task)
  return {
    phase_ordinal: phaseIndex < 0 ? null : phaseIndex + 1,
    source_prompt_exact: phaseIndex >= 0,
    last_instruction_present: task.includes(expectations.last_instruction),
    receipt_envelope_shape_valid: true,
  }
}

function blankRecord(nonce, ordinal, status) {
  return {
    schema_version: 1,
    nonce,
    ordinal,
    status,
    phase_ordinal: null,
    source_prompt_exact: false,
    last_instruction_present: false,
    receipt_envelope_shape_valid: false,
  }
}

async function inspectRequest(event, { fsApi, opencodeDirectory, nonce, ordinal }) {
  try {
    const model = event?.model
    if (
      model === null ||
      typeof model !== "object" ||
      Array.isArray(model) ||
      model.providerID !== PROVIDER_ID ||
      model.id !== MODEL_ID
    ) {
      return blankRecord(nonce, ordinal, "model_mismatch")
    }
    if (event?.kind !== "primary") {
      return blankRecord(
        nonce,
        ordinal,
        ["compaction", "title", "generate"].includes(event?.kind)
          ? "non_primary"
          : "request_kind_unknown",
      )
    }
    const expectations = await readExpectations(fsApi, opencodeDirectory, nonce)
    if (expectations === null) return blankRecord(nonce, ordinal, "expectations_unavailable")
    const body = await readBoundedBody(event.request)
    if (body.status !== "parsed") return blankRecord(nonce, ordinal, body.status)
    const content = latestUserContent(body.document)
    if (content === null) return blankRecord(nonce, ordinal, "message_shape_unavailable")
    const comparison = comparePrompt(content, expectations)
    if (comparison === null) return blankRecord(nonce, ordinal, "receipt_envelope_unavailable")
    return {
      ...blankRecord(nonce, ordinal, "observed"),
      ...comparison,
    }
  } catch {
    return blankRecord(nonce, ordinal, "observer_error")
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

async function publishRecord({ fsApi, directory, fileName, payload, maximum }) {
  let handle
  let pendingCreated = false
  let finalPublished = false
  const pendingPath = path.join(directory, `.${fileName}.pending`)
  const finalPath = path.join(directory, fileName)
  try {
    if (!isPrivateDirectory(directory)) return null
    const encoded = Buffer.from(JSON.stringify(payload), "utf8")
    if (encoded.byteLength > maximum) return null
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
    return encoded.byteLength
  } catch {
    if (handle !== undefined) await handle.close().catch(() => undefined)
    if (pendingCreated) await fsApi.unlink(pendingPath).catch(() => undefined)
    if (finalPublished) {
      await fsApi.unlink(finalPath).catch(() => undefined)
      await syncDirectory(fsApi, directory).catch(() => undefined)
    }
    return null
  }
}

function observationDirectory(moduleUrl) {
  const pluginDirectory = path.dirname(fileURLToPath(moduleUrl))
  const opencodeDirectory = path.dirname(pluginDirectory)
  return {
    pluginDirectory,
    opencodeDirectory,
    directory: path.join(opencodeDirectory, "http-prompt-observation"),
  }
}

export function createHttpPromptObserver({
  nonce = process.env.CAO_OPENCODE_HTTP_PROMPT_NONCE,
  moduleUrl = import.meta.url,
  fsApi = fs,
} = {}) {
  return {
    id: "cao-opencode-http-prompt-observer",
    async setup(context) {
      if (typeof nonce !== "string" || !NONCE_RE.test(nonce)) return undefined
      const { pluginDirectory, opencodeDirectory, directory } = observationDirectory(moduleUrl)
      const state = {
        hookAvailable: false,
        traceIntegrity: true,
        requestCount: 0,
        durableRecordCount: 0,
        totalBytes: 0,
        overflow: false,
        pending: new Set(),
        directoryAvailable: false,
      }
      let registration
      try {
        if (
          !isPrivateDirectory(path.dirname(opencodeDirectory)) ||
          !isPrivateDirectory(opencodeDirectory) ||
          !isPrivateDirectory(pluginDirectory)
        ) {
          throw new Error("private_root_required")
        }
        await fsApi.mkdir(directory, { mode: 0o700 })
        if (!isPrivateDirectory(directory)) throw new Error("private_ledger_required")
        state.directoryAvailable = true
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
              const record = await inspectRequest(event, {
                fsApi,
                opencodeDirectory,
                nonce,
                ordinal,
              })
              const bytes = Buffer.byteLength(JSON.stringify(record), "utf8")
              if (state.totalBytes + bytes + MAX_SEAL_BYTES > MAX_LEDGER_BYTES) {
                state.traceIntegrity = false
                return
              }
              const published = await publishRecord({
                fsApi,
                directory,
                fileName: `request-${String(ordinal).padStart(4, "0")}.json`,
                payload: record,
                maximum: MAX_RECORD_BYTES,
              })
              if (published === null) state.traceIntegrity = false
              else {
                state.totalBytes += published
                state.durableRecordCount += 1
              }
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
        if (!state.directoryAvailable) return
        const traceComplete =
          state.hookAvailable &&
          hookDisposed &&
          state.traceIntegrity &&
          !state.overflow &&
          state.durableRecordCount === state.requestCount
        const seal = {
          schema_version: 1,
          nonce,
          hook_disposed: hookDisposed,
          trace_complete: traceComplete,
          request_count: state.requestCount,
          durable_record_count: state.durableRecordCount,
          overflow: state.overflow,
        }
        const sealBytes = Buffer.byteLength(JSON.stringify(seal), "utf8")
        if (state.totalBytes + sealBytes > MAX_LEDGER_BYTES) return
        const published = await publishRecord({
          fsApi,
          directory,
          fileName: "seal.json",
          maximum: MAX_SEAL_BYTES,
          payload: seal,
        })
        if (published !== null) state.totalBytes += published
      }
    },
  }
}

export default createHttpPromptObserver()

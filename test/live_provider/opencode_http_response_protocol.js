const DEFAULT_TOOL_NAME = "cao-mcp-server_send_message"
const MAX_BODY_BYTES = 1024 * 1024
const MAX_ARGUMENT_BYTES = 64 * 1024
const MAX_EXPECTED_BYTES = 8192
const MAX_JSON_DEPTH = 64
const MAX_JSON_NODES = 20000
const MAX_SSE_EVENTS = 4096
const MAX_TOOL_CALLS = 32
const TERMINAL_FINISH_REASONS = new Set([
  "stop",
  "length",
  "tool_calls",
  "content_filter",
  "function_call",
])
const JSON_WHITESPACE = /[\u0009\u000a\u000d\u0020]/
const JSON_DELIMITER = /[\u0009\u000a\u000d\u0020,\]}]/

class ParseFailure extends Error {
  constructor(reason) {
    super(reason)
    this.reason = reason
  }
}

function fail(reason) {
  throw new ParseFailure(reason)
}

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value)
}

function isUnicodeScalarString(value) {
  if (typeof value !== "string") return false
  for (let index = 0; index < value.length; index += 1) {
    const codeUnit = value.charCodeAt(index)
    if (codeUnit >= 0xd800 && codeUnit <= 0xdbff) {
      const next = value.charCodeAt(index + 1)
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false
      index += 1
    } else if (codeUnit >= 0xdc00 && codeUnit <= 0xdfff) {
      return false
    }
  }
  return true
}

function fixedResult({
  status,
  reason,
  targetCount,
  argumentExact = false,
  punctuationExact = false,
  expectedBytes,
  observedBytes = null,
}) {
  return {
    status,
    reason,
    target_tool_call_count: targetCount,
    argument_exact: argumentExact,
    known_punctuation_variant_exact: punctuationExact,
    expected_utf8_bytes: expectedBytes,
    observed_utf8_bytes: observedBytes,
  }
}

function unavailable(reason, expectedBytes) {
  return fixedResult({
    status: "unavailable",
    reason,
    targetCount: null,
    expectedBytes,
  })
}

function noTarget(expectedBytes) {
  return fixedResult({
    status: "no_target_tool",
    reason: "none",
    targetCount: 0,
    expectedBytes,
  })
}

function observed(expectedMessage, message, expectedBytes) {
  const observedBytes = Buffer.byteLength(message, "utf8")
  const argumentExact = message === expectedMessage
  const punctuationVariant =
    expectedMessage.endsWith(".") &&
    message === expectedMessage.slice(0, -1) + "。"
  return fixedResult({
    status: "observed",
    reason: "none",
    targetCount: 1,
    argumentExact,
    punctuationExact: punctuationVariant,
    expectedBytes,
    observedBytes,
  })
}

function parseJsonWithoutDuplicateKeys(text, reason) {
  let offset = 0
  let nodes = 0

  function whitespace() {
    while (offset < text.length && JSON_WHITESPACE.test(text[offset])) offset += 1
  }

  function stringToken() {
    const start = offset
    if (text[offset] !== '"') fail(reason)
    offset += 1
    while (offset < text.length) {
      const character = text[offset]
      if (character === '"') {
        offset += 1
        let decoded
        try {
          decoded = JSON.parse(text.slice(start, offset))
        } catch {
          fail(reason)
        }
        if (!isUnicodeScalarString(decoded)) fail(reason)
        return decoded
      }
      if (character === "\\") {
        offset += 2
      } else {
        if (character.charCodeAt(0) < 0x20) fail(reason)
        offset += 1
      }
    }
    fail(reason)
  }

  function value(depth) {
    nodes += 1
    if (nodes > MAX_JSON_NODES || depth > MAX_JSON_DEPTH) fail(reason)
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
        if (keys.has(key)) fail(reason)
        keys.add(key)
        whitespace()
        if (text[offset] !== ":") fail(reason)
        offset += 1
        value(depth + 1)
        whitespace()
        if (text[offset] === "}") {
          offset += 1
          return
        }
        if (text[offset] !== ",") fail(reason)
        offset += 1
      }
      fail(reason)
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
        if (text[offset] !== ",") fail(reason)
        offset += 1
      }
      fail(reason)
    }
    if (current === '"') {
      stringToken()
      return
    }
    const start = offset
    while (offset < text.length && !JSON_DELIMITER.test(text[offset])) offset += 1
    if (offset === start) fail(reason)
  }

  value(0)
  whitespace()
  if (offset !== text.length) fail(reason)
  try {
    return JSON.parse(text)
  } catch {
    fail(reason)
  }
}

function decodeTargetArguments(argumentsText, expectedMessage, expectedBytes) {
  if (Buffer.byteLength(argumentsText, "utf8") > MAX_ARGUMENT_BYTES) {
    return unavailable("arguments_oversized", expectedBytes)
  }

  let argumentsValue
  try {
    argumentsValue = parseJsonWithoutDuplicateKeys(argumentsText, "malformed_arguments")
  } catch (error) {
    return unavailable(
      error instanceof ParseFailure ? error.reason : "malformed_arguments",
      expectedBytes,
    )
  }

  if (!isRecord(argumentsValue)) return unavailable("malformed_arguments", expectedBytes)
  const keys = Object.keys(argumentsValue)
  if (
    keys.some((key) => key !== "message" && key !== "receiver_id") ||
    !Object.hasOwn(argumentsValue, "message") ||
    typeof argumentsValue.message !== "string" ||
    !isUnicodeScalarString(argumentsValue.message)
  ) {
    return unavailable("malformed_arguments", expectedBytes)
  }
  if (
    Object.hasOwn(argumentsValue, "receiver_id") &&
    argumentsValue.receiver_id !== null &&
    !(typeof argumentsValue.receiver_id === "string" &&
      isUnicodeScalarString(argumentsValue.receiver_id)) &&
    !(typeof argumentsValue.receiver_id === "number" &&
      Number.isInteger(argumentsValue.receiver_id) &&
      argumentsValue.receiver_id >= 10000000 &&
      argumentsValue.receiver_id <= 99999999)
  ) {
    return unavailable("malformed_arguments", expectedBytes)
  }

  return observed(expectedMessage, argumentsValue.message, expectedBytes)
}

function validateResponseCall(call, expectedToolName) {
  if (
    !isRecord(call) ||
    typeof call.id !== "string" ||
    call.id.length === 0 ||
    !isUnicodeScalarString(call.id) ||
    call.type !== "function" ||
    !isRecord(call.function) ||
    typeof call.function.name !== "string" ||
    call.function.name.length === 0 ||
    !isUnicodeScalarString(call.function.name) ||
    typeof call.function.arguments !== "string"
  ) {
    fail("malformed_response")
  }
  return {
    id: call.id,
    name: call.function.name,
    argumentsText: call.function.arguments,
    target: call.function.name === expectedToolName,
  }
}

function parseJsonResponse(bodyText, expectedMessage, expectedToolName, expectedBytes) {
  let document
  try {
    document = parseJsonWithoutDuplicateKeys(bodyText, "malformed_response")
  } catch (error) {
    return unavailable(
      error instanceof ParseFailure ? error.reason : "malformed_response",
      expectedBytes,
    )
  }

  if (!isRecord(document) || !Array.isArray(document.choices)) {
    return unavailable("malformed_response", expectedBytes)
  }
  if (document.choices.length === 0) return unavailable("incomplete_response", expectedBytes)
  if (document.choices.length !== 1) return unavailable("ambiguous_response", expectedBytes)

  const choice = document.choices[0]
  if (!isRecord(choice) || !isRecord(choice.message)) {
    return unavailable("malformed_response", expectedBytes)
  }
  if (!Object.hasOwn(choice.message, "tool_calls")) return noTarget(expectedBytes)
  if (!Array.isArray(choice.message.tool_calls)) {
    return unavailable("malformed_response", expectedBytes)
  }
  if (choice.message.tool_calls.length > MAX_TOOL_CALLS) {
    return unavailable("malformed_response", expectedBytes)
  }

  const targetCalls = []
  const callIds = new Set()
  try {
    for (const call of choice.message.tool_calls) {
      const parsedCall = validateResponseCall(call, expectedToolName)
      if (callIds.has(parsedCall.id)) fail("ambiguous_response")
      callIds.add(parsedCall.id)
      if (parsedCall.target) targetCalls.push(parsedCall.argumentsText)
    }
  } catch (error) {
    return unavailable(
      error instanceof ParseFailure ? error.reason : "malformed_response",
      expectedBytes,
    )
  }

  if (targetCalls.length > 1) return unavailable("ambiguous_response", expectedBytes)
  if (targetCalls.length === 0) return noTarget(expectedBytes)
  return decodeTargetArguments(targetCalls[0], expectedMessage, expectedBytes)
}

function parseSseEvents(text) {
  const events = []
  let dataLines = []
  let lineStart = 0
  let offset = 0
  let pendingEvent = false

  function processLine(line) {
    if (line.length === 0) {
      if (events.length >= MAX_SSE_EVENTS) fail("malformed_response")
      events.push(dataLines.length === 0 ? null : dataLines.join("\n"))
      dataLines = []
      pendingEvent = false
      return
    }
    pendingEvent = true
    if (line.startsWith(":")) return
    const separator = line.indexOf(":")
    const field = separator < 0 ? line : line.slice(0, separator)
    let value = separator < 0 ? "" : line.slice(separator + 1)
    if (value.startsWith(" ")) value = value.slice(1)
    if (field === "data") dataLines.push(value)
  }

  while (offset < text.length) {
    const character = text[offset]
    if (character !== "\r" && character !== "\n") {
      offset += 1
      continue
    }
    processLine(text.slice(lineStart, offset))
    if (character === "\r" && text[offset + 1] === "\n") offset += 1
    offset += 1
    lineStart = offset
  }
  if (lineStart < text.length) processLine(text.slice(lineStart))
  return { events, pendingEvent }
}

function parseSseResponse(bodyText, expectedMessage, expectedToolName, expectedBytes) {
  let parsedEvents
  try {
    parsedEvents = parseSseEvents(bodyText)
  } catch (error) {
    return unavailable(
      error instanceof ParseFailure ? error.reason : "malformed_response",
      expectedBytes,
    )
  }

  const calls = new Map()
  const seenIds = new Set()
  let finishSeen = false
  let doneSeen = false

  function applyToolFragment(fragment, eventIndexes) {
    if (
      !isRecord(fragment) ||
      !Object.hasOwn(fragment, "index") ||
      !Number.isInteger(fragment.index) ||
      fragment.index < 0 ||
      fragment.index >= MAX_TOOL_CALLS ||
      eventIndexes.has(fragment.index) ||
      !isRecord(fragment.function)
    ) {
      fail("malformed_response")
    }
    eventIndexes.add(fragment.index)

    const functionPart = fragment.function
    const existing = calls.get(fragment.index)
    let call = existing
    if (call === undefined) {
      if (
        typeof fragment.id !== "string" ||
        fragment.id.length === 0 ||
        !isUnicodeScalarString(fragment.id) ||
        fragment.type !== "function" ||
        typeof functionPart.name !== "string" ||
        functionPart.name.length === 0 ||
        !isUnicodeScalarString(functionPart.name)
      ) {
        fail("malformed_response")
      }
      if (seenIds.has(fragment.id)) fail("ambiguous_response")
      seenIds.add(fragment.id)
      call = {
        id: fragment.id,
        name: functionPart.name,
        target: functionPart.name === expectedToolName,
        argumentParts: [],
        argumentBytes: 0,
      }
      calls.set(fragment.index, call)
    } else {
      if (
        (Object.hasOwn(fragment, "id") && fragment.id !== call.id) ||
        (Object.hasOwn(fragment, "type") && fragment.type !== "function") ||
        (Object.hasOwn(functionPart, "name") && functionPart.name !== call.name)
      ) {
        fail("malformed_response")
      }
    }

    if (Object.hasOwn(functionPart, "name")) {
      if (
        typeof functionPart.name !== "string" ||
        !isUnicodeScalarString(functionPart.name) ||
        functionPart.name !== call.name
      ) {
        fail("malformed_response")
      }
    }
    if (Object.hasOwn(functionPart, "arguments")) {
      if (typeof functionPart.arguments !== "string") fail("malformed_response")
      if (call.target) {
        const partBytes = Buffer.byteLength(functionPart.arguments, "utf8")
        call.argumentBytes += partBytes
        if (call.argumentBytes > MAX_ARGUMENT_BYTES) fail("arguments_oversized")
        call.argumentParts.push(functionPart.arguments)
      }
    }
  }

  try {
    for (const eventData of parsedEvents.events) {
      if (eventData === null) continue
      if (doneSeen) fail("malformed_response")
      if (eventData === "[DONE]") {
        doneSeen = true
        continue
      }

      const event = parseJsonWithoutDuplicateKeys(eventData, "malformed_response")
      if (!isRecord(event) || !Array.isArray(event.choices)) fail("malformed_response")
      if (event.choices.length > 1) fail("ambiguous_response")
      if (event.choices.length === 0) continue
      if (finishSeen) fail("malformed_response")

      const choice = event.choices[0]
      if (
        !isRecord(choice) ||
        choice.index !== 0 ||
        !isRecord(choice.delta)
      ) {
        fail("malformed_response")
      }

      if (Object.hasOwn(choice.delta, "tool_calls")) {
        if (!Array.isArray(choice.delta.tool_calls)) fail("malformed_response")
        if (choice.delta.tool_calls.length > MAX_TOOL_CALLS) fail("malformed_response")
        const eventIndexes = new Set()
        for (const fragment of choice.delta.tool_calls) {
          applyToolFragment(fragment, eventIndexes)
        }
      }

      if (Object.hasOwn(choice, "finish_reason")) {
        if (choice.finish_reason === null) continue
        if (
          typeof choice.finish_reason !== "string" ||
          !TERMINAL_FINISH_REASONS.has(choice.finish_reason)
        ) {
          fail("malformed_response")
        }
        finishSeen = true
      }
    }
  } catch (error) {
    return unavailable(
      error instanceof ParseFailure ? error.reason : "malformed_response",
      expectedBytes,
    )
  }

  if (parsedEvents.pendingEvent) return unavailable("incomplete_response", expectedBytes)
  if (!doneSeen && !finishSeen) return unavailable("incomplete_response", expectedBytes)

  const targetCalls = Array.from(calls.values()).filter((call) => call.target)
  if (targetCalls.length > 1) return unavailable("ambiguous_response", expectedBytes)
  if (targetCalls.length === 0) return noTarget(expectedBytes)

  return decodeTargetArguments(
    targetCalls[0].argumentParts.join(""),
    expectedMessage,
    expectedBytes,
  )
}

export function parseResponseArguments(input = {}) {
  const request = isRecord(input) ? input : {}
  const { bodyText, contentType, expectedMessage } = request
  const expectedToolName = Object.hasOwn(request, "expectedToolName")
    ? request.expectedToolName
    : DEFAULT_TOOL_NAME
  const validExpectedMessage =
    typeof expectedMessage === "string" &&
    expectedMessage.length > 0 &&
    isUnicodeScalarString(expectedMessage)
  const expectedBytes = validExpectedMessage
    ? Buffer.byteLength(expectedMessage, "utf8")
    : null
  if (
    !validExpectedMessage ||
    expectedBytes > MAX_EXPECTED_BYTES ||
    typeof expectedToolName !== "string" ||
    expectedToolName.length === 0 ||
    !isUnicodeScalarString(expectedToolName) ||
    Buffer.byteLength(expectedToolName, "utf8") > 256
  ) {
    return unavailable("invalid_expectation", expectedBytes <= MAX_EXPECTED_BYTES ? expectedBytes : null)
  }

  if (typeof contentType !== "string" || !isUnicodeScalarString(contentType)) {
    return unavailable("unsupported_content_type", expectedBytes)
  }
  const mediaType = contentType.split(";", 1)[0].trim().toLowerCase()
  const kind = mediaType === "application/json"
    ? "json"
    : mediaType === "text/event-stream"
      ? "sse"
      : null
  if (kind === null) return unavailable("unsupported_content_type", expectedBytes)
  if (typeof bodyText !== "string") return unavailable("malformed_response", expectedBytes)
  if (Buffer.byteLength(bodyText, "utf8") > MAX_BODY_BYTES) {
    return unavailable("body_oversized", expectedBytes)
  }
  if (!isUnicodeScalarString(bodyText)) return unavailable("malformed_response", expectedBytes)

  if (kind === "json") {
    return parseJsonResponse(bodyText, expectedMessage, expectedToolName, expectedBytes)
  }
  return parseSseResponse(bodyText, expectedMessage, expectedToolName, expectedBytes)
}

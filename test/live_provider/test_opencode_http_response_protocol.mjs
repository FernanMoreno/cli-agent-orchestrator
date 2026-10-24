import assert from "node:assert/strict"
import test from "node:test"

import { parseResponseArguments } from "./opencode_http_response_protocol.js"

const TOOL = "cao-mcp-server_send_message"
const EXPECTED = "Revisa el mensaje de José 🧭."
const OUTPUT_KEYS = [
  "argument_exact",
  "expected_utf8_bytes",
  "known_punctuation_variant_exact",
  "observed_utf8_bytes",
  "reason",
  "status",
  "target_tool_call_count",
].sort()

function output(result) {
  assert.deepEqual(Object.keys(result).sort(), OUTPUT_KEYS)
  return result
}

function expectedResult({
  status,
  reason,
  count,
  exact = false,
  punctuation = false,
  expectedBytes = Buffer.byteLength(EXPECTED, "utf8"),
  observedBytes = null,
}) {
  return {
    status,
    reason,
    target_tool_call_count: count,
    argument_exact: exact,
    known_punctuation_variant_exact: punctuation,
    expected_utf8_bytes: expectedBytes,
    observed_utf8_bytes: observedBytes,
  }
}

function call(name, argumentsText, id = "private-call-id") {
  return {
    id,
    type: "function",
    function: { name, arguments: argumentsText },
  }
}

function chatResponse(calls, extra = {}) {
  return {
    choices: [
      {
        index: 0,
        message: {
          role: "assistant",
          tool_calls: calls,
        },
        finish_reason: "tool_calls",
      },
    ],
    ...extra,
  }
}

function jsonResult(calls, options = {}) {
  const { contentType = "application/json; charset=utf-8", expectedMessage = EXPECTED } = options
  return output(
    parseResponseArguments({
      bodyText: JSON.stringify(chatResponse(calls)),
      contentType,
      expectedMessage,
    }),
  )
}

function sseData(value) {
  const text = typeof value === "string" ? value : JSON.stringify(value)
  return "data: " + text + "\n\n"
}

function sseResult(events, options = {}) {
  const { expectedMessage = EXPECTED, expectedToolName = TOOL } = options
  return output(
    parseResponseArguments({
      bodyText: events.map(sseData).join(""),
      contentType: "text/event-stream; charset=utf-8",
      expectedMessage,
      expectedToolName,
    }),
  )
}

function targetStart({ index = 0, id = "private-call-id", name = TOOL, argumentsText = "" } = {}) {
  return {
    choices: [
      {
        index: 0,
        delta: {
          tool_calls: [
            {
              index,
              id,
              type: "function",
              function: { name, arguments: argumentsText },
            },
          ],
        },
        finish_reason: null,
      },
    ],
  }
}

function targetContinuation(index, argumentsText, extraFunctionFields = {}) {
  return {
    choices: [
      {
        index: 0,
        delta: {
          tool_calls: [
            {
              index,
              function: { arguments: argumentsText, ...extraFunctionFields },
            },
          ],
        },
        finish_reason: null,
      },
    ],
  }
}

function terminal(reason = "tool_calls") {
  return {
    choices: [{ index: 0, delta: {}, finish_reason: reason }],
  }
}

test("JSON Chat Completions decodes one exact target call and exports only the fixed projection", () => {
  const result = jsonResult([
    call(TOOL, JSON.stringify({ message: EXPECTED, receiver_id: "private-receiver" })),
  ])

  assert.deepEqual(
    result,
    expectedResult({
      status: "observed",
      reason: "none",
      count: 1,
      exact: true,
      observedBytes: Buffer.byteLength(EXPECTED, "utf8"),
    }),
  )
  const serialized = JSON.stringify(result)
  for (const privateValue of [EXPECTED, "private-call-id", "private-receiver"]) {
    assert.equal(serialized.includes(privateValue), false)
  }
})

test("JSON compares Unicode scalar values exactly without normalization", () => {
  const decomposed = "Cafe\u0301."
  const composed = "Café."
  const result = jsonResult([call(TOOL, JSON.stringify({ message: decomposed }))], {
    expectedMessage: composed,
  })

  assert.deepEqual(
    result,
    expectedResult({
      status: "observed",
      reason: "none",
      count: 1,
      observedBytes: Buffer.byteLength(decomposed, "utf8"),
      expectedBytes: Buffer.byteLength(composed, "utf8"),
    }),
  )
})

test("JSON separately recognizes only the known final U+002E to U+3002 variant", () => {
  const variant = EXPECTED.slice(0, -1) + "。"
  const result = jsonResult([call(TOOL, JSON.stringify({ message: variant }))])

  assert.deepEqual(
    result,
    expectedResult({
      status: "observed",
      reason: "none",
      count: 1,
      punctuation: true,
      observedBytes: Buffer.byteLength(variant, "utf8"),
    }),
  )
})

test("JSON accepts an omitted optional receiver_id and ignores unrelated tool argument values", () => {
  const result = jsonResult([
    call("unrelated_tool", "this is not JSON and is intentionally ignored", "private-unrelated-id"),
    call(TOOL, JSON.stringify({ message: EXPECTED }), "private-target-id"),
  ])

  assert.equal(result.status, "observed")
  assert.equal(result.target_tool_call_count, 1)
  assert.equal(result.argument_exact, true)

  const numericReceiver = jsonResult([
    call(TOOL, JSON.stringify({ message: EXPECTED, receiver_id: 12345678 })),
  ])
  assert.equal(numericReceiver.status, "observed")
  assert.equal(numericReceiver.argument_exact, true)
})

test("JSON reports no target only for a valid completed Chat Completions response", () => {
  const result = jsonResult([call("unrelated_tool", "opaque unrelated arguments")])

  assert.deepEqual(
    result,
    expectedResult({ status: "no_target_tool", reason: "none", count: 0 }),
  )
})

test("custom tool-name matching is exact and invalid call inputs still return the fixed shape", () => {
  const customName = "private_expected_tool_name"
  const prefixCollision = output(
    parseResponseArguments({
      bodyText: JSON.stringify(chatResponse([
        call(customName + "_extra", JSON.stringify({ message: EXPECTED })),
      ])),
      contentType: "application/json",
      expectedMessage: EXPECTED,
      expectedToolName: customName,
    }),
  )
  assert.equal(prefixCollision.status, "no_target_tool")

  const invalidInput = output(parseResponseArguments(null))
  assert.deepEqual(
    invalidInput,
    expectedResult({
      status: "unavailable",
      reason: "invalid_expectation",
      count: null,
      expectedBytes: null,
    }),
  )

  const invalidToolExpectation = output(
    parseResponseArguments({
      bodyText: "{}",
      contentType: "application/json",
      expectedMessage: EXPECTED,
      expectedToolName: null,
    }),
  )
  assert.equal(invalidToolExpectation.reason, "invalid_expectation")
  assert.equal(invalidToolExpectation.expected_utf8_bytes, Buffer.byteLength(EXPECTED, "utf8"))
})

test("JSON refuses ambiguous choices and multiple target calls", () => {
  const multipleChoices = output(
    parseResponseArguments({
      bodyText: JSON.stringify({
        choices: [
          { index: 0, message: { tool_calls: [] } },
          { index: 1, message: { tool_calls: [] } },
        ],
      }),
      contentType: "application/json",
      expectedMessage: EXPECTED,
    }),
  )
  assert.equal(multipleChoices.status, "unavailable")
  assert.equal(multipleChoices.reason, "ambiguous_response")

  const multipleTargets = jsonResult([
    call(TOOL, JSON.stringify({ message: EXPECTED }), "private-id-a"),
    call(TOOL, JSON.stringify({ message: EXPECTED }), "private-id-b"),
  ])
  assert.equal(multipleTargets.status, "unavailable")
  assert.equal(multipleTargets.reason, "ambiguous_response")
  assert.equal(multipleTargets.target_tool_call_count, null)
})

test("JSON rejects duplicate keys, malformed JSON, and unknown target argument keys", () => {
  const duplicateOuter = output(
    parseResponseArguments({
      bodyText: '{"choices":[{"message":{"tool_calls":[],"tool_calls":[]}}]}',
      contentType: "application/json",
      expectedMessage: EXPECTED,
    }),
  )
  assert.equal(duplicateOuter.reason, "malformed_response")

  const duplicateArguments = jsonResult([call(TOOL, '{"message":"first","message":"second"}')])
  assert.equal(duplicateArguments.reason, "malformed_arguments")

  const invalidArguments = jsonResult([call(TOOL, "{ definitely not JSON")])
  assert.equal(invalidArguments.reason, "malformed_arguments")

  const extraArgument = jsonResult([
    call(TOOL, JSON.stringify({ message: EXPECTED, unexpected: "private-extra" })),
  ])
  assert.equal(extraArgument.reason, "malformed_arguments")

  const invalidReceiver = jsonResult([
    call(TOOL, JSON.stringify({ message: EXPECTED, receiver_id: false })),
  ])
  assert.equal(invalidReceiver.reason, "malformed_arguments")

  for (const receiverId of [9999999, 100000000, 12345678.5, true]) {
    const invalidNumericReceiver = jsonResult([
      call(TOOL, JSON.stringify({ message: EXPECTED, receiver_id: receiverId })),
    ])
    assert.equal(invalidNumericReceiver.reason, "malformed_arguments")
  }
})

test("private target arguments and parser errors never appear in the public projection", () => {
  const privateMessage = "private-response-text-canary"
  const privateKey = "private-extra-key-canary"
  const privateId = "private-call-id-canary"
  const result = jsonResult([
    call(
      TOOL,
      JSON.stringify({ message: privateMessage, [privateKey]: "private-value-canary" }),
      privateId,
    ),
  ])

  assert.equal(result.status, "unavailable")
  assert.equal(result.reason, "malformed_arguments")
  const projection = JSON.stringify(result)
  for (const canary of [privateMessage, privateKey, privateId, "private-value-canary", TOOL]) {
    assert.equal(projection.includes(canary), false)
  }
})

test("JSON treats __proto__ as an unknown own key, not an inherited argument", () => {
  const result = jsonResult([
    call(TOOL, '{"message":"safe","__proto__":{"receiver_id":"injected"}}'),
  ])

  assert.equal(result.status, "unavailable")
  assert.equal(result.reason, "malformed_arguments")
})

test("JSON rejects duplicate tool-call IDs as ambiguous even across different tool names", () => {
  const result = jsonResult([
    call(TOOL, JSON.stringify({ message: EXPECTED }), "private-duplicate-id"),
    call("unrelated_tool", "ignored arguments", "private-duplicate-id"),
  ])

  assert.equal(result.status, "unavailable")
  assert.equal(result.reason, "ambiguous_response")
  assert.equal(result.target_tool_call_count, null)
})

test("JSON rejects non-scalar Unicode in body and target arguments", () => {
  const invalidBody = output(
    parseResponseArguments({
      bodyText: '{"choices":[{"message":{"content":"' + String.fromCharCode(0xd800) + '"}}]}',
      contentType: "application/json",
      expectedMessage: EXPECTED,
    }),
  )
  assert.equal(invalidBody.reason, "malformed_response")

  const invalidArgument = jsonResult([call(TOOL, '{"message":"\\ud800"}')])
  assert.equal(invalidArgument.reason, "malformed_arguments")
})

test("expectations and content types fail closed with distinct fixed reasons", () => {
  const invalidExpectation = output(
    parseResponseArguments({
      bodyText: "{}",
      contentType: "application/json",
      expectedMessage: "",
    }),
  )
  assert.equal(invalidExpectation.status, "unavailable")
  assert.equal(invalidExpectation.reason, "invalid_expectation")
  assert.equal(invalidExpectation.expected_utf8_bytes, null)

  const invalidUnicodeExpectation = output(
    parseResponseArguments({
      bodyText: "{}",
      contentType: "application/json",
      expectedMessage: String.fromCharCode(0xdfff),
    }),
  )
  assert.equal(invalidUnicodeExpectation.reason, "invalid_expectation")

  const oversizedExpectation = output(
    parseResponseArguments({
      bodyText: "{}",
      contentType: "application/json",
      expectedMessage: "x".repeat(8193),
    }),
  )
  assert.equal(oversizedExpectation.reason, "invalid_expectation")
  assert.equal(oversizedExpectation.expected_utf8_bytes, null)

  const nonStringBody = output(
    parseResponseArguments({
      bodyText: null,
      contentType: "application/json",
      expectedMessage: EXPECTED,
    }),
  )
  assert.equal(nonStringBody.reason, "malformed_response")

  const unsupported = output(
    parseResponseArguments({
      bodyText: "{}",
      contentType: "application/vnd.api+json",
      expectedMessage: EXPECTED,
    }),
  )
  assert.equal(unsupported.reason, "unsupported_content_type")
})

test("JSON and argument byte limits are enforced before exporting lengths", () => {
  const tooLargeBody = output(
    parseResponseArguments({
      bodyText: " ".repeat(1024 * 1024 + 1),
      contentType: "application/json",
      expectedMessage: EXPECTED,
    }),
  )
  assert.equal(tooLargeBody.reason, "body_oversized")
  assert.equal(tooLargeBody.target_tool_call_count, null)
  assert.equal(tooLargeBody.observed_utf8_bytes, null)

  const tooLargeArguments = jsonResult([
    call(TOOL, JSON.stringify({ message: "x".repeat(64 * 1024) })),
  ])
  assert.equal(tooLargeArguments.reason, "arguments_oversized")
  assert.equal(tooLargeArguments.observed_utf8_bytes, null)
})

test("outer JSON parser enforces depth and node limits", () => {
  const tooDeep = "{" + '"nested":'.repeat(66) + "0" + "}".repeat(66)
  const tooManyNodes = JSON.stringify({
    filler: Array.from({ length: 20010 }, () => 0),
    choices: [{ message: { tool_calls: [] } }],
  })
  for (const bodyText of [tooDeep, tooManyNodes]) {
    const result = output(
      parseResponseArguments({ bodyText, contentType: "application/json", expectedMessage: EXPECTED }),
    )
    assert.equal(result.status, "unavailable")
    assert.equal(result.reason, "malformed_response")
  }

  const nestedArgument =
    '{"message":"private","nested":' + "[".repeat(65) + "0" + "]".repeat(65) + "}"
  const manyArgumentNodes = JSON.stringify({
    message: "private",
    nested: Array.from({ length: 20010 }, () => 0),
  })
  for (const argumentsText of [nestedArgument, manyArgumentNodes]) {
    const result = jsonResult([call(TOOL, argumentsText)])
    assert.equal(result.status, "unavailable")
    assert.equal(result.reason, "malformed_arguments")
  }
})

test("SSE reconstructs fragmented JSON arguments with a full initial id and function name", () => {
  const argumentsText = JSON.stringify({ message: EXPECTED, receiver_id: null })
  const split = Math.floor(argumentsText.length / 2)
  const result = sseResult([
    targetStart({ argumentsText: argumentsText.slice(0, split) }),
    targetContinuation(0, argumentsText.slice(split)),
    terminal("tool_calls"),
  ])

  assert.deepEqual(
    result,
    expectedResult({
      status: "observed",
      reason: "none",
      count: 1,
      exact: true,
      observedBytes: Buffer.byteLength(EXPECTED, "utf8"),
    }),
  )
  assert.equal(JSON.stringify(result).includes("private-call-id"), false)
})

test("SSE joins escaped Unicode argument fragments without repairing or normalizing them", () => {
  const argumentsText = '{"message":"Revisa el mensaje de Jos\\u00e9 🧭."}'
  const escapeStart = argumentsText.indexOf("\\u00e9") + 3
  const result = sseResult([
    targetStart({ argumentsText: argumentsText.slice(0, escapeStart) }),
    targetContinuation(0, argumentsText.slice(escapeStart)),
    terminal(),
  ])

  assert.equal(result.status, "observed")
  assert.equal(result.argument_exact, true)
})

test("SSE accepts [DONE] as a terminal marker and recognizes the known punctuation variant", () => {
  const variant = EXPECTED.slice(0, -1) + "。"
  const result = sseResult([
    targetStart({ argumentsText: JSON.stringify({ message: variant }) }),
    "[DONE]",
  ])

  assert.equal(result.status, "observed")
  assert.equal(result.known_punctuation_variant_exact, true)
})

test("SSE permits an empty usage-only choice after a valid terminal finish reason", () => {
  const result = sseResult([
    targetStart({ argumentsText: JSON.stringify({ message: EXPECTED }) }),
    terminal("tool_calls"),
    { choices: [], usage: { completion_tokens: 1 } },
  ])

  assert.equal(result.status, "observed")
  assert.equal(result.argument_exact, true)
})

test("SSE accepts SDK-null or omitted delta on a terminal finish chunk", () => {
  const exact = sseResult([
    targetStart({ argumentsText: JSON.stringify({ message: EXPECTED }) }),
    { choices: [{ index: 0, delta: null, finish_reason: "tool_calls" }] },
  ])
  assert.equal(exact.status, "observed")
  assert.equal(exact.argument_exact, true)

  const variant = EXPECTED.slice(0, -1) + "。"
  const omittedDelta = sseResult([
    targetStart({ argumentsText: JSON.stringify({ message: variant }) }),
    { choices: [{ index: 0, finish_reason: "tool_calls" }] },
  ])
  assert.equal(omittedDelta.status, "observed")
  assert.equal(omittedDelta.known_punctuation_variant_exact, true)
})

test("SSE null delta without a terminal signal remains incomplete", () => {
  const result = sseResult([
    targetStart({ argumentsText: JSON.stringify({ message: EXPECTED }) }),
    { choices: [{ index: 0, delta: null, finish_reason: null }] },
  ])

  assert.equal(result.status, "unavailable")
  assert.equal(result.reason, "incomplete_response")
  assert.equal(result.target_tool_call_count, null)
})

test("SSE rejects typed delta values other than object, null, or omission", () => {
  for (const delta of ["invalid", [], 1, false]) {
    const result = sseResult([
      { choices: [{ index: 0, delta, finish_reason: "tool_calls" }] },
    ])
    assert.equal(result.status, "unavailable")
    assert.equal(result.reason, "malformed_response")
  }
})

test("SSE without a terminal marker is incomplete even when arguments decode", () => {
  const result = sseResult([
    targetStart({ argumentsText: JSON.stringify({ message: EXPECTED }) }),
  ])

  assert.equal(result.status, "unavailable")
  assert.equal(result.reason, "incomplete_response")
  assert.equal(result.target_tool_call_count, null)
})

test("SSE requires an event boundary after terminal data before accepting clean EOF", () => {
  const result = output(
    parseResponseArguments({
      bodyText:
        sseData(targetStart({ argumentsText: JSON.stringify({ message: EXPECTED }) })) +
        sseData(terminal()) +
        "data: {\"choices\":[]}",
      contentType: "text/event-stream",
      expectedMessage: EXPECTED,
    }),
  )

  assert.equal(result.status, "unavailable")
  assert.equal(result.reason, "incomplete_response")
})

test("SSE requires a valid terminal finish reason and no payload after terminal", () => {
  const invalidTerminal = sseResult([
    targetStart({ argumentsText: JSON.stringify({ message: EXPECTED }) }),
    terminal("not-a-finish-reason"),
  ])
  assert.equal(invalidTerminal.reason, "malformed_response")

  const lateCall = sseResult([
    terminal("tool_calls"),
    targetStart({ argumentsText: JSON.stringify({ message: EXPECTED }) }),
  ])
  assert.equal(lateCall.reason, "malformed_response")
})

test("SSE rejects missing indices, split initial names, and conflicting call identity", () => {
  const missingIndex = {
    choices: [
      {
        index: 0,
        delta: {
          tool_calls: [
            { id: "private-call-id", type: "function", function: { name: TOOL, arguments: "{}" } },
          ],
        },
      },
    ],
  }
  const noInitialName = {
    choices: [
      {
        index: 0,
        delta: {
          tool_calls: [
            { index: 0, id: "private-call-id", type: "function", function: { arguments: "{}" } },
          ],
        },
      },
    ],
  }
  const conflictingName = targetContinuation(0, "", { name: "different_tool" })
  const conflictingId = {
    choices: [
      {
        index: 0,
        delta: {
          tool_calls: [
            { index: 0, id: "different-private-id", function: { arguments: "" } },
          ],
        },
      },
    ],
  }
  const splitNameStart = targetStart({ name: "cao-mcp-server_" })
  const splitNameContinuation = targetContinuation(0, "", { name: "send_message" })
  for (const events of [
    [missingIndex, terminal()],
    [noInitialName, targetContinuation(0, "", { name: TOOL }), terminal()],
    [targetStart(), conflictingName, terminal()],
    [targetStart(), conflictingId, terminal()],
    [splitNameStart, splitNameContinuation, terminal()],
  ]) {
    const result = sseResult(events)
    assert.equal(result.status, "unavailable")
    assert.equal(result.reason, "malformed_response")
  }
})

test("SSE rejects ambiguous choices, duplicate keys, duplicate target calls, and excessive call counts", () => {
  const multipleChoices = sseResult([
    {
      choices: [
        { index: 0, delta: {}, finish_reason: null },
        { index: 1, delta: {}, finish_reason: null },
      ],
    },
    "[DONE]",
  ])
  assert.equal(multipleChoices.reason, "ambiguous_response")

  const duplicateJsonKey = sseResult([
    '{"choices":[{"index":0,"index":0,"delta":{}}]}',
    "[DONE]",
  ])
  assert.equal(duplicateJsonKey.reason, "malformed_response")

  const duplicateArgs = sseResult([
    targetStart({ argumentsText: '{"message":"one","message":"two"}' }),
    terminal(),
  ])
  assert.equal(duplicateArgs.reason, "malformed_arguments")

  const duplicateTargets = sseResult([
    targetStart({ index: 0, id: "private-id-a", argumentsText: JSON.stringify({ message: EXPECTED }) }),
    targetStart({ index: 1, id: "private-id-b", argumentsText: JSON.stringify({ message: EXPECTED }) }),
    terminal(),
  ])
  assert.equal(duplicateTargets.reason, "ambiguous_response")

  const duplicateCallIds = sseResult([
    targetStart({ index: 0, id: "private-reused-id", argumentsText: JSON.stringify({ message: EXPECTED }) }),
    targetStart({ index: 1, id: "private-reused-id", name: "unrelated_tool", argumentsText: "opaque" }),
    terminal(),
  ])
  assert.equal(duplicateCallIds.reason, "ambiguous_response")

  const tooManyCalls = sseResult([
    {
      choices: [
        {
          index: 0,
          delta: {
            tool_calls: Array.from({ length: 33 }, (_, index) => ({
              index,
              id: "private-id-" + index,
              type: "function",
              function: { name: "unrelated_tool_" + index, arguments: "opaque" },
            })),
          },
        },
      ],
    },
    "[DONE]",
  ])
  assert.equal(tooManyCalls.reason, "malformed_response")
})

test("SSE ignores unrelated argument values but still requires an unambiguous complete target call", () => {
  const result = sseResult([
    {
      choices: [
        {
          index: 0,
          delta: {
            tool_calls: [
              {
                index: 0,
                id: "private-unrelated-id",
                type: "function",
                function: { name: "unrelated_tool", arguments: "not-json" },
              },
            ],
          },
        },
      ],
    },
    targetStart({ index: 1, argumentsText: JSON.stringify({ message: EXPECTED }) }),
    terminal(),
  ])

  assert.equal(result.status, "observed")
  assert.equal(result.target_tool_call_count, 1)
})

test("SSE no-target result also requires a clean complete stream", () => {
  const complete = sseResult([
    {
      choices: [
        {
          index: 0,
          delta: {
            tool_calls: [
              {
                index: 0,
                id: "private-unrelated-id",
                type: "function",
                function: { name: "unrelated_tool", arguments: "opaque" },
              },
            ],
          },
        },
      ],
    },
    "[DONE]",
  ])
  assert.deepEqual(
    complete,
    expectedResult({ status: "no_target_tool", reason: "none", count: 0 }),
  )

  const incomplete = sseResult([])
  assert.equal(incomplete.reason, "incomplete_response")
})

test("SSE event and body bounds are enforced with fixed reasons", () => {
  const tooManyEvents = output(
    parseResponseArguments({
      bodyText: ": keepalive\n\n".repeat(4097),
      contentType: "text/event-stream",
      expectedMessage: EXPECTED,
    }),
  )
  assert.equal(tooManyEvents.reason, "malformed_response")

  const tooLargeBody = output(
    parseResponseArguments({
      bodyText: ": keepalive\n\n".repeat(100000),
      contentType: "text/event-stream",
      expectedMessage: EXPECTED,
    }),
  )
  assert.equal(tooLargeBody.reason, "body_oversized")

  const tooLargeArguments = sseResult([
    targetStart({ argumentsText: JSON.stringify({ message: "x".repeat(64 * 1024) }) }),
    terminal(),
  ])
  assert.equal(tooLargeArguments.reason, "arguments_oversized")
})

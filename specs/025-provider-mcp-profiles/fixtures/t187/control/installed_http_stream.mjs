// Local-only installed-hook control. Never contacts a provider or network.
import fs from "node:fs/promises"
import readline from "node:readline"
import { pathToFileURL } from "node:url"
const { default: plugin } = await import(pathToFileURL(process.argv[2]).href)
const hooks = new Map()
let disposed = 0
const cleanup = await plugin.setup({ session: { hook: async (name, callback) => {
  hooks.set(name, callback)
  return { dispose: async () => { disposed += 1 } }
} } })
if (typeof cleanup !== "function" || hooks.size !== 2) throw new Error("hook_setup_failed")
const request = new Request("https://example.invalid", { method: "POST", body: JSON.stringify({
  messages: [{ role: "user", content: await fs.readFile(process.argv[3], "utf8") }],
}) })
const model = { providerID: "opencode-go", id: "glm-5.3-flash" }
hooks.get("http.request")({ kind: "primary", model, request })
await request.text()
let source
const response = new Response(new ReadableStream({ start(controller) { source = controller } }), {
  headers: { "content-type": "text/event-stream" },
})
const event = { kind: "primary", model, request, response }
hooks.get("http.response")(event)
const original = response.text()
const sent = []
function frame(content, finish = null) {
  return Buffer.from(`data: ${JSON.stringify({ id: "response", model: "synthetic-wire-model", choices: [{ index: 0,
    delta: content === null ? {} : { content }, finish_reason: finish }] })}\n\n`)
}
process.stdout.write(JSON.stringify({ schema: 1, state: "ready" }) + "\n")
for await (const line of readline.createInterface({ input: process.stdin })) {
  const command = JSON.parse(line)
  if (Object.keys(command).sort().join(",") !== "operation,schema" || command.schema !== 1) throw new Error("command_invalid")
  if (command.operation === "first" || command.operation === "second") {
    const bytes = frame(command.operation)
    sent.push(bytes); source.enqueue(bytes)
  } else if (command.operation === "finish") {
    const bytes = frame(null, "stop")
    sent.push(bytes); source.enqueue(bytes); source.close()
    const actual = await original
    await cleanup()
    process.stdout.write(JSON.stringify({ schema: 1, state: "finished", original_exact: actual === Buffer.concat(sent).toString(),
      original_identity: event.response === response, disposed }) + "\n")
    break
  } else throw new Error("command_invalid")
}

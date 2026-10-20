import { expect, test, type Page, type Route } from "@playwright/test";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createInterface } from "node:readline";
import { resolve } from "node:path";

let child: ChildProcessWithoutNullStreams;
let origin: string;
const messages: Record<string, unknown>[] = [];
let startupError = "";

async function waitForMessage(
  predicate: (message: Record<string, unknown>) => boolean,
  timeoutMs = 90_000,
) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const index = messages.findIndex(predicate);
    if (index >= 0) return messages.splice(index, 1)[0];
    if (child.exitCode !== null)
      throw new Error(`Interaction browser fixture exited: ${startupError}`);
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`Interaction browser fixture timed out: ${startupError}`);
}

async function command(action: string, fields: Record<string, unknown> = {}) {
  child.stdin.write(`${JSON.stringify({ action, ...fields })}\n`);
  return waitForMessage((message) => message.ack === action);
}

function nodeAUpstreamUrl(requestUrl: string) {
  const upstream = new URL(requestUrl);
  upstream.pathname = upstream.pathname.replace(/^\/nodes\/node-a(?=\/)/, "");
  return upstream.toString();
}

async function routeFleet(page: Page) {
  await page.route("**/api/fleet", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        machines: [
          {
            name: "node-a",
            label: "Interaction acceptance",
            host: "127.0.0.1",
            role: "supervisor",
            online: true,
            sessions: [],
          },
          {
            name: "node-b",
            label: "Empty privacy-check node",
            host: "127.0.0.2",
            role: "worker",
            online: true,
            sessions: [],
          },
        ],
      }),
    });
  });

  // The node proxy is the only mocked layer. node-a strips its fleet prefix
  // and reaches the real local CAO API/PostgreSQL; interaction responses are real.
  await page.route("**/nodes/node-a/**", async (route) => {
    const response = await route.fetch({ url: nodeAUpstreamUrl(route.request().url()) });
    await route.fulfill({ response });
  });
  await page.route("**/nodes/node-b/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/nodes\/node-b/, "");
    let body: unknown = {};
    if (path === "/interactions") body = { items: [], next_cursor: null };
    else if (path === "/interaction-wait-graph") body = { nodes: [], edges: [] };
    else if (path === "/interaction-capabilities") body = { items: [] };
    else if (path === "/sessions" || path === "/agents/providers" || path === "/agents/profiles") body = [];
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
}

test.beforeAll(async () => {
  child = spawn("uv", ["run", "python", "web/e2e/interactions_runtime.py"], {
    cwd: resolve(import.meta.dirname, "../.."),
    stdio: "pipe",
  });
  child.stderr.on("data", (data) => {
    startupError = (startupError + data.toString()).slice(-5000);
  });
  createInterface({ input: child.stdout }).on("line", (line) => {
    try {
      messages.push(JSON.parse(line));
    } catch {
      startupError = (startupError + `\n${line}`).slice(-5000);
    }
  });
  const ready = await waitForMessage((message) => message.ready === true);
  origin = String(ready.origin);
});

test.afterAll(async () => {
  if (child && child.exitCode === null) {
    child.stdin.write('{"action":"stop"}\n');
    await new Promise<void>((resolve) => {
      const timeout = setTimeout(() => {
        child.kill("SIGTERM");
        resolve();
      }, 30_000);
      child.once("exit", () => {
        clearTimeout(timeout);
        resolve();
      });
    });
  }
});

test("real API inbox exercises all response kinds, replay conflict, required wait, pull and acknowledgement", async ({ page }) => {
  test.setTimeout(180_000);
  await routeFleet(page);
  const answerIdempotencyKeys: string[] = [];
  let droppedFirstAnswerReceipt = false;
  let firstAnswerStatus = 0;
  let firstAnswerId = "";
  await page.route("**/nodes/node-a/interactions/*/answers", async (route) => {
    answerIdempotencyKeys.push(route.request().headers()["idempotency-key"] ?? "");
    const response = await route.fetch({ url: nodeAUpstreamUrl(route.request().url()) });
    if (!droppedFirstAnswerReceipt) {
      droppedFirstAnswerReceipt = true;
      firstAnswerStatus = response.status();
      const receipt = await response.json() as { answer_id?: string };
      firstAnswerId = receipt.answer_id ?? "";
      await route.abort("failed");
      return;
    }
    await route.fulfill({ response });
  });
  await page.addInitScript(() => {
    const target = window as unknown as { __questionSeenAt?: Record<string, number> };
    target.__questionSeenAt = {};
    const record = () => {
      for (const button of document.querySelectorAll<HTMLButtonElement>(
        'button[aria-pressed][aria-label*="question"]',
      )) {
        const questionId = button.getAttribute("aria-label")?.match(/question ([^,]+)/i)?.[1];
        if (questionId && target.__questionSeenAt && target.__questionSeenAt[questionId] == null)
          target.__questionSeenAt[questionId] = Date.now();
      }
    };
    new MutationObserver(record).observe(document, { childList: true, subtree: true });
    document.addEventListener("DOMContentLoaded", record);
  });
  await page.goto(origin);
  const questionsTab = page.getByRole("tab", { name: "Questions" });
  await expect(questionsTab).toBeVisible();
  await questionsTab.click();
  await expect(page.getByRole("heading", { name: "Interaction inbox" })).toBeVisible();

  const seeded = await command("populate");
  const questions = seeded.questions as { question_id: string; admitted_ms: number }[];
  const scenarios = seeded.scenarios as { question_id: string; kind: string; prompt: string }[];
  expect(questions).toHaveLength(100);
  expect(scenarios.map((item) => item.kind)).toEqual([
    "text",
    "single_choice",
    "multi_choice",
    "choice_or_text",
    "approval",
  ]);
  await expect(page.getByText("100 loaded", { exact: true })).toBeVisible({ timeout: 20_000 });
  await expect(page.locator('button[aria-pressed]')).toHaveCount(100);

  const visibleAt = await page.evaluate(() =>
    (window as unknown as { __questionSeenAt?: Record<string, number> }).__questionSeenAt ?? {},
  );
  const visibleLatencyMs = questions
    .map((question) => visibleAt[question.question_id] - question.admitted_ms)
    .sort((left, right) => left - right);
  expect(visibleLatencyMs).toHaveLength(100);
  const p95Ms = visibleLatencyMs[Math.ceil(visibleLatencyMs.length * 0.95) - 1];
  console.log(`SC001 browser pickup: 20 sources, 100 committed questions, DOM visibility p95 ${p95Ms} ms`);
  expect(p95Ms).toBeGreaterThanOrEqual(0);
  expect(p95Ms).toBeLessThanOrEqual(2000);
  test.info().annotations.push({
    type: "SC001 connected browser pickup",
    description: `20 source terminals, 100 real PostgreSQL questions; DOM visibility p95 ${p95Ms} ms`,
  });

  const byKind = Object.fromEntries(scenarios.map((item) => [item.kind, item])) as Record<string, (typeof scenarios)[number]>;
  const textQuestion = byKind.text;
  const textAnswer = "Start with the contract tests.";
  const freeTextAnswer = "Ask the owner to confirm the boundary.";
  const expectedResponses = {
    [textQuestion.question_id]: { kind: "text", text: textAnswer },
    [byKind.single_choice.question_id]: { kind: "options", option_ids: ["inspect"] },
    [byKind.multi_choice.question_id]: { kind: "options", option_ids: ["tests", "diff"] },
    [byKind.choice_or_text.question_id]: { kind: "free_text", text: freeTextAnswer },
    [byKind.approval.question_id]: { kind: "options", option_ids: ["approve"] },
  };
  const mcpStarted = await command("mcp_start_wait", { expected_responses: expectedResponses });
  expect(mcpStarted.started).toBe(true);

  await page.getByRole("button", { name: new RegExp(textQuestion.question_id) }).click();
  await expect(page.getByText(textQuestion.prompt, { exact: true })).toBeVisible();
  await expect(page.getByText(/Do not enter passwords or authentication codes; use the provider terminal/i)).toBeVisible();
  await page.getByRole("textbox", { name: "Your answer" }).fill(textAnswer);
  await page.getByRole("button", { name: "Submit answer" }).click();
  await expect(page.getByText("Answer accepted", { exact: true })).toBeVisible();
  expect(droppedFirstAnswerReceipt).toBe(true);
  expect(firstAnswerStatus).toBe(201);
  expect(firstAnswerId).toBeTruthy();
  expect(answerIdempotencyKeys).toHaveLength(1);
  expect(answerIdempotencyKeys[0]).toBeTruthy();

  const replay = await page.evaluate(async ({ questionId, idempotencyKey, answer }) => {
    const response = await fetch(`/nodes/node-a/interactions/${encodeURIComponent(questionId)}/answers`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "If-Match": '"1"',
        "Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify({
        expected_version: 1,
        response: answer,
      }),
    });
    return { status: response.status, ...(await response.json()) };
  }, {
    questionId: textQuestion.question_id,
    idempotencyKey: answerIdempotencyKeys[0],
    answer: expectedResponses[textQuestion.question_id],
  });
  expect(replay.status).toBe(200);
  expect(replay.replayed).toBe(true);
  expect(replay.answer_id).toBe(firstAnswerId);
  expect(answerIdempotencyKeys).toHaveLength(2);
  expect(answerIdempotencyKeys[1]).toBe(answerIdempotencyKeys[0]);

  const conflictStatus = await page.evaluate(async ({ questionId, idempotencyKey }) => {
    const response = await fetch(`/nodes/node-a/interactions/${encodeURIComponent(questionId)}/answers`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "If-Match": '"1"',
        "Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify({
        expected_version: 1,
        response: { kind: "text", text: "A different response for the same intent." },
      }),
    });
    return response.status;
  }, { questionId: textQuestion.question_id, idempotencyKey: answerIdempotencyKeys[0] });
  expect(conflictStatus).toBe(409);
  expect(answerIdempotencyKeys).toHaveLength(3);
  expect(answerIdempotencyKeys[2]).toBe(answerIdempotencyKeys[0]);

  await page.getByRole("button", { name: new RegExp(byKind.single_choice.question_id) }).click();
  await expect(page.getByText(byKind.single_choice.prompt, { exact: true })).toBeVisible();
  await page.getByLabel("Inspect recent changes").check();
  await page.getByRole("button", { name: "Submit answer" }).click();
  await expect(page.getByText("Answer accepted", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: new RegExp(byKind.multi_choice.question_id) }).click();
  await expect(page.getByText(byKind.multi_choice.prompt, { exact: true })).toBeVisible();
  await page.getByLabel("Run contract tests").check();
  await page.getByLabel("Review the diff").check();
  await page.getByRole("button", { name: "Submit answer" }).click();
  await expect(page.getByText("Answer accepted", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: new RegExp(byKind.choice_or_text.question_id) }).click();
  await expect(page.getByText(byKind.choice_or_text.prompt, { exact: true })).toBeVisible();
  await page.getByLabel("Write another answer").check();
  await page.getByRole("textbox", { name: "Your answer" }).fill(freeTextAnswer);
  await page.getByRole("button", { name: "Submit answer" }).click();
  await expect(page.getByText("Answer accepted", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: new RegExp(byKind.approval.question_id) }).click();
  await expect(page.getByText(byKind.approval.prompt, { exact: true })).toBeVisible();
  await expect(page.getByText(
    "This response does not grant Work authorization or replace the approval required for a specific work revision.",
    { exact: true },
  )).toBeVisible();
  await page.getByLabel("Approve read-only inspection").check();
  await page.getByRole("button", { name: "Submit answer" }).click();
  await expect(page.getByText("Answer accepted", { exact: true })).toBeVisible();

  const pulled = await command("mcp_collect_result");
  expect(pulled.state).toBe("ready");
  expect(pulled.answer_count).toBe(5);
  expect(pulled.responses_match).toBe(true);
  expect(pulled.acknowledged).toBe(true);
  await expect(page.getByText(/Agent acknowledgement observed/i)).toBeVisible({ timeout: 5000 });

  await page.getByLabel("Active node").selectOption("node-b");
  await page.getByRole("tab", { name: "Questions" }).click();
  await expect(page.getByRole("heading", { name: "Interaction inbox" })).toBeVisible();
  await expect(page.getByText(textQuestion.prompt, { exact: true })).toHaveCount(0);
  await expect(page.getByText("0 loaded", { exact: true })).toBeVisible();
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("native manual notice opens the exact source terminal incarnation", async ({ page }) => {
  await routeFleet(page);
  await command("populate");
  const liveSessions = await command("list_sessions");
  expect(liveSessions.sessions).toEqual(expect.arrayContaining([
    expect.objectContaining({ id: "cao-browser-session-00" }),
  ]));
  const native = await command("seed_native");
  expect(native.lifecycle).toBe("manual_only");
  await page.addInitScript(() => {
    const NativeWebSocket = window.WebSocket;
    window.WebSocket = new Proxy(NativeWebSocket, {
      construct(target, args) {
        const socketUrl = new URL(String(args[0]), window.location.href);
        socketUrl.pathname = socketUrl.pathname.replace(/^\/nodes\/node-a(?=\/)/, "");
        return Reflect.construct(target, [socketUrl.toString(), ...args.slice(1)]);
      },
    }) as typeof WebSocket;
  });
  const socketUrls: string[] = [];
  page.on("websocket", (socket) => socketUrls.push(socket.url()));
  await page.goto(origin);
  await page.getByRole("tab", { name: "Questions" }).click();
  await expect(page.getByRole("heading", { name: "Interaction inbox" })).toBeVisible();

  const questionId = String(native.question_id);
  const sourceTerminalId = "b0000000";
  const sourceIncarnationId = "browser-incarnation-00";
  const notice = page.getByRole("button", { name: new RegExp(questionId) });
  await expect(notice).toBeVisible();
  await notice.click();
  await expect(page.getByText(/Handle this provider-owned dialog in its exact terminal/i)).toBeVisible();
  await expect(page.getByText(/Native provider dialogs require manual action in the exact terminal/i)).toBeVisible();
  expect(await page.getByRole("textbox", { name: "Your answer" }).count()).toBe(0);
  expect(await page.getByRole("button", { name: "Submit answer" }).count()).toBe(0);

  await page.getByRole("button", { name: /Open source terminal for manual handling/i }).click();
  await expect(page.getByRole("tab", { name: "Agents" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText(sourceTerminalId, { exact: true }).first()).toBeVisible();
  await expect.poll(() => socketUrls.length, { timeout: 15000 }).toBeGreaterThan(0);
  const terminalSocket = socketUrls.find((value) => value.includes(`/terminals/${sourceTerminalId}/ws`));
  expect(terminalSocket).toBeTruthy();
  const socketUrl = new URL(terminalSocket!);
  expect(socketUrl.searchParams.has("ticket")).toBe(true);
  expect(socketUrl.searchParams.get("expected_incarnation_id")).toBe(sourceIncarnationId);

  await page.getByRole("button", { name: "Close terminal", exact: true }).click();
  await page.getByRole("tab", { name: "Questions" }).click();
  const explicitCredential = await command("seed_explicit_credential");
  const credentialQuestionId = String(explicitCredential.question_id);
  const credentialNotice = page.getByRole("button", { name: new RegExp(credentialQuestionId) });
  await expect(credentialNotice).toBeVisible();
  await credentialNotice.click();
  await expect(page.getByText(/This explicit credential request is inspect-only in CAO/i)).toBeVisible();
  await expect(page.getByText(/Credential prompt hidden for privacy/i)).toBeVisible();
  await expect(page.getByText("What is your password?", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "Your answer" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Open source terminal/i })).toHaveCount(0);
  await page.getByRole("button", { name: "Cancel question" }).click();
  await page.getByRole("textbox", { name: "Cancellation reason" }).fill(
    "Use the provider’s private authentication flow instead.",
  );
  await page.getByRole("button", { name: "Confirm cancellation" }).click();
  await expect(page.getByText("Question cancelled. No answer was sent.", { exact: true })).toBeVisible();
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("Questions inbox recovers after a refresh transport failure without losing its snapshot", async ({ page }) => {
  await routeFleet(page);
  await command("populate");
  await command("seed_native");
  await page.goto(origin);
  await page.getByRole("tab", { name: "Questions" }).click();
  await expect(page.getByRole("heading", { name: "Interaction inbox" })).toBeVisible();
  const loadedCount = await page.evaluate(async () => {
    const response = await fetch(
      "/nodes/node-a/interactions?status=open&status=manual_only&limit=100",
    );
    const result = await response.json() as { items?: unknown[] };
    return result.items?.length ?? 0;
  });
  expect(loadedCount).toBeGreaterThan(0);
  const loadedLabel = `${loadedCount} loaded`;
  await expect(page.getByText(loadedLabel, { exact: true })).toBeVisible({ timeout: 15000 });
  const visibleQuestion = await page.locator('button[aria-pressed]').first().getAttribute("aria-label");

  await page.reload();
  await page.getByRole("tab", { name: "Questions" }).click();
  await expect(page.getByText(loadedLabel, { exact: true })).toBeVisible({ timeout: 15000 });
  if (visibleQuestion) await expect(page.getByRole("button", { name: new RegExp(visibleQuestion) })).toBeVisible();

  let failListRefresh = true;
  const listRoute = /\/nodes\/node-a\/interactions(?:\?.*)?$/;
  const handleListRefresh = async (route: Route) => {
    if (failListRefresh && route.request().method() === "GET") {
      await route.abort("failed");
      return;
    }
    const response = await route.fetch({ url: nodeAUpstreamUrl(route.request().url()) });
    await route.fulfill({ response });
  };
  await page.route(listRoute, handleListRefresh);
  await page.getByRole("button", { name: "Refresh questions" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Questions could not be refreshed" })).toBeVisible({ timeout: 5000 });
  await expect(page.getByText(loadedLabel, { exact: true })).toBeVisible();

  failListRefresh = false;
  await page.getByRole("button", { name: "Refresh questions" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Questions could not be refreshed" })).toHaveCount(0, { timeout: 10000 });
  await expect(page.getByText(loadedLabel, { exact: true })).toBeVisible();
  await page.unroute(listRoute, handleListRefresh);
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

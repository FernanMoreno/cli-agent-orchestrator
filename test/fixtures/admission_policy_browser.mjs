import assert from "node:assert/strict";
import { createInterface } from "node:readline";
import { readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";

const BASE_REVISION = 9007199254741993n;
const DRAFT_MEMORY = 9007199254742045n;
const DRAFT_CPU = 9007199254742047n;
const EXTERNAL_CPU = 9007199254741997n;
const HISTORY_CURSOR = BASE_REVISION - 16n;
const TOKEN_KEY = "cao.browser.bearer";
const POLICY_PATH = "/admission/policy";

let stage = "configuration";
let browser;
let context;
let page;
let cleanupPromise;
const protocol = createInterface({ input: process.stdin, crlfDelay: Infinity });
const pendingLines = [];
const pendingReaders = [];
let exactIntegerTokens = 0;
protocol.on("line", (line) => {
  const reader = pendingReaders.shift();
  if (reader) reader(line);
  else pendingLines.push(line);
});

function send(event) {
  process.stdout.write(`${event}\n`);
}

function nextCommand() {
  if (pendingLines.length > 0) return Promise.resolve(pendingLines.shift());
  return new Promise((resolve) => pendingReaders.push(resolve));
}

function hasExactJsonSupport() {
  if (typeof JSON.rawJSON !== "function") return false;
  let source;
  JSON.parse("9007199254740993", (_key, value, contextValue) => {
    if (value === 9007199254740992) source = contextValue?.source;
    return value;
  });
  return (
    source === "9007199254740993" &&
    JSON.stringify(JSON.rawJSON("9007199254740993")) === "9007199254740993"
  );
}

function revisionText(value) {
  return value.toString();
}

function requestBodyHasExactInteger(body, field, value) {
  let source;
  let parsedType;
  JSON.parse(body, (key, parsed, contextValue) => {
    if (key === field) {
      source = contextValue?.source;
      parsedType = typeof parsed;
    }
    return parsed;
  });
  assert.equal(parsedType, "number");
  assert.equal(source, value.toString());
  exactIntegerTokens += 1;
}

async function label(name) {
  return page.getByLabel(new RegExp(name));
}

async function policyReadout(name) {
  return page.getByText(name, { exact: true }).locator("xpath=..");
}

async function assertReviewedField(review, label, value, unit) {
  const field = review.getByText(label, { exact: true }).locator("xpath=..");
  assert.ok((await field.innerText()).includes(`${value} ${unit}`));
}

function cleanup() {
  if (cleanupPromise) return cleanupPromise;
  cleanupPromise = (async () => {
    protocol.close();
    const ownedContext = context;
    const ownedBrowser = browser;
    context = undefined;
    browser = undefined;
    const failures = [];
    if (ownedContext) {
      try {
        await ownedContext.close();
      } catch {
        failures.push("context");
      }
    }
    if (ownedBrowser) {
      try {
        await ownedBrowser.close();
      } catch {
        failures.push("browser");
      }
    }
    if (failures.length) throw new Error("browser cleanup failed");
  })();
  return cleanupPromise;
}

process.once("SIGTERM", () => {
  void cleanup()
    .catch(() => {})
    .finally(() => process.exit(1));
});

try {
  assert.match(process.version, /^v\d+\.\d+\.\d+$/);
  const modulePath = process.env.CAO_ADMISSION_BROWSER_PLAYWRIGHT_MODULE;
  const chromiumPath = process.env.CAO_ADMISSION_BROWSER_CHROMIUM;
  const appUrl = process.env.CAO_ADMISSION_BROWSER_APP_URL;
  const bearerPath = process.env.CAO_ADMISSION_BROWSER_BEARER_FILE;
  assert.ok(modulePath && chromiumPath && appUrl && bearerPath);
  const packageJson = JSON.parse(
    readFileSync(join(dirname(modulePath), "package.json"), "utf8"),
  );
  assert.match(packageJson.version, /^\d+\.\d+\.\d+$/);
  const bearerStat = statSync(bearerPath);
  assert.equal(bearerStat.mode & 0o777, 0o600);
  const bearer = readFileSync(bearerPath, "ascii");
  assert.ok(/^[A-Za-z0-9._~-]+$/.test(bearer));

  stage = "playwright-import";
  const { chromium, expect } = await import(pathToFileURL(modulePath).href);
  stage = "chromium-launch";
  browser = await chromium.launch({
    headless: true,
    executablePath: chromiumPath,
    timeout: 30000,
  });
  const chromiumVersion = browser.version();
  assert.match(chromiumVersion, /^\d+\.\d+\.\d+\.\d+$/);
  context = await browser.newContext({ acceptDownloads: false });
  await context.addInitScript(
    (args) => sessionStorage.setItem(args.key, args.bearer),
    { key: TOKEN_KEY, bearer },
  );
  page = await context.newPage();
  page.setDefaultTimeout(15000);

  const browserWrites = [];
  const historyRequests = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === POLICY_PATH && request.method() === "PUT") {
      browserWrites.push({
        method: request.method(),
        body: request.postData() ?? "",
      });
    }
    if (
      url.pathname === `${POLICY_PATH}/history` &&
      request.method() === "GET"
    ) {
      historyRequests.push(url);
    }
  });

  stage = "exact-json-feature-check";
  const supported = await page.evaluate(hasExactJsonSupport);
  assert.equal(supported, true);

  stage = "load-built-app";
  const appResponse = await page.goto(appUrl, {
    waitUntil: "domcontentloaded",
    timeout: 30000,
  });
  assert.equal(appResponse?.status(), 200);
  const settingsTab = page.getByRole("tab", { name: "Settings" });
  await settingsTab.waitFor({ state: "visible" });
  await settingsTab.click();
  const reviewButton = page.getByRole("button", { name: "Revisar valores" });
  await reviewButton.waitFor({ state: "visible" });
  await expect(reviewButton).toBeEnabled();
  await page.getByText("Perfil local", { exact: true }).waitFor();
  assert.ok((await page.getByText("This server", { exact: true }).count()) > 0);
  const persistedReadout = await policyReadout("Revisión persistida");
  await persistedReadout.waitFor({ state: "visible" });
  await expect(persistedReadout).toContainText(revisionText(BASE_REVISION));
  const appliedReadout = await policyReadout("Revisión aplicada");
  await expect(appliedReadout).toContainText("Desconocida");

  stage = "read-exact-integers";
  assert.equal(
    await (await label("Memoria estimada por entrega")).inputValue(),
    revisionText(BASE_REVISION + 1n),
  );
  assert.equal(
    await (await label("CPU estimada por entrega")).inputValue(),
    revisionText(BASE_REVISION + 2n),
  );

  stage = "review-conflicting-draft";
  const memoryInput = await label("Memoria estimada por entrega");
  const cpuInput = await label("CPU estimada por entrega");
  await memoryInput.fill(revisionText(DRAFT_MEMORY));
  await cpuInput.fill(revisionText(DRAFT_CPU));
  await reviewButton.click();
  const review = page.getByRole("region", {
    name: "Revisión antes de guardar",
  });
  await review.waitFor({ state: "visible" });
  assert.ok(
    (await review.innerText()).includes(
      `revisión esperada ${revisionText(BASE_REVISION)}`,
    ),
  );
  const reviewFields = [
    ["Memoria estimada por entrega", DRAFT_MEMORY, "bytes"],
    ["CPU estimada por entrega", DRAFT_CPU, "millicores"],
    ["Reserva de memoria", 20n, "%"],
    ["Reserva de CPU", 20n, "%"],
    ["Antigüedad máxima de una muestra", 3, "segundos"],
    ["Reserva de conexiones PostgreSQL", 20n, "%"],
    ["Tiempo límite de sentencia PostgreSQL", 500n, "milisegundos"],
    ["Intervalo de muestreo", 1, "segundos"],
    ["Muestras sanas antes de aumentar capacidad", 3n, "muestras"],
  ];
  for (const [fieldLabel, value, unit] of reviewFields) {
    await assertReviewedField(review, fieldLabel, value, unit);
  }
  assert.equal(browserWrites.length, 0);
  send("READY_CONFLICT");
  assert.equal(await nextCommand(), "continue");

  stage = "conflict-with-external-writer";
  const confirmReview = review.getByRole("button", {
    name: "Confirmar y guardar",
  });
  const conflictResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname === POLICY_PATH && response.request().method() === "PUT"
    );
  });
  await confirmReview.click();
  const conflictResponse = await conflictResponsePromise;
  const conflictStatus = conflictResponse.status();
  assert.equal(conflictStatus, 409);
  await page
    .getByRole("alert")
    .filter({ hasText: "La política cambió desde la revisión mostrada" })
    .waitFor({ state: "visible" });
  assert.equal(browserWrites.length, 1);
  requestBodyHasExactInteger(
    browserWrites[0].body,
    "expected_revision",
    BASE_REVISION,
  );
  requestBodyHasExactInteger(
    browserWrites[0].body,
    "memory_per_delivery_bytes",
    DRAFT_MEMORY,
  );
  requestBodyHasExactInteger(
    browserWrites[0].body,
    "cpu_per_delivery_millicores",
    DRAFT_CPU,
  );
  assert.equal(await memoryInput.inputValue(), revisionText(DRAFT_MEMORY));
  assert.equal(await cpuInput.inputValue(), revisionText(DRAFT_CPU));
  const reloadButton = page.getByRole("button", {
    name: "Cargar política actual",
  });
  await reloadButton.waitFor({ state: "visible" });
  await expect(reloadButton).toBeEnabled();

  stage = "explicit-conflict-inspection";
  const reloadResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname === POLICY_PATH && response.request().method() === "GET"
    );
  });
  await reloadButton.click();
  assert.equal((await reloadResponsePromise).status(), 200);
  await expect(reviewButton).toBeEnabled();
  await expect(persistedReadout).toContainText(
    revisionText(BASE_REVISION + 1n),
  );
  assert.equal(await memoryInput.inputValue(), revisionText(DRAFT_MEMORY));
  assert.equal(await cpuInput.inputValue(), revisionText(DRAFT_CPU));
  await reviewButton.click();
  const refreshedReview = page.getByRole("region", {
    name: "Revisión antes de guardar",
  });
  await refreshedReview.waitFor({ state: "visible" });
  assert.ok(
    (await refreshedReview.innerText()).includes(
      `revisión esperada ${revisionText(BASE_REVISION + 1n)}`,
    ),
  );

  stage = "exact-ui-put";
  const savedResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname === POLICY_PATH && response.request().method() === "PUT"
    );
  });
  await refreshedReview
    .getByRole("button", { name: "Confirmar y guardar" })
    .click();
  const savedStatus = (await savedResponsePromise).status();
  assert.equal(savedStatus, 200);
  await page.getByText(/Guardado confirmado:/).waitFor({ state: "visible" });
  assert.equal(browserWrites.length, 2);
  requestBodyHasExactInteger(
    browserWrites[1].body,
    "expected_revision",
    BASE_REVISION + 1n,
  );
  requestBodyHasExactInteger(
    browserWrites[1].body,
    "memory_per_delivery_bytes",
    DRAFT_MEMORY,
  );
  requestBodyHasExactInteger(
    browserWrites[1].body,
    "cpu_per_delivery_millicores",
    DRAFT_CPU,
  );
  await expect(persistedReadout).toContainText(
    revisionText(BASE_REVISION + 2n),
  );
  await expect(appliedReadout).toContainText("Desconocida");

  stage = "confirmed-revocation";
  await memoryInput.fill(revisionText(DRAFT_MEMORY + 1n));
  const revokeButton = page.getByRole("button", { name: "Revocar política" });
  await expect(revokeButton).toBeDisabled();
  await page.getByRole("button", { name: "Descartar borrador" }).click();
  await expect(revokeButton).toBeEnabled();
  await revokeButton.click();
  await page.getByRole("heading", { name: "Confirmar revocación" }).waitFor();
  const revokeResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname === `${POLICY_PATH}/revoke` &&
      response.request().method() === "POST"
    );
  });
  await page.getByRole("button", { name: "Confirmar revocación" }).click();
  const revokeStatus = (await revokeResponsePromise).status();
  assert.equal(revokeStatus, 200);
  await page.getByText(/Revocación confirmada/).waitFor({ state: "visible" });
  await expect(persistedReadout).toContainText(
    revisionText(BASE_REVISION + 3n),
  );
  await expect(appliedReadout).toContainText("Desconocida");

  stage = "large-history-cursor";
  await page.getByRole("button", { name: "Cargar historial" }).click();
  await page
    .getByText(`Revisión ${revisionText(BASE_REVISION + 3n)} · revocada`, {
      exact: true,
    })
    .waitFor({ state: "visible" });
  const earlier = page.getByRole("button", {
    name: "Cargar revisiones anteriores",
  });
  await earlier.waitFor({ state: "visible" });
  await earlier.click();
  await page
    .getByText(`Revisión ${revisionText(HISTORY_CURSOR - 1n)} · activa`, {
      exact: true,
    })
    .waitFor({ state: "visible" });
  const cursorRequest = historyRequests.find((url) =>
    url.searchParams.has("before_revision"),
  );
  assert.ok(cursorRequest);
  const observedCursor = cursorRequest.searchParams.get("before_revision");
  assert.equal(observedCursor, revisionText(HISTORY_CURSOR));
  const appliedRevisionUnknown = (await appliedReadout.innerText()).includes(
    "Desconocida",
  );
  assert.equal(appliedRevisionUnknown, true);

  stage = "complete";
  const receipt = {
    node: process.version,
    playwright: packageJson.version,
    chromium: chromiumVersion,
    exactJsonSupport: supported,
    browserPutRequests: browserWrites.length,
    exactIntegerTokens,
    conflictStatus,
    saveStatus: savedStatus,
    revokeStatus,
    appliedRevisionUnknown,
    historyCursor: observedCursor,
    historyCursorExact: observedCursor === revisionText(HISTORY_CURSOR),
  };
  await cleanup();
  send(`RESULT:${JSON.stringify(receipt)}`);
} catch {
  send(`FAIL:${stage}`);
  await nextCommand();
  process.exitCode = 1;
} finally {
  await cleanup();
}

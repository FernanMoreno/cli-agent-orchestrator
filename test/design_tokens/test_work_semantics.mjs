import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "../..");
const fixture = JSON.parse(
  readFileSync(resolve(REPO, "test/fixtures/work_contract_v1.json"), "utf8"),
);

function expectedWorkSemantics() {
  const presentation = fixture.presentation_expectations;
  assert.ok(
    presentation,
    "the common fixture must declare top-level Work presentation expectations",
  );
  assert.ok(
    Array.isArray(presentation.work_states),
    "the common fixture must enumerate Work presentation states",
  );
  return presentation.work_states.map((entry) => ({
    state: entry.state,
    label: entry.label,
    semanticRole: entry.semantic_role,
    observedRunningCount: entry.running_count,
    observedSucceededCount: entry.succeeded_count,
  }));
}

function parseWebSemantics(source) {
  return Array.from(
    source.matchAll(
      /\{ state: "([^"]+)", label: "([^"]+)", semanticRole: "([^"]+)", observedRunningCount: ([01]), observedSucceededCount: ([01]) \},/g,
    ),
    ([, state, label, semanticRole, running, succeeded]) => ({
      state,
      label,
      semanticRole,
      observedRunningCount: Number(running),
      observedSucceededCount: Number(succeeded),
    }),
  );
}

function parseTuiSemantics(source) {
  return Array.from(
    source.matchAll(
      /WorkStatusSemantics\s*\{\s*state:\s*"([^"]+)",\s*label:\s*"([^"]+)",\s*semantic_role:\s*WorkSemanticRole::(\w+),\s*observed_running_count:\s*([01]),\s*observed_succeeded_count:\s*([01]),?\s*\},/g,
    ),
    ([, state, label, semanticRole, running, succeeded]) => ({
      state,
      label,
      semanticRole: semanticRole.replace(/^[A-Z]/, (letter) => letter.toLowerCase()),
      observedRunningCount: Number(running),
      observedSucceededCount: Number(succeeded),
    }),
  );
}

test("generated Web and TUI Work semantics exactly match the common fixture", () => {
  const expected = expectedWorkSemantics();
  const web = readFileSync(resolve(REPO, "web/src/work-status.generated.ts"), "utf8");
  const tui = readFileSync(resolve(REPO, "tui/src/work_status_generated.rs"), "utf8");

  assert.deepEqual(parseWebSemantics(web), expected);
  assert.deepEqual(parseTuiSemantics(tui), expected);
});

test("fixture keeps WorkView v1 bodies wire-only while presentation remains top-level", () => {
  const presentation = fixture.presentation_expectations;
  const allowedWorkViewFields = [
    "attempt_id",
    "attempt_state",
    "cleanup_state",
    "job_id",
    "job_state",
    "process_state",
    "required_action",
    "result_ref",
    "revision",
    "turn_state",
    "work_item_id",
    "work_state",
  ];

  assert.equal(fixture.schema_version, 1);
  for (const view of fixture.views) {
    assert.deepEqual(Object.keys(view).sort(), allowedWorkViewFields);
  }
  assert.ok(
    presentation,
    "presentation expectations must remain outside HTTP WorkView bodies",
  );
  assert.equal(presentation.schema_version, 1);
  assert.deepEqual(
    presentation.unknown_selection,
    {
      no_selection: "unknown",
      load_error: "unknown",
      invalid_schema_version: "unknown",
    },
  );
});

import { act, fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { AgentView } from "../agent/AgentView";
import type { AgentDetailSnapshot } from "../shared/types";

it("keeps a newer observed generation when an older recovery action returns late", async () => {
  const snapshot: AgentDetailSnapshot = {
    terminal_id: "abcd1234",
    session_name: "cao-x",
    provider: "codex",
    agent_profile: "dev",
    status: "reconcile",
    last_active: null,
    output_tail: "",
    scopes: ["cao:read", "cao:write"],
    turn: {
      terminal_id: "abcd1234",
      generation: "a".repeat(32),
      state: "reconcile",
      allowed_actions: ["verify", "cancel"],
      attempts: 3,
    },
  };
  let resolveAction!: (result: unknown) => void;
  let observeSnapshot!: (result: unknown) => void;
  const submitCommand = vi.fn(
    () =>
      new Promise((resolve) => {
        resolveAction = resolve;
      }),
  );
  const app = {
    onToolResult: vi.fn((callback) => {
      observeSnapshot = callback;
    }),
    connect: vi.fn().mockResolvedValue(undefined),
    startPolling: vi.fn().mockReturnValue(vi.fn()),
    submitCommand,
    silentlyNoteToModel: vi.fn(),
  };
  render(<AgentView initialSnapshot={snapshot} app={app as any} />);
  fireEvent.click(screen.getByRole("button", { name: "Verify turn" }));
  await vi.waitFor(() => expect(submitCommand).toHaveBeenCalledTimes(1));
  act(() =>
    observeSnapshot({
      ...snapshot,
      turn: {
        ...snapshot.turn!,
        generation: "c".repeat(32),
        reason: "newer_snapshot",
      },
    }),
  );
  expect(screen.getByTestId("agent-turn-state").textContent).toContain(
    "newer_snapshot",
  );
  await act(async () =>
    resolveAction({
      success: false,
      error: "stale generation",
      turn: {
        ...snapshot.turn!,
        generation: "b".repeat(32),
        reason: "late_old_response",
      },
    }),
  );
  expect(screen.getByTestId("agent-turn-state").textContent).toContain(
    "newer_snapshot",
  );
  expect(screen.getByTestId("agent-turn-state").textContent).not.toContain(
    "late_old_response",
  );
  expect(submitCommand).toHaveBeenCalledTimes(1);
});

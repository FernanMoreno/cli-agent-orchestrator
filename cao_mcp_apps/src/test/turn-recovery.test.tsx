import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AgentView } from "../agent/AgentView";
import { TaskControl } from "../shared/TaskControl";

const generation = "a".repeat(32);
const snapshot = {
  terminal_id: "abcd1234",
  session_name: "cao-x",
  provider: "codex",
  agent_profile: "dev",
  status: "processing",
  last_active: null,
  output_tail: "",
  scopes: ["cao:read", "cao:write"],
  turn: {
    terminal_id: "abcd1234",
    generation,
    state: "reconcile",
    allowed_actions: ["verify", "cancel"],
    attempts: 3,
  },
  output_error: {
    kind: "internal_error",
    http_status: 500,
    message: "Capture unavailable",
  },
};

describe("receipt-bound Apps controls", () => {
  it("shows reconciliation and capture failure separately", () => {
    render(<AgentView initialSnapshot={snapshot} />);
    expect(screen.getByTestId("agent-turn-state").textContent).toContain(
      "reconcile",
    );
    expect(screen.getByTestId("agent-output-error").textContent).toContain(
      "Capture unavailable",
    );
    expect(screen.getByRole("button", { name: "Verify turn" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel turn" })).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Assign" }).hasAttribute("disabled"),
    ).toBe(true);
  });
  it("does not offer recovery without write authority", () => {
    render(
      <AgentView initialSnapshot={{ ...snapshot, scopes: ["cao:read"] }} />,
    );
    expect(screen.queryByRole("button", { name: "Verify turn" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Cancel turn" })).toBeNull();
  });
  it("sends the selected generation through the command channel", async () => {
    const submitCommand = vi.fn().mockResolvedValue({
      success: false,
      turn: {
        ...snapshot.turn,
        generation: "b".repeat(32),
        reason: "stale_generation",
      },
    });
    const app = {
      onToolResult: vi.fn(),
      connect: vi.fn().mockResolvedValue(undefined),
      startPolling: vi.fn().mockReturnValue(vi.fn()),
      submitCommand,
      silentlyNoteToModel: vi.fn(),
    };
    render(<AgentView initialSnapshot={snapshot} app={app as any} />);
    fireEvent.click(screen.getByRole("button", { name: "Verify turn" }));
    await vi.waitFor(() =>
      expect(submitCommand).toHaveBeenCalledWith("verify_turn", {
        terminal_id: "abcd1234",
        generation,
      }),
    );
    await vi.waitFor(() =>
      expect(screen.getByTestId("agent-turn-state").textContent).toContain(
        "stale_generation",
      ),
    );
    expect(submitCommand).toHaveBeenCalledTimes(1);
  });
  it("does not present unsupported pause or resume and respects an explicit empty scope set", () => {
    const { rerender } = render(
      <TaskControl onSubmit={vi.fn()} scopes={["cao:write"]} />,
    );
    expect(screen.queryByRole("button", { name: "Pause" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Resume" })).toBeNull();
    rerender(<TaskControl onSubmit={vi.fn()} scopes={[]} />);
    expect(screen.queryByRole("button", { name: "Assign" })).toBeNull();
  });
});

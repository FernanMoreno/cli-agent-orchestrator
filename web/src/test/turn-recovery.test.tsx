import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
} from "@testing-library/react";
import { afterEach, it, expect, vi } from "vitest";
import { TurnRecoveryPanel } from "../components/TurnRecoveryPanel";
import { api } from "../api";
vi.mock("../api", () => ({
  api: {
    getTerminalTurn: vi.fn(),
    verifyTerminalTurn: vi.fn(),
    cancelTerminalTurn: vi.fn(),
  },
}));
const turn = {
  terminal_id: "term",
  provider: "codex",
  generation: "a".repeat(32),
  state: "reconcile" as const,
  reason: "receipt_missing",
  attempts: 3,
  allowed_actions: ["verify", "cancel"] as ("verify" | "cancel")[],
};
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
it("shows diagnosis and verifies the displayed generation", async () => {
  vi.mocked(api.getTerminalTurn).mockResolvedValue(turn);
  vi.mocked(api.verifyTerminalTurn).mockResolvedValue({
    ...turn,
    state: "verified",
    allowed_actions: [],
  });
  render(<TurnRecoveryPanel terminalId="term" />);
  expect(await screen.findByText("receipt_missing")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Verify turn" }));
  await waitFor(() =>
    expect(api.verifyTerminalTurn).toHaveBeenCalledWith(
      "term",
      turn.generation,
    ),
  );
  expect(await screen.findByText("Turn: verified")).toBeTruthy();
});
it("refreshes stale generation without replaying the action", async () => {
  vi.mocked(api.getTerminalTurn)
    .mockResolvedValueOnce(turn)
    .mockResolvedValue({ ...turn, generation: "b".repeat(32) });
  vi.mocked(api.cancelTerminalTurn).mockRejectedValue(
    Object.assign(new Error("Conflict"), { status: 409 }),
  );
  render(<TurnRecoveryPanel terminalId="term" />);
  fireEvent.click(await screen.findByRole("button", { name: "Cancel turn" }));
  expect(await screen.findByText(/Turn changed/)).toBeTruthy();
  expect(api.cancelTerminalTurn).toHaveBeenCalledTimes(1);
});
it("offers no actions for a restricted turn", async () => {
  vi.mocked(api.getTerminalTurn).mockResolvedValue({
    ...turn,
    allowed_actions: [],
  });
  render(<TurnRecoveryPanel terminalId="term" />);
  await screen.findByText("receipt_missing");
  expect(screen.queryByRole("button")).toBeNull();
});
it("shows permission errors and removes actions when ownership disallows recovery", async () => {
  vi.mocked(api.getTerminalTurn)
    .mockResolvedValueOnce(turn)
    .mockResolvedValue({ ...turn, allowed_actions: [] });
  vi.mocked(api.verifyTerminalTurn).mockRejectedValue(
    Object.assign(new Error("Forbidden"), {
      status: 403,
      detail: "Work-owned terminal",
    }),
  );
  render(<TurnRecoveryPanel terminalId="term" />);
  fireEvent.click(await screen.findByRole("button", { name: "Verify turn" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Work-owned terminal",
  );
  await waitFor(() => expect(screen.queryByRole("button")).toBeNull());
});

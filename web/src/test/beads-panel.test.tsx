import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { BeadsPanel } from "../components/BeadsPanel";
import { useStore } from "../store";
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useStore.setState({ navLockCount: 0 });
});
it("shows optional unavailable capability without attempting writes", async () => {
  vi.spyOn(api, "beadsCapabilities").mockResolvedValue({
    enabled: false,
    available: false,
    error_kind: "beads_disabled",
    workspaces: [],
  });
  const write = vi.spyOn(api, "mutateBead");
  render(<BeadsPanel />);
  expect(await screen.findByText("beads_disabled")).toBeVisible();
  expect(write).not.toHaveBeenCalled();
});
it("retains uncertain mutation key and prevents double submission", async () => {
  vi.spyOn(api, "beadsCapabilities").mockResolvedValue({
    enabled: true,
    available: true,
    workspaces: [{ id: "repo", revision: "r" }],
  });
  vi.spyOn(api, "listBeads").mockResolvedValue({ tasks: [] });
  const write = vi
    .spyOn(api, "mutateBead")
    .mockResolvedValue({ operation_id: "b1", state: "uncertain" });
  render(<BeadsPanel />);
  await screen.findByRole("button", { name: "Create task" });
  fireEvent.change(screen.getByLabelText("Title"), {
    target: { value: "one" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create task" }));
  await screen.findByText("uncertain");
  expect(write).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Create task" })).toBeDisabled();
  await waitFor(() => expect(useStore.getState().navLockCount).toBe(0));
});
it("shows verified Work run and terminal independently of external task state", async () => {
  vi.spyOn(api, "beadsCapabilities").mockResolvedValue({
    enabled: true,
    available: true,
    workspaces: [{ id: "repo", revision: "r" }],
  });
  vi.spyOn(api, "listBeads").mockResolvedValue({
    tasks: [
      {
        id: "t1",
        title: "Accepted task",
        priority: 2,
        external_status: "open",
        material_hash: "a".repeat(64),
        work_verified_completed: true,
        work_assignment: {
          binding_id: "b1",
          run_id: "approved-run",
          state: "completed",
        },
        work_attempts: [
          {
            id: "a1",
            terminal_id: "terminal-1",
            state: "finished",
            generation: 1,
            step_id: "iteration",
            run_generation: 1,
          },
        ],
      },
    ],
  });
  render(<BeadsPanel />);
  fireEvent.click(
    await screen.findByRole("button", {
      name: "Accepted task · open · verified",
    }),
  );
  expect(
    await screen.findByText("Work run: approved-run · completed"),
  ).toBeVisible();
  expect(screen.getByRole("list", { name: "Work attempts" })).toHaveTextContent(
    "iteration · finished · terminal-1",
  );
  expect(
    screen.getByText("External status: open. Work completion: verified."),
  ).toBeVisible();
});

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, setActiveNode } from "../api";
import { useStore } from "../store";
import { BeadsWorkControl } from "../components/BeadsWorkControl";
const task = {
  id: "task-1",
  title: "Exact task",
  external_status: "open",
  material_hash: "a".repeat(64),
} as any;
const assignment = {
  binding_id: "binding-1",
  workspace_id: "repo",
  task_id: "task-1",
  prepared_id: "prepared-1",
  plan_id: "plan-1",
  run_id: null,
  state: "prepared",
  revision: 1,
  work_verified_completed: false,
} as any;
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  setActiveNode(null);
  useStore.setState({ activeNode: null, navLockCount: 0 });
});
async function prepare() {
  fireEvent.change(screen.getByLabelText("Coordinator workflow"), {
    target: { value: "wf" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Prepare task plan" }));
  await screen.findByText("Assignment: binding-1");
}
async function approve() {
  fireEvent.click(screen.getByRole("button", { name: "Review task plan" }));
  await screen.findByRole("button", { name: "Approve task plan" });
  fireEvent.click(screen.getByRole("button", { name: "Approve task plan" }));
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Start approved task" }),
    ).toBeEnabled(),
  );
}
function mount() {
  const view = render(<BeadsWorkControl workspace="repo" task={task} />);
  fireEvent.click(screen.getByText("Approved Work assignment"));
  return view;
}
function mocks(expires = Date.now() / 1000 + 60) {
  vi.spyOn(api, "prepareBeadAssignment").mockResolvedValue(assignment);
  vi.spyOn(api, "reviewWorkflowPlan").mockResolvedValue({
    plan_id: "plan-1",
    expires_at: expires,
    public_plan: { review: "exact" },
  } as any);
  vi.spyOn(api, "approveWorkflowPlan").mockResolvedValue({
    plan_id: "plan-1",
    approved: true,
  } as any);
  vi.spyOn(api, "startBeadAssignment").mockResolvedValue({
    ...assignment,
    run_id: "bead_binding-1",
    state: "assigned",
  });
}
it("local invalid JSON remains editable and never sends preparation", async () => {
  mocks();
  mount();
  fireEvent.change(screen.getByLabelText("Coordinator workflow"), {
    target: { value: "wf" },
  });
  fireEvent.change(screen.getByLabelText("Completion criteria"), {
    target: { value: "not JSON" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Prepare task plan" }));
  await screen.findByRole("alert");
  expect(api.prepareBeadAssignment).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Completion criteria")).toBeEnabled();
  expect(screen.getByLabelText("Coordinator workflow")).toBeEnabled();
});
it("expiry after approval removes usable start without another render or HTTP attempt", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-07T12:00:00Z"));
  try {
    mocks(Date.now() / 1000 + 0.3);
    mount();
    await act(async () => {
      fireEvent.change(screen.getByLabelText("Coordinator workflow"), {
        target: { value: "wf" },
      });
      fireEvent.click(
        screen.getByRole("button", { name: "Prepare task plan" }),
      );
    });
    expect(screen.getByText("Assignment: binding-1")).toBeVisible();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Review task plan" }));
    });
    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Approve task plan" }),
      );
    });
    expect(
      screen.getByRole("button", { name: "Start approved task" }),
    ).toBeEnabled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });
    expect(
      screen.getByRole("button", { name: "Start approved task" }),
    ).toBeDisabled();
    fireEvent.click(
      screen.getByRole("button", { name: "Start approved task" }),
    );
    expect(api.startBeadAssignment).not.toHaveBeenCalled();
  } finally {
    vi.useRealTimers();
  }
});
it("lost start response retries identical run and releases owned navigation lock", async () => {
  mocks();
  vi.mocked(api.startBeadAssignment).mockRejectedValueOnce(
    new TypeError("response lost"),
  );
  mount();
  await prepare();
  await approve();
  fireEvent.click(screen.getByRole("button", { name: "Start approved task" }));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Retry same task run" }));
  await waitFor(() => expect(api.startBeadAssignment).toHaveBeenCalledTimes(2));
  expect(vi.mocked(api.startBeadAssignment).mock.calls[0]).toEqual(
    vi.mocked(api.startBeadAssignment).mock.calls[1],
  );
  await waitFor(() => expect(useStore.getState().navLockCount).toBe(0));
});
it("unmount releases one lock and suppresses late same-task publication", async () => {
  mocks();
  let resolve!: (v: any) => void;
  vi.mocked(api.prepareBeadAssignment).mockReturnValue(
    new Promise((r) => (resolve = r)),
  );
  const view = mount();
  fireEvent.change(screen.getByLabelText("Coordinator workflow"), {
    target: { value: "wf" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Prepare task plan" }));
  expect(useStore.getState().navLockCount).toBe(1);
  view.unmount();
  expect(useStore.getState().navLockCount).toBe(0);
  useStore.getState().acquireNavLock();
  await act(async () => resolve(assignment));
  expect(useStore.getState().navLockCount).toBe(1);
});
it("saved assignment belonging to another workspace is never reviewable", async () => {
  mocks();
  vi.spyOn(api, "inspectBeadAssignment").mockResolvedValue({
    ...assignment,
    workspace_id: "other",
  });
  mount();
  fireEvent.change(screen.getByLabelText("Saved assignment"), {
    target: { value: "binding-1" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Inspect saved assignment" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "another task or workspace",
  );
  expect(screen.queryByRole("button", { name: "Review task plan" })).toBeNull();
});
it("lost preparation retries the exact stable operation key and frozen material", async () => {
  mocks();
  vi.mocked(api.prepareBeadAssignment).mockRejectedValueOnce(
    new TypeError("lost preparation"),
  );
  mount();
  fireEvent.change(screen.getByLabelText("Coordinator workflow"), {
    target: { value: "wf" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Prepare task plan" }));
  await screen.findByRole("alert");
  expect(screen.getByLabelText("Coordinator workflow")).toBeDisabled();
  fireEvent.click(
    screen.getByRole("button", { name: "Retry same preparation" }),
  );
  await screen.findByText("Assignment: binding-1");
  expect(vi.mocked(api.prepareBeadAssignment).mock.calls[0]).toEqual(
    vi.mocked(api.prepareBeadAssignment).mock.calls[1],
  );
});
it("node epoch change suppresses a pending preparation even when transport ignores abort", async () => {
  mocks();
  let resolve!: (v: any) => void;
  vi.mocked(api.prepareBeadAssignment).mockReturnValue(
    new Promise((r) => (resolve = r)),
  );
  mount();
  fireEvent.change(screen.getByLabelText("Coordinator workflow"), {
    target: { value: "wf" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Prepare task plan" }));
  act(() => setActiveNode("other-node"));
  await act(async () => resolve(assignment));
  expect(screen.queryByText("Assignment: binding-1")).toBeNull();
  expect(useStore.getState().navLockCount).toBe(0);
});
it("definitive expired-plan refusal revokes prior approval and disables further start", async () => {
  mocks();
  vi.mocked(api.startBeadAssignment).mockRejectedValue({
    status: 409,
    kind: "prepared_plan_expired",
    message: "expired",
    detail: { kind: "prepared_plan_expired" },
  });
  mount();
  await prepare();
  await approve();
  fireEvent.click(screen.getByRole("button", { name: "Start approved task" }));
  await screen.findByRole("alert");
  const start = screen.queryByRole("button", { name: "Start approved task" });
  if (start) expect(start).toBeDisabled();
  else expect(screen.queryByText("Assignment: binding-1")).toBeNull();
});
it("lost preparation followed by definitive refusal releases editing for a new plan", async () => {
  mocks();
  vi.mocked(api.prepareBeadAssignment)
    .mockRejectedValueOnce(new TypeError("lost preparation"))
    .mockRejectedValueOnce({ status: 409, message: "task material changed" });
  mount();
  fireEvent.change(screen.getByLabelText("Coordinator workflow"), {
    target: { value: "wf" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Prepare task plan" }));
  await screen.findByRole("alert");
  fireEvent.click(
    screen.getByRole("button", { name: "Retry same preparation" }),
  );
  await waitFor(() =>
    expect(api.prepareBeadAssignment).toHaveBeenCalledTimes(2),
  );
  await screen.findByText("task material changed");
  expect(screen.getByLabelText("Coordinator workflow")).toBeEnabled();
});
it("bulk metadata response loss retains one complete immutable draft and operation key", async () => {
  const { BeadsBulkPlanner } = await import("../components/BeadsBulkPlanner");
  vi.spyOn(api, "decomposeBeads").mockResolvedValue({
    tasks: [{ title: "first task" }, { title: "second task" }],
    draft_hash: "b".repeat(64),
  } as any);
  const write = vi
    .spyOn(api, "bulkCreateBeads")
    .mockRejectedValueOnce(new TypeError("lost bulk response"))
    .mockResolvedValue({ operation_id: "bulk-1", state: "applied" });
  render(<BeadsBulkPlanner workspace="repo" />);
  fireEvent.click(screen.getByText("Plan tasks and epic"));
  fireEvent.change(screen.getByLabelText("Task draft"), {
    target: { value: "first task\nsecond task" },
  });
  fireEvent.change(screen.getByLabelText("Epic title"), {
    target: { value: "Exact epic" },
  });
  fireEvent.click(screen.getByLabelText("Sequential dependencies"));
  fireEvent.click(screen.getByRole("button", { name: "Preview tasks" }));
  await screen.findByRole("button", { name: "Create reviewed tasks" });
  fireEvent.click(
    screen.getByRole("button", { name: "Create reviewed tasks" }),
  );
  await screen.findByRole("alert");
  expect(screen.getByLabelText("Task draft")).toBeDisabled();
  fireEvent.click(
    screen.getByRole("button", { name: "Retry same draft operation" }),
  );
  await waitFor(() => expect(write).toHaveBeenCalledTimes(2));
  expect(write.mock.calls[0]).toEqual(write.mock.calls[1]);
  expect(write.mock.calls[0][1]).toMatchObject({
    epic: { title: "Exact epic" },
    sequential: true,
    tasks: [{ title: "first task" }, { title: "second task" }],
  });
  await waitFor(() => expect(useStore.getState().navLockCount).toBe(0));
});
it("assembled fleet node change drops saved prepared task assignment with identical ids", async () => {
  const { BeadsPanel } = await import("../components/BeadsPanel");
  mocks();
  vi.spyOn(api, "beadsCapabilities").mockResolvedValue({
    enabled: true,
    available: true,
    workspaces: [{ id: "repo", revision: "same" }],
  } as any);
  vi.spyOn(api, "listBeads").mockResolvedValue({ tasks: [task] } as any);
  render(<BeadsPanel />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Exact task · open" }),
  );
  fireEvent.click(screen.getByText("Approved Work assignment"));
  await prepare();
  expect(screen.getByText("Assignment: binding-1")).toBeVisible();
  act(() => useStore.getState().selectNode("new-node"));
  await screen.findByRole("button", { name: "Exact task · open" });
  expect(screen.queryByText("Assignment: binding-1")).toBeNull();
});

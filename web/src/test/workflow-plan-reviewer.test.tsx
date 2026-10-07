import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, setActiveNode } from "../api";
import { WorkflowPlanReview } from "../components/workflow/WorkflowPlanReview";
import { useStore } from "../store";

const prepared = () => ({
  prepared_id: "a".repeat(32),
  plan_id: `plan-v2:${"b".repeat(64)}`,
  source_hash: "c".repeat(64),
  expires_at: Date.now() / 1000 + 3600,
  public_plan: { targets: ["checkout"] },
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  setActiveNode(null);
  useStore.setState({ navLockCount: 0 });
});

it.each(["prepare", "approve", "submit"])(
  "sends %s JSON with an explicit content type through selected node",
  async (operation) => {
    const fetch = vi.fn().mockResolvedValue(new Response("{}"));
    vi.stubGlobal("fetch", fetch);
    setActiveNode("review-node");
    const plan = prepared();
    if (operation === "prepare")
      await api.prepareWorkflowPlan({
        name_or_path: "review",
        inputs: {},
        target_mappings: {},
        binding_selections: {},
      });
    if (operation === "approve") await api.approveWorkflowPlan(plan.plan_id);
    if (operation === "submit")
      await api.submitPreparedWorkflow({
        name_or_path: "review",
        inputs: {},
        prepared_id: plan.prepared_id,
        expected_plan_id: plan.plan_id,
        run_id: `run_${plan.prepared_id}`,
      });
    const [url, options] = fetch.mock.calls[0];
    expect(url).toContain("/nodes/review-node/workflows/");
    expect(new Headers(options.headers).get("Content-Type")).toBe(
      "application/json",
    );
  },
);

async function openPrepared() {
  const plan = prepared();
  vi.spyOn(api, "prepareWorkflowPlan").mockResolvedValue(plan);
  vi.spyOn(api, "approveWorkflowPlan").mockResolvedValue({
    plan_id: plan.plan_id,
    approved: true,
  });
  useStore.setState({ navLockCount: 0 });
  const view = render(<WorkflowPlanReview />);
  fireEvent.change(screen.getByLabelText("Workflow"), {
    target: { value: "review" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Prepare plan" }));
  await screen.findByText(plan.plan_id);
  return { plan, view };
}
async function approve() {
  fireEvent.click(screen.getByRole("button", { name: "Approve this plan" }));
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Start approved run" }),
    ).toBeEnabled(),
  );
}

it("retries a lost submission response with the same stable run identity", async () => {
  const { plan } = await openPrepared();
  vi.spyOn(api, "submitPreparedWorkflow")
    .mockRejectedValueOnce(new TypeError("lost response"))
    .mockResolvedValueOnce({
      run_id: `run_${plan.prepared_id}`,
      state: "running",
    });
  await approve();
  fireEvent.click(screen.getByRole("button", { name: "Start approved run" }));
  await screen.findByText("lost response");
  fireEvent.click(screen.getByRole("button", { name: "Start approved run" }));
  await screen.findByText(`Run submitted: run_${plan.prepared_id}`);
  expect(
    vi
      .mocked(api.submitPreparedWorkflow)
      .mock.calls.map(([request]) => request.run_id),
  ).toEqual([`run_${plan.prepared_id}`, `run_${plan.prepared_id}`]);
});

it("a saved public review may be approved but cannot start without prepared material", async () => {
  const plan = prepared();
  vi.spyOn(api, "reviewWorkflowPlan").mockResolvedValue(plan);
  vi.spyOn(api, "approveWorkflowPlan").mockResolvedValue({
    plan_id: plan.plan_id,
    approved: true,
  });
  const submit = vi.spyOn(api, "submitPreparedWorkflow");
  render(<WorkflowPlanReview />);
  fireEvent.change(screen.getByLabelText("Prepared plan ID"), {
    target: { value: plan.prepared_id },
  });
  fireEvent.click(screen.getByRole("button", { name: "Review saved plan" }));
  await screen.findByText(plan.plan_id);
  fireEvent.click(screen.getByRole("button", { name: "Approve this plan" }));
  await screen.findByText("Plan approved");
  expect(
    screen.getByRole("button", { name: "Start approved run" }),
  ).toBeDisabled();
  expect(submit).not.toHaveBeenCalled();
});

it("double start creates one request and unmount releases its lock exactly once", async () => {
  const { plan, view } = await openPrepared();
  await approve();
  let finish!: (value: any) => void;
  vi.spyOn(api, "submitPreparedWorkflow").mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const button = screen.getByRole("button", { name: "Start approved run" });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(api.submitPreparedWorkflow).toHaveBeenCalledTimes(1);
  expect(useStore.getState().navLockCount).toBe(1);
  view.unmount();
  expect(useStore.getState().navLockCount).toBe(0);
  finish({ run_id: `run_${plan.prepared_id}`, state: "running" });
  await Promise.resolve();
  expect(useStore.getState().navLockCount).toBe(0);
});

it("does not submit an approved plan that expires while the form is idle", async () => {
  const { plan } = await openPrepared();
  await approve();
  const submit = vi
    .spyOn(api, "submitPreparedWorkflow")
    .mockResolvedValue({ run_id: "unexpected", state: "running" });
  vi.spyOn(Date, "now").mockReturnValue((plan.expires_at + 1) * 1000);
  fireEvent.click(screen.getByRole("button", { name: "Start approved run" }));
  await Promise.resolve();
  expect(submit).not.toHaveBeenCalled();
});

it("drops delayed approval from a previous node authority", async () => {
  const { plan } = await openPrepared();
  let finish!: (value: any) => void;
  vi.mocked(api.approveWorkflowPlan).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Approve this plan" }));
  setActiveNode("replacement");
  finish({ plan_id: plan.plan_id, approved: true });
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Approve this plan" }),
    ).toBeEnabled(),
  );
  expect(
    screen.getByRole("button", { name: "Start approved run" }),
  ).toBeDisabled();
});

it("does not send administrator approval after idle expiration", async () => {
  const { plan } = await openPrepared();
  vi.spyOn(Date, "now").mockReturnValue((plan.expires_at + 1) * 1000);
  fireEvent.click(screen.getByRole("button", { name: "Approve this plan" }));
  await Promise.resolve();
  expect(api.approveWorkflowPlan).not.toHaveBeenCalled();
});

it("server-declared expiration is actionable and revokes the start affordance", async () => {
  await openPrepared();
  await approve();
  vi.spyOn(api, "submitPreparedWorkflow").mockRejectedValue({
    status: 409,
    kind: "prepared_plan_expired",
    message: "409 Conflict",
  });
  fireEvent.click(screen.getByRole("button", { name: "Start approved run" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/expired/i);
  expect(
    screen.getByRole("button", { name: "Start approved run" }),
  ).toBeDisabled();
});

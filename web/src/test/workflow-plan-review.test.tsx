import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, setActiveNode } from "../api";
import { WorkflowPlanReview } from "../components/workflow/WorkflowPlanReview";
import { useStore } from "../store";
const prepared = {
  prepared_id: "a".repeat(32),
  plan_id: `plan-v2:${"b".repeat(64)}`,
  source_hash: "c".repeat(64),
  expires_at: Date.now() / 1000 + 3600,
  public_plan: { targets: ["checkout"] },
};
beforeEach(() => {
  vi.spyOn(api, "prepareWorkflowPlan").mockResolvedValue(prepared);
  vi.spyOn(api, "approveWorkflowPlan").mockResolvedValue({
    plan_id: prepared.plan_id,
    approved: true,
  });
  vi.spyOn(api, "submitPreparedWorkflow").mockResolvedValue({
    run_id: "run_1",
    state: "running",
  });
  useStore.setState({ navLockCount: 0 });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  setActiveNode(null);
});
async function prepare() {
  fireEvent.change(screen.getByLabelText("Workflow"), {
    target: { value: "review" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Prepare plan" }));
  await screen.findByText(prepared.plan_id);
}
it("requires explicit reviewed approval before submitting exact prepared identity", async () => {
  render(<WorkflowPlanReview />);
  await prepare();
  expect(
    screen.getByRole("button", { name: "Start approved run" }),
  ).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Approve this plan" }));
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Start approved run" }),
    ).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole("button", { name: "Start approved run" }));
  await waitFor(() =>
    expect(api.submitPreparedWorkflow).toHaveBeenCalledWith({
      name_or_path: "review",
      inputs: {},
      prepared_id: prepared.prepared_id,
      expected_plan_id: prepared.plan_id,
      run_id: `run_${prepared.prepared_id}`,
    }),
  );
  expect(api.submitPreparedWorkflow).toHaveBeenCalledTimes(1);
});
it("editing material invalidates the previous preparation and approval", async () => {
  render(<WorkflowPlanReview />);
  await prepare();
  fireEvent.change(screen.getByLabelText("Inputs (JSON)"), {
    target: { value: '{"topic":"changed"}' },
  });
  expect(screen.queryByText(prepared.plan_id)).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Start approved run" }),
  ).toBeNull();
  expect(api.submitPreparedWorkflow).not.toHaveBeenCalled();
});
it("rejected administrator approval never opens start", async () => {
  vi.mocked(api.approveWorkflowPlan).mockRejectedValue({
    status: 403,
    detail: "Administrator approval required",
  });
  render(<WorkflowPlanReview />);
  await prepare();
  fireEvent.click(screen.getByRole("button", { name: "Approve this plan" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Administrator approval required",
  );
  expect(
    screen.getByRole("button", { name: "Start approved run" }),
  ).toBeDisabled();
  expect(api.submitPreparedWorkflow).not.toHaveBeenCalled();
});
it("holds navigation while preparing and releases it on unmount without stale publication", async () => {
  let finish!: (value: typeof prepared) => void;
  vi.mocked(api.prepareWorkflowPlan).mockReturnValue(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  const view = render(<WorkflowPlanReview />);
  fireEvent.change(screen.getByLabelText("Workflow"), {
    target: { value: "review" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Prepare plan" }));
  expect(useStore.getState().navLockCount).toBe(1);
  view.unmount();
  expect(useStore.getState().navLockCount).toBe(0);
  finish(prepared);
  await Promise.resolve();
  expect(useStore.getState().navLockCount).toBe(0);
  expect(api.approveWorkflowPlan).not.toHaveBeenCalled();
});

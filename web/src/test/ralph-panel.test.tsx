import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { vi, test, expect } from "vitest";
import { RalphPanel } from "../components/RalphPanel";
vi.mock("../ralphApi", () => ({
  ralphApi: {
    status: vi.fn(async () => ({
      coordinator_id: "loop",
      run_id: "run",
      state: "stopping",
      iteration: 1,
      min_iterations: 1,
      max_iterations: 3,
      driver_state: "stopping",
      pause_reason: "Work stop proof required",
      work_verified_completed: false,
      events: [],
    })),
    feedback: vi.fn(),
    stop: vi.fn(),
    complete: vi.fn(),
    resume: vi.fn(),
    prepare: vi.fn(),
    start: vi.fn(),
    template: vi.fn(),
  },
}));
test("shows uncertain stopping without claiming verified completion", async () => {
  render(<RalphPanel />);
  fireEvent.change(screen.getByLabelText("Run or coordinator ID"), {
    target: { value: "loop" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));
  await waitFor(() => expect(screen.getByText("stopping")).toBeInTheDocument());
  expect(screen.getByText("Work stop proof required")).toBeInTheDocument();
  expect(screen.queryByText("Verified completed")).not.toBeInTheDocument();
});

import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { it, afterEach, expect, vi } from "vitest";
import { OutputViewer } from "../components/OutputViewer";
import { api } from "../api";
vi.mock("../api", () => ({ api: { getTerminalOutput: vi.fn() } }));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
it("shows pending diagnosis and keeps FULL transcript accessible", async () => {
  vi.mocked(api.getTerminalOutput).mockRejectedValueOnce(
    Object.assign(new Error("Pending"), {
      status: 202,
      detailMeta: { state: "pending", reason: "receipt_pending" },
    }),
  );
  vi.mocked(api.getTerminalOutput).mockResolvedValueOnce({
    mode: "full",
    output: "transcript",
  });
  render(<OutputViewer terminalId="term" onClose={() => {}} />);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "pending: receipt_pending",
  );
  fireEvent.click(screen.getByRole("button", { name: "Full Output" }));
  expect(await screen.findByText("transcript")).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
});

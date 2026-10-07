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
import { BeadsPanel } from "../components/BeadsPanel";
import { useStore } from "../store";
const cap = {
  enabled: true,
  available: true,
  workspaces: [{ id: "repo", revision: "same" }],
};
function deferred() {
  let resolve!: (value: any) => void;
  const promise = new Promise<any>((r) => (resolve = r));
  return { promise, resolve };
}
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  setActiveNode(null);
  useStore.setState({ activeNode: null, navLockCount: 0 });
});
it("drops previous fleet node same-workspace list even when mock ignores abort", async () => {
  const old = deferred();
  useStore.getState().selectNode("first");
  vi.spyOn(api, "beadsCapabilities").mockResolvedValue(cap);
  vi.spyOn(api, "listBeads")
    .mockReturnValueOnce(old.promise)
    .mockResolvedValue({
      tasks: [
        { id: "same-id", title: "new node task", external_status: "open" },
      ],
    } as any);
  render(<BeadsPanel />);
  await waitFor(() => expect(api.listBeads).toHaveBeenCalledTimes(1));
  act(() => useStore.getState().selectNode("second"));
  expect(
    await screen.findByRole("button", { name: "new node task · open" }),
  ).toBeVisible();
  await act(async () =>
    old.resolve({
      tasks: [
        { id: "same-id", title: "old node task", external_status: "closed" },
      ],
    }),
  );
  expect(screen.queryByText(/old node task/)).toBeNull();
});
it("uncertain retry retains exact material/key and releases navigation ownership", async () => {
  vi.spyOn(api, "beadsCapabilities").mockResolvedValue(cap);
  vi.spyOn(api, "listBeads").mockResolvedValue({ tasks: [] });
  const write = vi
    .spyOn(api, "mutateBead")
    .mockResolvedValue({ operation_id: "receipt", state: "uncertain" });
  render(<BeadsPanel />);
  await screen.findByRole("button", { name: "Create task" });
  fireEvent.change(screen.getByLabelText("Title"), {
    target: { value: "exact task" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create task" }));
  await screen.findByText("uncertain");
  fireEvent.click(screen.getByRole("button", { name: "Retry same operation" }));
  await waitFor(() => expect(write).toHaveBeenCalledTimes(2));
  expect(write.mock.calls[0][1]).toEqual(write.mock.calls[1][1]);
  await waitFor(() => expect(useStore.getState().navLockCount).toBe(0));
});
it("unmount releases its busy lock once and late completion preserves another lock", async () => {
  vi.spyOn(api, "beadsCapabilities").mockResolvedValue(cap);
  vi.spyOn(api, "listBeads").mockResolvedValue({ tasks: [] });
  const pending = deferred();
  vi.spyOn(api, "mutateBead").mockReturnValue(pending.promise);
  const view = render(<BeadsPanel />);
  await screen.findByRole("button", { name: "Create task" });
  fireEvent.change(screen.getByLabelText("Title"), {
    target: { value: "exact task" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create task" }));
  expect(useStore.getState().navLockCount).toBe(1);
  view.unmount();
  expect(useStore.getState().navLockCount).toBe(0);
  useStore.getState().acquireNavLock();
  await act(async () =>
    pending.resolve({ operation_id: "receipt", state: "applied" }),
  );
  expect(useStore.getState().navLockCount).toBe(1);
});

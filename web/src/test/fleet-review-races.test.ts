import { afterEach, expect, it, vi } from "vitest";
import { api, setActiveNode } from "../api";

afterEach(() => {
  setActiveNode(null);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("drops a late error-body reply after selection changes", async () => {
  let finish!: (body: unknown) => void;
  let bodyStarted!: () => void;
  const started = new Promise<void>((resolve) => {
    bodyStarted = resolve;
  });
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      statusText: "Conflict",
      json: () => {
        bodyStarted();
        return new Promise((resolve) => {
          finish = resolve;
        });
      },
    }),
  );
  setActiveNode("first");
  const pending = api.listSessions();
  const rejection = expect(pending).rejects.toThrow("Node selection changed");
  await started;
  setActiveNode("second");
  finish({ detail: "old node failed" });
  await rejection;
});

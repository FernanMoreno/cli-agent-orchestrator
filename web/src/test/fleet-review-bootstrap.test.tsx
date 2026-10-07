import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import App from "../App";
import { setActiveNode } from "../api";
import { useStore } from "../store";
vi.mock("../components/AgentPanel", () => ({ AgentPanel: () => null }));
vi.mock("../components/ProfilesPanel", () => ({ ProfilesPanel: () => null }));
vi.mock("../components/DashboardHome", () => ({
  DashboardHome: () => <div>Protected dashboard</div>,
}));
afterEach(() => {
  cleanup();
  setActiveNode(null);
  useStore.setState({ activeNode: null, fleetNodes: [], nodeEpoch: 0 });
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

it("discovers fleet after recovering initial authentication bootstrap outage", async () => {
  let configReads = 0;
  const fetch = vi.fn(async (url: string) => {
    if (url === "/auth/config") {
      if (++configReads === 1) throw new TypeError("initial outage");
      return new Response(
        JSON.stringify({ mode: "disabled", fleet_panel: true }),
      );
    }
    if (url === "/api/fleet")
      return new Response(
        JSON.stringify({
          machines: [{ name: "worker", online: true, sessions: [] }],
        }),
      );
    return new Response(JSON.stringify({ enabled: false }));
  });
  vi.stubGlobal("fetch", fetch);
  render(<App />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Reintentar conexión" }),
  );
  await screen.findByText("Protected dashboard");
  expect(fetch.mock.calls.some(([url]) => url === "/api/fleet")).toBe(true);
  expect(useStore.getState().activeNode).toBe("worker");
});

it.each([403, 503])(
  "keeps dashboard closed when fleet discovery returns %s",
  async (status) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async (url: string) =>
          new Response(
            JSON.stringify(
              url === "/auth/config"
                ? { mode: "disabled", fleet_panel: true }
                : { detail: "fleet unavailable" },
            ),
            { status: url === "/auth/config" ? 200 : status },
          ),
      ),
    );
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent(String(status));
    expect(screen.queryByText("Protected dashboard")).toBeNull();
    expect(useStore.getState().activeNode).toBeNull();
  },
);

it("keeps dashboard closed until definitive standalone 404 arrives", async () => {
  let finish!: (response: Response) => void;
  let requested!: () => void;
  const started = new Promise<void>((resolve) => {
    requested = resolve;
  });
  const reply = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url === "/auth/config")
        return new Response(JSON.stringify({ mode: "disabled" }));
      if (url === "/api/fleet") {
        requested();
        return reply.then((response) => response.clone());
      }
      return new Response(JSON.stringify({ enabled: false }));
    }),
  );
  render(<App />);
  await started;
  expect(screen.queryByText("Protected dashboard")).toBeNull();
  finish(new Response("{}", { status: 404 }));
  expect(await screen.findByText("Protected dashboard")).toBeInTheDocument();
  expect(useStore.getState().activeNode).toBeNull();
});

import { describe, expect, it } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import type { InboxMessage, SessionDetail } from "../api";
import { buildExecutionMap, type ExecutionMap } from "../components/execution/executionMap";
import { ExecutionMapPanel } from "../components/execution/ExecutionMapPanel";

const session: SessionDetail = {
  session: { id: "s1", name: "cao-s1", status: "active" },
  terminals: [
    { id: "aaaa1111", tmux_session: "cao-s1", tmux_window: "w0", provider: "claude_code", agent_profile: "supervisor", created_at: null, last_active: null },
    { id: "bbbb2222", tmux_session: "cao-s1", tmux_window: "w1", provider: "codex", agent_profile: "developer", created_at: null, last_active: null },
  ].map((t, i) => ({ ...t, status: i === 0 ? "idle" : "processing" })),
};

const msg = (id: string, from: string, to: string): InboxMessage => ({
  id, sender_id: from, receiver_id: to, message: "SECRET PROMPT TEXT", status: "delivered", created_at: null,
});

describe("buildExecutionMap", () => {
  it("projects agents, dedupes messages, hides unknown endpoints and drops content", () => {
    const map = buildExecutionMap({
      sessions: { ok: true, value: [session] },
      inbox: {
        bbbb2222: { ok: true, value: [msg("1", "aaaa1111", "bbbb2222"), msg("1", "aaaa1111", "bbbb2222"), msg("2", "zzzz9999", "bbbb2222")] },
        aaaa1111: { ok: true, value: [] },
      },
      waitGraph: { ok: true, value: { nodes: [], edges: [] } },
    });
    expect(map.nodes.map((n) => n.id)).toEqual(["aaaa1111", "bbbb2222"]);
    expect(map.edges).toHaveLength(1);
    expect(map.edges[0]).toMatchObject({ from: "aaaa1111", to: "bbbb2222", type: "communication", deliveryState: "delivered" });
    expect(map.hiddenEndpointEdges).toBe(1);
    expect(JSON.stringify(map)).not.toContain("SECRET");
    expect(map.empty).toBe(false);
  });

  it("distinguishes empty from failed and reports partial coverage", () => {
    const empty = buildExecutionMap({
      sessions: { ok: true, value: [] }, inbox: {}, waitGraph: { ok: true, value: { nodes: [], edges: [] } },
    });
    expect(empty).toMatchObject({ empty: true, failed: false });

    const failed = buildExecutionMap({
      sessions: { ok: false, reason: "error", message: "boom" }, inbox: {},
      waitGraph: { ok: false, reason: "unavailable", message: "n/a" },
    });
    expect(failed).toMatchObject({ empty: false, failed: true });

    const partial = buildExecutionMap({
      sessions: { ok: true, value: [session] },
      inbox: { aaaa1111: { ok: true, value: [] }, bbbb2222: { ok: false, reason: "error", message: "x" } },
      waitGraph: { ok: false, reason: "unavailable", message: "Not available on this server." },
    });
    expect(partial.coverage.find((c) => c.source === "inbox")?.state).toBe("partial");
    expect(partial.coverage.find((c) => c.source === "interaction-wait-graph")?.state).toBe("unavailable");
  });

  it("keeps required waits and confirmed cycles from the 016 wait graph", () => {
    const map = buildExecutionMap({
      sessions: { ok: true, value: [] }, inbox: {},
      waitGraph: { ok: true, value: {
        nodes: [{ id: "task:t1", kind: "task", state: "waiting", label: "Terminal task" },
                { id: "question_group:g1", kind: "question_group", state: "open", label: "Required operator response" }],
        edges: [{ from: "task:t1", to: "question_group:g1", required: true, cycle: false },
                { from: "task:t1", to: "question_group:g1", required: true, cycle: false }],
      } },
    });
    expect(map.edges).toHaveLength(1);
    expect(map.edges[0]).toMatchObject({ type: "waits_on", required: true, cycle: false });
    expect(map.empty).toBe(false);
  });
});

describe("ExecutionMapPanel", () => {
  const ready = (): ExecutionMap =>
    buildExecutionMap({
      sessions: { ok: true, value: [session] },
      inbox: { bbbb2222: { ok: true, value: [msg("1", "aaaa1111", "bbbb2222")] } },
      waitGraph: { ok: true, value: { nodes: [], edges: [] } },
    });

  it("shows agents, separates process status from relations, and is keyboard operable", async () => {
    render(<ExecutionMapPanel load={async () => ready()} />);
    const agent = await screen.findByRole("button", { name: /developer \(codex\)/ });
    expect(screen.getByText(/Snapshot, not live/)).toBeInTheDocument();
    agent.focus();
    fireEvent.click(agent);
    expect(agent).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/not a task result/)).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "message, delivered" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "supervisor (claude_code)" })).toBeInTheDocument();
  });

  it("renders the empty state distinctly from failure", async () => {
    render(<ExecutionMapPanel load={async () => buildExecutionMap({ sessions: { ok: true, value: [] }, inbox: {}, waitGraph: { ok: true, value: { nodes: [], edges: [] } } })} />);
    expect(await screen.findByText(/No agents are running/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("renders a load failure as an alert", async () => {
    render(<ExecutionMapPanel load={async () => { throw new Error("offline"); }} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("offline");
  });

  it("discards a late response from an earlier load", async () => {
    let resolveFirst: (m: ExecutionMap) => void = () => {};
    let calls = 0;
    const load = () => {
      calls++;
      if (calls === 1) return new Promise<ExecutionMap>((r) => { resolveFirst = r; });
      return Promise.resolve(buildExecutionMap({ sessions: { ok: true, value: [] }, inbox: {}, waitGraph: { ok: true, value: { nodes: [], edges: [] } } }));
    };
    render(<ExecutionMapPanel load={load} />);
    fireEvent.click(screen.getByRole("button", { name: /Refresh/ }));
    expect(await screen.findByText(/No agents are running/)).toBeInTheDocument();
    await act(async () => { resolveFirst(ready()); });
    expect(screen.queryByRole("button", { name: /developer/ })).toBeNull();
  });
});

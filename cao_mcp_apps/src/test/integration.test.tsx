// Integration tier cells — driven through the Mock Host peer.
//
// Closes the matrix integration cells at the postMessage/JSON-RPC layer:
//   - a frame from an untrusted origin is ignored; view state unchanged,
//   - a no-UI-surface host still returns structured plain-text results,
//   - an unreachable Backplane surfaces a retry control that recovers,
//   - re-mount idempotence: `oninitialized` replay + re-mount
//     hydration both reproduce the same governance timeline.

import {
  cleanup,
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Dashboard } from "../dashboard/Dashboard";
import { EventStreamView } from "../event-stream/EventStreamView";
import { AgentView } from "../agent/AgentView";
import { McpApp } from "../shared/mcpApp";
import type { CaoEvent, DashboardSnapshot } from "../shared/types";
import { MockHost, type MockHostOptions } from "./mockHost";

afterEach(() => cleanup());

function makeApp(host: MockHost): McpApp {
  return new McpApp({
    scope: host.appWindow as unknown as Window,
    target: host.appTarget as unknown as Window,
  });
}

function buildHost(opts: MockHostOptions = {}): MockHost {
  return new MockHost(opts);
}

const SAMPLE_EVENTS: CaoEvent[] = [
  {
    id: "e1",
    kind: "launch",
    terminal_id: "t1",
    session_name: "cao-x",
    timestamp: "2026-01-01T00:00:01Z",
    detail: {},
  },
  {
    id: "e2",
    kind: "handoff",
    terminal_id: "t1",
    session_name: "cao-x",
    timestamp: "2026-01-01T00:00:02Z",
    detail: {},
  },
];

const noopEventSource = () => ({
  addEventListener() {},
  close() {},
});

function snapshot(n: number): DashboardSnapshot {
  return {
    sessions: [{ id: "cao-x", name: "cao-x", status: "active" }],
    terminals: Array.from({ length: n }, (_, i) => ({
      id: `t${i}`,
      session_name: "cao-x",
      provider: "kiro_cli",
      agent_profile: `agent-${i}`,
      window: "w",
      status: "idle",
      last_active: null,
    })),
    counts: { sessions: 1, terminals: n },
    scopes: [],
  };
}

describe("19.13 — untrusted-origin frames are ignored", () => {
  it("drops a notification delivered from an unexpected origin", async () => {
    const host = buildHost({ tools: {} });
    const app = makeApp(host);
    const onToolResult = vi.fn();
    app.onToolResult(onToolResult);

    await app.connect(); // pins the host origin on the first reply

    // A forged tool-result from a different origin must be ignored.
    host.deliverFromOrigin("https://evil.example", {
      jsonrpc: "2.0",
      method: "ui/notifications/tool-result",
      params: { structuredContent: snapshot(3) },
    });
    expect(onToolResult).not.toHaveBeenCalled();

    // The same notification from the genuine host origin is delivered.
    host.pushNotification("ui/notifications/tool-result", {
      structuredContent: snapshot(3),
    });
    expect(onToolResult).toHaveBeenCalledOnce();
    app.disconnect();
  });

  it("leaves the dashboard grid unchanged when an evil-origin snapshot arrives", async () => {
    const host = buildHost({ tools: { render_dashboard: () => snapshot(0) } });
    const app = makeApp(host);
    render(<Dashboard app={app} />);

    // Empty placeholder after the initial (zero-agent) hydration.
    await screen.findByTestId("empty-placeholder");

    host.deliverFromOrigin("https://evil.example", {
      jsonrpc: "2.0",
      method: "ui/notifications/tool-result",
      params: { structuredContent: snapshot(5) },
    });

    // State unchanged: still the placeholder, no cards injected by the attacker.
    await Promise.resolve();
    expect(screen.queryByTestId("agent-card")).toBeNull();
    expect(screen.getByTestId("empty-placeholder")).toBeTruthy();
    app.disconnect();
  });
});

describe("19.9 — no-UI-surface host returns structured plain-text results", () => {
  it("delivers a plain-text content block whose payload is the structured result", async () => {
    const snap = snapshot(2);
    const host = buildHost({
      hostContext: { uiSurface: false },
      tools: { render_dashboard: () => snap },
    });
    const app = makeApp(host);
    await app.connect();

    const result = (await app.callServerTool("render_dashboard")) as {
      content: Array<{ type: string; text: string }>;
    };
    // No structuredContent (no UI surface) -> the raw CallToolResult is returned.
    expect(result.content[0].type).toBe("text");
    // The plain text is the serialized structured result and round-trips.
    expect(JSON.parse(result.content[0].text)).toEqual(snap);
    app.disconnect();
  });

  it("unwraps structuredContent for a UI-capable host", async () => {
    const snap = snapshot(1);
    const host = buildHost({
      hostContext: { uiSurface: true },
      tools: { render_dashboard: () => snap },
    });
    const app = makeApp(host);
    await app.connect();
    const result = await app.callServerTool("render_dashboard");
    expect(result).toEqual(snap);
    app.disconnect();
  });
});

describe("19.14 — unreachable Backplane surfaces a recoverable retry control", () => {
  it("shows the retry control on failure and recovers once the Backplane returns", async () => {
    let reachable = false;
    const host = buildHost({
      tools: {
        render_dashboard: () => {
          if (!reachable) throw new Error("ECONNREFUSED 127.0.0.1:9889");
          return snapshot(2);
        },
      },
    });
    const app = makeApp(host);
    render(<Dashboard app={app} />);

    // The failed initial poll surfaces the retry banner + button.
    await screen.findByTestId("retry-banner");
    expect(screen.getByTestId("retry-button")).toBeTruthy();

    // Backplane recovers; clicking Retry clears the banner and renders cards.
    reachable = true;
    fireEvent.click(screen.getByTestId("retry-button"));
    await waitFor(() =>
      expect(screen.queryByTestId("retry-banner")).toBeNull(),
    );
    expect(screen.getAllByTestId("agent-card")).toHaveLength(2);
    app.disconnect();
  });
});

describe("agent view — host-mediated hydration + choke point", () => {
  it("hydrates from the opening tool-result and routes a send through submit_command", async () => {
    const agentDetail = {
      terminal_id: "t1",
      session_name: "cao-main",
      provider: "kiro_cli",
      agent_profile: "builder",
      status: "processing",
      last_active: null,
      output_tail: "--- terminal t1 ---",
      scopes: ["cao:read", "cao:write", "cao:admin"],
    };
    const host = buildHost({
      tools: {
        render_agent_view: () => agentDetail,
        submit_command: () => ({ success: true, kind: "send_message" }),
      },
    });
    const app = makeApp(host);
    render(<AgentView app={app} />);

    // The agent view needs a terminal_id; the host delivers it as the opening
    // tool-result once initialized (host-mediated hydration).
    await waitFor(() => expect(host.initialized).toBe(true));
    host.pushNotification("ui/notifications/tool-result", {
      structuredContent: agentDetail,
    });

    await screen.findByTestId("agent-detail");
    expect(screen.getByTestId("agent-output").textContent).toContain(
      "terminal t1",
    );

    // Send a task through the choke point; a body-free model-context note is posted.
    fireEvent.change(screen.getByTestId("task-input"), {
      target: { value: "run the build" },
    });
    fireEvent.click(screen.getByTestId("btn-send_message"));

    await waitFor(() =>
      expect(
        host.toolCalls.some((c) => c.name === "cao___submit_command"),
      ).toBe(true),
    );
    await waitFor(() => expect(host.modelNotes.length).toBeGreaterThan(0));
    app.disconnect();
  });

  it("shows the loading placeholder until a snapshot arrives", () => {
    render(<AgentView app={undefined} />);
    expect(screen.getByTestId("agent-loading")).toBeTruthy();
  });
});

describe("19.8 — iframe teardown releases listeners", () => {
  it("disconnects on ui/resource-teardown so later notifications are ignored", async () => {
    const host = buildHost({ tools: {} });
    const app = makeApp(host);
    const onToolResult = vi.fn();
    app.onToolResult(onToolResult);
    await app.connect();

    // Host tears the iframe down.
    host.pushNotification("ui/resource-teardown", { reason: "host-unmount" });

    // A subsequent (genuine-origin) notification must NOT reach a handler,
    // because the message listener was released for GC.
    host.pushNotification("ui/notifications/tool-result", {
      structuredContent: snapshot(2),
    });
    expect(onToolResult).not.toHaveBeenCalled();
  });
});

describe("re-mount idempotence", () => {
  it("oninitialized replay hydrates the dashboard from a pushed tool-result", async () => {
    const host = buildHost({ tools: { render_dashboard: () => snapshot(0) } });
    const app = makeApp(host);
    render(<Dashboard app={app} />);
    await screen.findByTestId("empty-placeholder");

    // After initialized, the host replays the current fleet as a tool-result.
    expect(host.initialized).toBe(true);
    host.pushNotification("ui/notifications/tool-result", {
      structuredContent: snapshot(3),
    });
    await waitFor(() =>
      expect(screen.getAllByTestId("agent-card")).toHaveLength(3),
    );
    app.disconnect();
  });

  it("re-fetches the same history on re-mount (idempotent timeline)", async () => {
    const tools = { cao_fetch_history: () => ({ events: SAMPLE_EVENTS }) };

    // First mount.
    const host1 = buildHost({ tools });
    const app1 = makeApp(host1);
    const first = render(
      <EventStreamView app={app1} eventSourceFactory={noopEventSource} />,
    );
    await waitFor(() =>
      expect(screen.getAllByTestId("event-row")).toHaveLength(2),
    );
    const firstHtml = screen.getByTestId("event-stream").innerHTML;
    app1.disconnect();
    first.unmount();
    cleanup();

    // Re-mount: the same history is replayed, producing an identical timeline.
    const host2 = buildHost({ tools });
    const app2 = makeApp(host2);
    render(<EventStreamView app={app2} eventSourceFactory={noopEventSource} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("event-row")).toHaveLength(2),
    );
    expect(screen.getByTestId("event-stream").innerHTML).toBe(firstHtml);
    app2.disconnect();
  });
});

describe("US2 trusted first correlated response", () => {
  it.each(["origin", "source"])(
    "ignores forged first %s before accepting the expected host",
    async (forgery) => {
      const listeners = new Set<(event: MessageEvent) => void>();
      let outbound: any;
      const target = {
        postMessage: (frame: any) => {
          outbound = frame;
        },
      };
      const scope = {
        document: { referrer: "https://trusted.example/frame" },
        addEventListener: (kind: string, fn: any) => {
          if (kind === "message") listeners.add(fn);
        },
        removeEventListener: (_kind: string, fn: any) => listeners.delete(fn),
      };
      const app = new McpApp({
        scope: scope as unknown as Window,
        target: target as unknown as Window,
      });
      let connected = false;
      const pending = app.connect().then(() => {
        connected = true;
      });
      const deliver = (origin: string, source: unknown) =>
        listeners.forEach((fn) =>
          fn({
            data: { jsonrpc: "2.0", id: outbound.id, result: {} },
            origin,
            source,
          } as MessageEvent),
        );
      deliver(
        forgery === "origin"
          ? "https://evil.example"
          : "https://trusted.example",
        forgery === "source" ? {} : target,
      );
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
      expect(connected).toBe(false);
      deliver("https://trusted.example", target);
      await pending;
      expect(connected).toBe(true);
      app.disconnect();
    },
  );
});

describe("US2 common history/live cursor", () => {
  it("backfills a history-subscription gap and renews tickets from the confirmed cursor", async () => {
    const between = { ...SAMPLE_EVENTS[1], id: "between" };
    const descriptors: Record<string, unknown>[] = [];
    const sources: Array<{
      onerror?: () => void;
      close: ReturnType<typeof vi.fn>;
      push?: (event: { data: string }) => void;
    }> = [];
    const host = buildHost({
      tools: {
        cao_fetch_history: () => ({ events: [SAMPLE_EVENTS[0]], cursor: "e1" }),
        subscribe_events: (args) => {
          descriptors.push(args);
          return {
            url: `/events?ticket=single-${descriptors.length}&cursor=${args.last_event_id}`,
          };
        },
      },
    });
    const app = makeApp(host);
    render(
      <EventStreamView
        app={app}
        eventSourceFactory={() => {
          const source = {
            close: vi.fn(),
            push: undefined as ((event: { data: string }) => void) | undefined,
            addEventListener: (
              type: string,
              listener: (event: { data: string }) => void,
            ) => {
              if (type === "message") source.push = listener;
            },
          };
          sources.push(source);
          return source;
        }}
      />,
    );
    await waitFor(() => expect(sources.length).toBe(1));
    expect(descriptors[0]).toEqual({ last_event_id: "e1" });
    act(() => sources[0].push?.({ data: JSON.stringify(between) }));
    await waitFor(() =>
      expect(screen.getAllByTestId("event-row")).toHaveLength(2),
    );
    act(() => sources[0].onerror?.());
    await waitFor(() => expect(sources.length).toBe(2), { timeout: 3000 });
    expect(sources[0].close).toHaveBeenCalledOnce();
    expect(descriptors[1]).toEqual({ last_event_id: "between" });
    act(() => sources[1].push?.({ data: JSON.stringify(between) }));
    expect(screen.getAllByTestId("event-row")).toHaveLength(2);
    app.disconnect();
  });
});

it("reloads retained history when the backend reports an expired cursor", async () => {
  let snapshots = 0;
  const args: Record<string, unknown>[] = [];
  const host = buildHost({
    tools: {
      cao_fetch_history: () =>
        ++snapshots === 1
          ? { events: [SAMPLE_EVENTS[0]], cursor: "expired" }
          : { events: SAMPLE_EVENTS, cursor: "e2" },
      subscribe_events: (input) => {
        args.push(input);
        return args.length === 1
          ? { resync_required: true, error: "event_cursor_expired" }
          : { url: "/events?ticket=fresh&cursor=e2" };
      },
    },
  });
  const app = makeApp(host);
  const factory = vi.fn(noopEventSource);
  render(<EventStreamView app={app} eventSourceFactory={factory} />);
  await waitFor(() => expect(factory).toHaveBeenCalledOnce());
  expect(args).toEqual([{ last_event_id: "expired" }, { last_event_id: "e2" }]);
  expect(screen.getAllByTestId("event-row")).toHaveLength(2);
  app.disconnect();
});

it.each(["dashboard", "agent"])(
  "shows a visible %s error when trusted embedding origin is unavailable",
  async (view) => {
    const host = buildHost();
    host.appWindow.document.referrer = "";
    const app = makeApp(host);
    if (view === "dashboard") render(<Dashboard app={app} />);
    else render(<AgentView app={app} terminalId="t1" />);
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(host.initialized).toBe(false);
    expect(host.toolCalls).toHaveLength(0);
    app.disconnect();
  },
);

it("explicitly closes and resyncs on a named cursor_expired frame before native error", async () => {
  let snapshots = 0;
  const argumentsSeen: Record<string, unknown>[] = [];
  const sources: Array<{
    onerror?: (() => void) | null;
    handlers: Record<string, (event: { data: string }) => void>;
    close: ReturnType<typeof vi.fn>;
  }> = [];
  const host = buildHost({
    tools: {
      cao_fetch_history: () =>
        ++snapshots === 1
          ? { events: [SAMPLE_EVENTS[0]], cursor: "e1" }
          : { events: SAMPLE_EVENTS, cursor: "e2" },
      subscribe_events: (args) => {
        argumentsSeen.push(args);
        return { url: `/events?ticket=single-${argumentsSeen.length}` };
      },
    },
  });
  const app = makeApp(host);
  render(
    <EventStreamView
      app={app}
      eventSourceFactory={() => {
        const source = {
          handlers: {} as Record<string, (event: { data: string }) => void>,
          close: vi.fn(),
          addEventListener: (
            type: string,
            listener: (event: { data: string }) => void,
          ) => {
            source.handlers[type] = listener;
          },
        };
        sources.push(source);
        return source;
      }}
    />,
  );
  await waitFor(() => expect(sources).toHaveLength(1));
  expect(sources[0].handlers.cursor_expired).toBeTypeOf("function");
  act(() => {
    sources[0].handlers.cursor_expired({
      data: '{"code":"event_cursor_expired","resync_required":true}',
    });
    sources[0].onerror?.(); // The following EOF cannot schedule a second reconnect.
  });
  expect(sources[0].close).toHaveBeenCalledOnce();
  expect(screen.getByRole("status").textContent).toMatch(/expired|retained/i);
  await waitFor(() => expect(sources).toHaveLength(2), { timeout: 3000 });
  expect(snapshots).toBe(2);
  expect(argumentsSeen).toEqual([
    { last_event_id: "e1" },
    { last_event_id: "e2" },
  ]);
  expect(screen.getAllByTestId("event-row")).toHaveLength(2);
  app.disconnect();
});

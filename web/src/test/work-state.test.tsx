import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { api } from "../api";
import { StatusBadge } from "../components/StatusBadge";
import { TerminalView } from "../components/TerminalView";
import type { WorkView } from "../api";
import workContractFixture from "../../../test/fixtures/work_contract_v1.json";
import { workStatusSemantics } from "../work-status.generated";

vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    rows = 24;
    cols = 80;
    loadAddon() {}
    open() {}
    write() {}
    onSelectionChange() {}
    getSelection() {
      return "";
    }
    attachCustomKeyEventHandler() {}
    onData() {}
    focus() {}
    dispose() {}
  },
}));

vi.mock("@xterm/addon-fit", () => ({
  FitAddon: class {
    fit() {}
  },
}));

const runningWork: WorkView = {
  schema_version: 1,
  job_id: "job-123",
  work_item_id: "work-123",
  attempt_id: "attempt-123",
  job_state: "running",
  work_state: "running",
  attempt_state: "running",
  turn_state: "processing",
  process_state: "unknown",
  revision: 3,
  result_ref: null,
  cleanup_state: "not_requested",
  required_action: null,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

class TestWebSocket {
  static OPEN = 1;
  static instances: TestWebSocket[] = [];
  readyState = TestWebSocket.OPEN;
  binaryType = "";
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  sent: string[] = [];

  constructor() {
    TestWebSocket.instances.push(this);
  }

  send(message?: string) {
    if (message !== undefined) this.sent.push(message);
  }
  close() {
    this.readyState = 3;
  }
}

class TestResizeObserver {
  observe() {}
  disconnect() {}
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  TestWebSocket.instances = [];
});

function workViewFor(
  id: string,
  state: WorkView["work_state"] = "running",
): WorkView {
  return { ...runningWork, work_item_id: id, work_state: state };
}

function submitManualWork(id: string) {
  fireEvent.change(screen.getByLabelText("Work item ID"), {
    target: { value: id },
  });
  fireEvent.click(screen.getByRole("button", { name: "Observe work" }));
}

const semanticRoleClasses = {
  info: { foreground: "text-cao-info", background: "bg-cao-info/10" },
  accent: { foreground: "text-cao-accent", background: "bg-cao-accent/10" },
  warning: { foreground: "text-cao-warning", background: "bg-cao-warning/10" },
  danger: { foreground: "text-cao-danger", background: "bg-cao-danger/10" },
  neutral: { foreground: "text-cao-neutral", background: "bg-cao-neutral/10" },
} as const;

function workStateExpectation(state: string) {
  const expected =
    workContractFixture.presentation_expectations.work_states.find(
      (entry) => entry.state === state,
    );
  if (!expected)
    throw new Error(`fixture has no presentation expectation for ${state}`);
  return expected;
}

async function expectWorkPresentation(state: string) {
  const expected = workStateExpectation(state);
  const semantics = workStatusSemantics(state);
  expect(semantics).toMatchObject({
    state: expected.state,
    label: expected.label,
    semanticRole: expected.semantic_role,
    observedRunningCount: expected.running_count,
    observedSucceededCount: expected.succeeded_count,
  });

  const workLabel = await screen.findByText(expected.label);
  const roleClasses =
    semanticRoleClasses[
      expected.semantic_role as keyof typeof semanticRoleClasses
    ];
  expect(workLabel).toHaveClass(roleClasses.foreground);
  expect(workLabel.parentElement).toHaveClass(roleClasses.background);
  expect(
    screen.getByText(`Observed running: ${expected.running_count}`),
  ).toBeInTheDocument();
  expect(
    screen.getByText(`Observed succeeded: ${expected.succeeded_count}`),
  ).toBeInTheDocument();
}

describe("work state display", () => {
  it.each(workContractFixture.presentation_expectations.work_states)(
    "renders fixture semantics and counters for $state",
    async ({ state, label, semantic_role, running_count, succeeded_count }) => {
      vi.stubGlobal("WebSocket", TestWebSocket);
      vi.stubGlobal("ResizeObserver", TestResizeObserver);
      const workView = workViewFor(
        "work-fixture",
        state as WorkView["work_state"],
      );
      vi.spyOn(api, "getWorkItem").mockResolvedValue(workView);

      render(
        <TerminalView
          terminalId="terminal-1"
          workItemId="work-fixture"
          terminalStatus="idle"
          onClose={() => {}}
        />,
      );

      const semantics = workStatusSemantics(state);
      const workLabel = await screen.findByText(label);
      expect(semantics).toMatchObject({
        label,
        semanticRole: semantic_role,
        observedRunningCount: running_count,
        observedSucceededCount: succeeded_count,
      });
      expect(workLabel).toHaveClass(
        semanticRoleClasses[semantic_role as keyof typeof semanticRoleClasses]
          .foreground,
      );
      expect(workLabel.parentElement).toHaveClass(
        semanticRoleClasses[semantic_role as keyof typeof semanticRoleClasses]
          .background,
      );
      expect(
        screen.getByText(`Observed running: ${running_count}`),
      ).toBeInTheDocument();
      expect(
        screen.getByText(`Observed succeeded: ${succeeded_count}`),
      ).toBeInTheDocument();
      expect(screen.getByText("Idle")).toBeInTheDocument();
    },
  );

  it("lists all 15 fixture transition edges exactly once", () => {
    const transitions =
      workContractFixture.presentation_expectations.work_transitions;
    const edges = transitions.map(({ from, to }) => `${from}->${to}`);

    expect(transitions).toHaveLength(15);
    expect(new Set(edges).size).toBe(15);
  });

  it.each(workContractFixture.presentation_expectations.work_transitions)(
    "replays fixture Work transition $from → $to",
    async ({ from, to }) => {
      vi.stubGlobal("WebSocket", TestWebSocket);
      vi.stubGlobal("ResizeObserver", TestResizeObserver);
      const getWorkItem = vi
        .spyOn(api, "getWorkItem")
        .mockResolvedValueOnce({
          ...workViewFor("work-transition", from as WorkView["work_state"]),
          revision: 3,
        })
        .mockResolvedValueOnce({
          ...workViewFor("work-transition", to as WorkView["work_state"]),
          revision: 4,
        });

      render(
        <TerminalView
          terminalId="terminal-1"
          terminalStatus="idle"
          onClose={() => {}}
        />,
      );
      submitManualWork("work-transition");
      await expectWorkPresentation(from);

      fireEvent.click(
        screen.getByRole("button", { name: "Clear observed work" }),
      );
      expect(screen.getByText("No work selected")).toBeInTheDocument();
      submitManualWork("work-transition");
      await expectWorkPresentation(to);

      expect(getWorkItem).toHaveBeenNthCalledWith(1, "work-transition");
      expect(getWorkItem).toHaveBeenNthCalledWith(2, "work-transition");
    },
  );

  it("shows no-work selection without asserting numeric zeroes", () => {
    vi.stubGlobal("WebSocket", TestWebSocket);
    vi.stubGlobal("ResizeObserver", TestResizeObserver);
    const getWorkItem = vi.spyOn(api, "getWorkItem");

    render(
      <TerminalView
        terminalId="terminal-1"
        terminalStatus="idle"
        onClose={() => {}}
      />,
    );

    expect(screen.getByText("No work selected")).toBeInTheDocument();
    expect(
      screen.queryByText(/Observed (running|succeeded):/),
    ).not.toBeInTheDocument();
    expect(getWorkItem).not.toHaveBeenCalled();
  });

  it("uses durable running work instead of idle terminal liveness", () => {
    render(<StatusBadge status="idle" workView={runningWork} />);

    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.queryByText("Idle")).not.toBeInTheDocument();
  });

  it("shows succeeded durable work instead of idle terminal liveness", () => {
    const completedWork: WorkView = {
      ...runningWork,
      job_state: "completed",
      work_state: "succeeded",
      attempt_state: "finished",
      turn_state: null,
      process_state: "dead",
      revision: 4,
      result_ref: "result-123",
    };

    render(<StatusBadge status="idle" workView={completedWork} />);

    expect(screen.getByText("Succeeded")).toBeInTheDocument();
    expect(screen.queryByText("Idle")).not.toBeInTheDocument();
  });

  it("keeps terminal status as the explicit fallback without a work view", () => {
    render(<StatusBadge status="idle" />);

    expect(screen.getByText("Idle")).toBeInTheDocument();
  });

  it("requests the common work DTO from its encoded work-item route", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: "OK",
      text: () => Promise.resolve(JSON.stringify(runningWork)),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.getWorkItem("work/a b")).resolves.toEqual(runningWork);

    expect(fetchMock).toHaveBeenCalledWith(
      "/work-items/work%2Fa%20b",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("loads a work view once and shows it over idle terminal liveness", async () => {
    vi.stubGlobal("WebSocket", TestWebSocket);
    vi.stubGlobal("ResizeObserver", TestResizeObserver);
    const workRequest = deferred<WorkView>();
    const getWorkItem = vi
      .spyOn(api, "getWorkItem")
      .mockReturnValueOnce(workRequest.promise);

    render(
      <TerminalView
        terminalId="terminal-1"
        workItemId="work-123"
        terminalStatus="idle"
        onClose={() => {}}
      />,
    );

    await waitFor(() => expect(getWorkItem).toHaveBeenCalledWith("work-123"));
    await act(async () => {
      workRequest.resolve(runningWork);
      await workRequest.promise;
    });

    expect(await screen.findByText("Running")).toBeInTheDocument();
    expect(screen.getByText("Idle")).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "Observed work: work-123" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Observed running: 1")).toBeInTheDocument();
    expect(screen.getByText("Observed succeeded: 0")).toBeInTheDocument();
    expect(screen.getByText("Running")).toHaveClass("text-cao-info");
    expect(screen.getByText("Running").parentElement).toHaveClass(
      "bg-cao-info/10",
    );
    expect(getWorkItem).toHaveBeenCalledTimes(1);
  });

  it("shows succeeded counts and accent role independently from processing terminal state", async () => {
    vi.stubGlobal("WebSocket", TestWebSocket);
    vi.stubGlobal("ResizeObserver", TestResizeObserver);
    const succeededWork: WorkView = {
      ...runningWork,
      work_state: "succeeded",
      attempt_state: "finished",
      turn_state: null,
      process_state: "dead",
      revision: 4,
    };
    vi.spyOn(api, "getWorkItem").mockResolvedValue(succeededWork);

    render(
      <TerminalView
        terminalId="terminal-1"
        workItemId="work-123"
        terminalStatus="processing"
        onClose={() => {}}
      />,
    );

    expect(await screen.findByText("Succeeded")).toBeInTheDocument();
    expect(screen.getByText("Observed running: 0")).toBeInTheDocument();
    expect(screen.getByText("Observed succeeded: 1")).toBeInTheDocument();
    expect(screen.getByText("Succeeded")).toHaveClass("text-cao-accent");
    expect(screen.getByText("Succeeded").parentElement).toHaveClass(
      "bg-cao-accent/10",
    );
    expect(screen.getByText("Processing")).toBeInTheDocument();
  });

  it("shows Unknown and unknown counters while the selected work is loading", async () => {
    vi.stubGlobal("WebSocket", TestWebSocket);
    vi.stubGlobal("ResizeObserver", TestResizeObserver);
    const request = deferred<WorkView>();
    const getWorkItem = vi
      .spyOn(api, "getWorkItem")
      .mockReturnValueOnce(request.promise);

    render(
      <TerminalView
        terminalId="terminal-1"
        workItemId="work-123"
        terminalStatus="idle"
        onClose={() => {}}
      />,
    );

    await waitFor(() => expect(getWorkItem).toHaveBeenCalledWith("work-123"));
    const observedWork = screen.getByRole("region", {
      name: "Observed work: work-123",
    });
    expect(observedWork).toHaveTextContent("Unknown");
    expect(observedWork).toHaveTextContent("Observed running: —");
    expect(observedWork).toHaveTextContent("Observed succeeded: —");
  });

  it("does not request a work item without an explicit work-item identity", () => {
    vi.stubGlobal("WebSocket", TestWebSocket);
    vi.stubGlobal("ResizeObserver", TestResizeObserver);
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: "OK",
      text: () => Promise.resolve(JSON.stringify(runningWork)),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <TerminalView
        terminalId="terminal-1"
        terminalStatus="idle"
        onClose={() => {}}
      />,
    );

    expect(screen.getByText("Idle")).toBeInTheDocument();
    const workItemRequests = fetchMock.mock.calls
      .map(([input]) => String(input))
      .filter((url) => url.startsWith("/work-items/"));
    expect(workItemRequests).toEqual([]);
  });

  it("selects a work item from the manual observation form", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async (url: string) =>
          new Response(
            JSON.stringify(
              url.endsWith("/ws/ticket") ? { ticket: "single-use" } : {},
            ),
          ),
      ),
    );
    vi.stubGlobal("WebSocket", TestWebSocket);
    vi.stubGlobal("ResizeObserver", TestResizeObserver);
    const getWorkItem = vi
      .spyOn(api, "getWorkItem")
      .mockResolvedValue(workViewFor("work-fixture"));
    const pushState = vi.spyOn(window.history, "pushState");
    const setItem = vi.spyOn(window.localStorage, "setItem");

    render(
      <TerminalView
        terminalId="terminal-1"
        terminalStatus="idle"
        onClose={() => {}}
      />,
    );
    await waitFor(() => expect(TestWebSocket.instances).toHaveLength(1));
    const socket = TestWebSocket.instances[0];
    socket.onopen?.();
    const sentOnOpen = [...socket.sent];

    const input = screen.getByLabelText("Work item ID");
    expect(getWorkItem).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: "  work-fixture  " } });
    expect(getWorkItem).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Observe work" }));

    await waitFor(() =>
      expect(getWorkItem).toHaveBeenCalledWith("work-fixture"),
    );
    expect(
      await screen.findByText("Observed work: work-fixture"),
    ).toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(getWorkItem).toHaveBeenCalledTimes(1);
    expect(TestWebSocket.instances).toHaveLength(1);
    expect(socket.sent).toEqual(sentOnOpen);
    expect(
      socket.sent.some((message) => JSON.parse(message).type === "input"),
    ).toBe(false);
    expect(pushState).not.toHaveBeenCalled();
    expect(setItem).not.toHaveBeenCalled();

    fireEvent.click(
      screen.getByRole("button", { name: "Clear observed work" }),
    );
    expect(
      screen.queryByRole("region", { name: "Observed work: work-fixture" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Running")).not.toBeInTheDocument();
    expect(screen.getByText("Idle")).toBeInTheDocument();
    expect(
      (screen.getByLabelText("Work item ID") as HTMLInputElement).value,
    ).toBe("");
    expect(getWorkItem).toHaveBeenCalledTimes(1);
  });

  it("shows an accessible error when the selected work cannot be read", async () => {
    vi.stubGlobal("WebSocket", TestWebSocket);
    vi.stubGlobal("ResizeObserver", TestResizeObserver);
    const getWorkItem = vi
      .spyOn(api, "getWorkItem")
      .mockRejectedValue(new Error("not found"));

    render(
      <TerminalView
        terminalId="terminal-1"
        terminalStatus="idle"
        onClose={() => {}}
      />,
    );
    submitManualWork("missing-work");

    expect(await screen.findByText("Work unavailable")).toBeInTheDocument();
    const observedWork = screen.getByRole("region", {
      name: "Observed work: missing-work",
    });
    expect(
      observedWork.querySelector('[aria-live="polite"]'),
    ).toBeInTheDocument();
    expect(observedWork.querySelector("span")).toHaveTextContent(
      "Observed work: missing-work",
    );
    expect(observedWork).toHaveTextContent("Unknown");
    expect(observedWork).toHaveTextContent("Observed running: —");
    expect(observedWork).toHaveTextContent("Observed succeeded: —");
    expect(observedWork).not.toHaveTextContent("Idle");
    expect(screen.getByText("Idle")).toBeInTheDocument();
    expect(getWorkItem).toHaveBeenCalledTimes(1);
  });

  it("rejects an empty manual ID without requesting work", () => {
    vi.stubGlobal("WebSocket", TestWebSocket);
    vi.stubGlobal("ResizeObserver", TestResizeObserver);
    const getWorkItem = vi.spyOn(api, "getWorkItem");

    render(
      <TerminalView
        terminalId="terminal-1"
        terminalStatus="idle"
        onClose={() => {}}
      />,
    );
    fireEvent.change(screen.getByLabelText("Work item ID"), {
      target: { value: "   " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Observe work" }));

    expect(screen.getByText("Enter a work item ID")).toBeInTheDocument();
    expect(getWorkItem).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("region", { name: "Observed work: " }),
    ).not.toBeInTheDocument();
  });

  it.each([
    ["a different returned ID", workViewFor("some-other-work")],
    [
      "an unsupported schema version",
      { ...workViewFor("work-target"), schema_version: 2 },
    ],
    [
      "an unknown work state",
      { ...workViewFor("work-target"), work_state: "unknown" },
    ],
  ])("shows unknown semantics for %s", async (_reason, response) => {
    vi.stubGlobal("WebSocket", TestWebSocket);
    vi.stubGlobal("ResizeObserver", TestResizeObserver);
    const getWorkItem = vi
      .spyOn(api, "getWorkItem")
      .mockResolvedValue(response as WorkView);

    render(
      <TerminalView
        terminalId="terminal-1"
        workItemId="work-target"
        terminalStatus="idle"
        onClose={() => {}}
      />,
    );

    expect(await screen.findByText("Work unavailable")).toBeInTheDocument();
    const observedWork = screen.getByRole("region", {
      name: "Observed work: work-target",
    });
    expect(observedWork).not.toHaveTextContent("Running");
    expect(observedWork).not.toHaveTextContent("Succeeded");
    expect(observedWork).toHaveTextContent("Unknown");
    expect(observedWork).toHaveTextContent("Observed running: —");
    expect(observedWork).toHaveTextContent("Observed succeeded: —");
    expect(getWorkItem).toHaveBeenCalledWith("work-target");
  });

  it("hides the previous result and ignores a late manual selection response", async () => {
    vi.stubGlobal("WebSocket", TestWebSocket);
    vi.stubGlobal("ResizeObserver", TestResizeObserver);
    const firstRequest = deferred<WorkView>();
    const secondRequest = deferred<WorkView>();
    const getWorkItem = vi
      .spyOn(api, "getWorkItem")
      .mockReturnValueOnce(firstRequest.promise)
      .mockReturnValueOnce(secondRequest.promise);

    render(
      <TerminalView
        terminalId="terminal-1"
        terminalStatus="idle"
        onClose={() => {}}
      />,
    );
    submitManualWork("work-a");
    await waitFor(() => expect(getWorkItem).toHaveBeenCalledWith("work-a"));
    expect(
      screen.getByRole("region", { name: "Observed work: work-a" }),
    ).toHaveTextContent("Loading observed work");
    expect(
      screen.getByRole("region", { name: "Observed work: work-a" }),
    ).toHaveTextContent("Unknown");
    expect(
      screen.getByRole("region", { name: "Observed work: work-a" }),
    ).toHaveTextContent("Observed running: —");
    expect(
      screen.getByRole("region", { name: "Observed work: work-a" }),
    ).toHaveTextContent("Observed succeeded: —");

    submitManualWork("work-b");
    await waitFor(() => expect(getWorkItem).toHaveBeenCalledWith("work-b"));
    expect(
      screen.queryByRole("region", { name: "Observed work: work-a" }),
    ).not.toBeInTheDocument();
    await act(async () => {
      secondRequest.resolve(workViewFor("work-b"));
      await secondRequest.promise;
    });
    expect(
      screen.getByRole("region", { name: "Observed work: work-b" }),
    ).toHaveTextContent("Running");

    await act(async () => {
      firstRequest.resolve(workViewFor("work-a", "failed"));
      await firstRequest.promise;
    });

    expect(
      screen.getByRole("region", { name: "Observed work: work-b" }),
    ).toHaveTextContent("Running");
    expect(screen.queryByText("Failed")).not.toBeInTheDocument();
    expect(getWorkItem).toHaveBeenCalledTimes(2);
  });

  it("clears local selection and result when the terminal changes", async () => {
    vi.stubGlobal("WebSocket", TestWebSocket);
    vi.stubGlobal("ResizeObserver", TestResizeObserver);
    const request = deferred<WorkView>();
    const getWorkItem = vi
      .spyOn(api, "getWorkItem")
      .mockReturnValueOnce(request.promise);
    const { rerender } = render(
      <TerminalView
        terminalId="terminal-1"
        terminalStatus="idle"
        onClose={() => {}}
      />,
    );
    submitManualWork("work-a");
    await waitFor(() => expect(getWorkItem).toHaveBeenCalledWith("work-a"));
    expect(
      screen.getByRole("region", { name: "Observed work: work-a" }),
    ).toHaveTextContent("Loading observed work");

    rerender(
      <TerminalView
        terminalId="terminal-2"
        terminalStatus="idle"
        onClose={() => {}}
      />,
    );
    await act(async () => {
      request.resolve(workViewFor("work-a"));
      await request.promise;
    });

    expect(
      screen.queryByRole("region", { name: "Observed work: work-a" }),
    ).not.toBeInTheDocument();
    expect(
      (screen.getByLabelText("Work item ID") as HTMLInputElement).value,
    ).toBe("");
    expect(
      screen.queryByRole("button", { name: "Clear observed work" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Running")).not.toBeInTheDocument();
    expect(getWorkItem).toHaveBeenCalledTimes(1);
  });

  it("gives a work-item prop precedence over and resets manual selection", async () => {
    vi.stubGlobal("WebSocket", TestWebSocket);
    vi.stubGlobal("ResizeObserver", TestResizeObserver);
    const getWorkItem = vi
      .spyOn(api, "getWorkItem")
      .mockResolvedValueOnce(workViewFor("work-local"))
      .mockResolvedValueOnce(workViewFor("work-prop"));
    const { rerender } = render(
      <TerminalView
        terminalId="terminal-1"
        terminalStatus="idle"
        onClose={() => {}}
      />,
    );
    submitManualWork("work-local");
    expect(
      await screen.findByRole("region", { name: "Observed work: work-local" }),
    ).toHaveTextContent("Running");

    rerender(
      <TerminalView
        terminalId="terminal-1"
        workItemId="work-prop"
        terminalStatus="idle"
        onClose={() => {}}
      />,
    );

    expect(screen.queryByLabelText("Work item ID")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "Observed work: work-local" }),
    ).not.toBeInTheDocument();
    expect(
      await screen.findByRole("region", { name: "Observed work: work-prop" }),
    ).toHaveTextContent("Running");
    expect(getWorkItem).toHaveBeenCalledTimes(2);
  });

  it("ignores a previous work-item response after the work identity changes", async () => {
    vi.stubGlobal("WebSocket", TestWebSocket);
    vi.stubGlobal("ResizeObserver", TestResizeObserver);
    const firstRequest = deferred<WorkView>();
    const secondRequest = deferred<WorkView>();
    const getWorkItem = vi
      .spyOn(api, "getWorkItem")
      .mockReturnValueOnce(firstRequest.promise)
      .mockReturnValueOnce(secondRequest.promise);
    const { rerender } = render(
      <TerminalView
        terminalId="terminal-1"
        workItemId="work-old"
        terminalStatus="idle"
        onClose={() => {}}
      />,
    );

    await waitFor(() => expect(getWorkItem).toHaveBeenCalledWith("work-old"));
    rerender(
      <TerminalView
        terminalId="terminal-1"
        workItemId="work-new"
        terminalStatus="idle"
        onClose={() => {}}
      />,
    );
    await waitFor(() => expect(getWorkItem).toHaveBeenCalledWith("work-new"));
    await act(async () => {
      secondRequest.resolve(workViewFor("work-new"));
      await secondRequest.promise;
    });
    expect(await screen.findByText("Running")).toBeInTheDocument();

    await act(async () => {
      firstRequest.resolve(workViewFor("work-old", "failed"));
      await firstRequest.promise;
    });

    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.queryByText("Failed")).not.toBeInTheDocument();
  });

  it("discards a lower revision for the same observed work ID", async () => {
    vi.stubGlobal("WebSocket", TestWebSocket);
    vi.stubGlobal("ResizeObserver", TestResizeObserver);
    const lowerRevision = deferred<WorkView>();
    const getWorkItem = vi
      .spyOn(api, "getWorkItem")
      .mockResolvedValueOnce({ ...workViewFor("work-a"), revision: 5 })
      .mockReturnValueOnce(lowerRevision.promise);
    render(
      <TerminalView
        terminalId="terminal-1"
        terminalStatus="idle"
        onClose={() => {}}
      />,
    );

    submitManualWork("work-a");
    expect(
      await screen.findByRole("region", { name: "Observed work: work-a" }),
    ).toHaveTextContent("Observed running: 1");
    submitManualWork("work-a");
    await waitFor(() => expect(getWorkItem).toHaveBeenCalledTimes(2));
    await act(async () => {
      lowerRevision.resolve({
        ...workViewFor("work-a", "failed"),
        revision: 4,
      });
      await lowerRevision.promise;
    });

    expect(
      screen.getByRole("region", { name: "Observed work: work-a" }),
    ).toHaveTextContent("Running");
    expect(
      screen.getByRole("region", { name: "Observed work: work-a" }),
    ).toHaveTextContent("Observed running: 1");
    expect(screen.queryByText("Failed")).not.toBeInTheDocument();
  });

  it("invalidates the observation on contradictory work states at equal revision", async () => {
    vi.stubGlobal("WebSocket", TestWebSocket);
    vi.stubGlobal("ResizeObserver", TestResizeObserver);
    const contradictoryRevision = deferred<WorkView>();
    const getWorkItem = vi
      .spyOn(api, "getWorkItem")
      .mockResolvedValueOnce({ ...workViewFor("work-a"), revision: 5 })
      .mockReturnValueOnce(contradictoryRevision.promise);
    render(
      <TerminalView
        terminalId="terminal-1"
        terminalStatus="idle"
        onClose={() => {}}
      />,
    );

    submitManualWork("work-a");
    expect(
      await screen.findByRole("region", { name: "Observed work: work-a" }),
    ).toHaveTextContent("Running");
    submitManualWork("work-a");
    await waitFor(() => expect(getWorkItem).toHaveBeenCalledTimes(2));
    await act(async () => {
      contradictoryRevision.resolve({
        ...workViewFor("work-a", "succeeded"),
        revision: 5,
      });
      await contradictoryRevision.promise;
    });

    const observedWork = screen.getByRole("region", {
      name: "Observed work: work-a",
    });
    expect(observedWork).toHaveTextContent("Unknown");
    expect(observedWork).toHaveTextContent("Observed running: —");
    expect(observedWork).toHaveTextContent("Observed succeeded: —");
    expect(observedWork).not.toHaveTextContent("Running");
    expect(observedWork).not.toHaveTextContent("Succeeded");
  });
});

it.each(
  [
    ["waiting_user_answer", "Awaiting Input"],
    ["waiting_quota", "Awaiting Quota Reset"],
  ].flatMap(([waiting, label]) =>
    (["reconcile", "cancelling", "cancelled"] as const).map((state) => ({
      waiting,
      label,
      state,
    })),
  ),
)(
  "TerminalView preserves $waiting while showing $state diagnosis",
  async ({ waiting, label, state }) => {
    vi.stubGlobal("WebSocket", TestWebSocket);
    vi.stubGlobal("ResizeObserver", TestResizeObserver);
    vi.spyOn(api, "getTerminalTurn").mockResolvedValue({
      terminal_id: "terminal-1",
      provider: "codex",
      generation: "a".repeat(32),
      state,
      reason: "receipt_missing",
      attempts: 3,
      allowed_actions: ["verify", "cancel"],
    });
    render(
      <TerminalView
        terminalId="terminal-1"
        terminalStatus={waiting}
        onClose={() => {}}
      />,
    );
    await screen.findByText(`Turn: ${state}`);
    expect(screen.getByText(label)).toBeInTheDocument();
  },
);

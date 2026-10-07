import { afterEach, describe, expect, it, vi } from "vitest";
import { api, eventStreamUrl, setActiveNode, terminalSocketUrl } from "../api";
import { useStore } from "../store";

afterEach(() => {
  setActiveNode(null);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("fleet transport ownership", () => {
  it("routes every transport to the selected node", async () => {
    setActiveNode("worker one");
    const fetch = vi.fn().mockResolvedValue(new Response("[]"));
    vi.stubGlobal("fetch", fetch);
    await api.listSessions();
    expect(fetch.mock.calls[0][0]).toBe("/nodes/worker%20one/sessions");
    expect(eventStreamUrl("same-id")).toBe(
      "/nodes/worker%20one/workflows/runs/same-id/events",
    );
    expect(terminalSocketUrl("same-id")).toContain(
      "/nodes/worker%20one/terminals/same-id/ws",
    );
  });

  it("rejects late HTTP replies after node switch without redelivering", async () => {
    let finish!: (response: Response) => void;
    const fetch = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    );
    vi.stubGlobal("fetch", fetch);
    setActiveNode("first");
    const pending = api.listSessions();
    setActiveNode("second");
    finish(new Response('[{"name":"old"}]'));
    await expect(pending).rejects.toThrow("Node selection changed");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("keeps same-id run selection isolated across nodes even with mocked APIs", async () => {
    let finish!: (value: any) => void;
    vi.spyOn(api, "inspectWorkflowRun").mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    vi.spyOn(api, "getWorkflowRunEvents").mockResolvedValue({
      events: [],
      gaps: [],
    } as any);
    useStore.getState().selectNode("first");
    const pending = useStore.getState().selectWorkflowRun("same-id");
    useStore.getState().selectNode("second");
    useStore.setState({
      selectedRunId: "same-id",
      selectedRun: { run_id: "same-id", state: "new" } as any,
    });
    finish({ run_id: "same-id", state: "old" });
    await pending;
    expect((useStore.getState().selectedRun as any).state).toBe("new");
  });

  it("does not fall back to origin on fleet authentication rejection", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("{}", { status: 403 })),
    );
    await expect(api.getFleet()).rejects.toMatchObject({ status: 403 });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});

it("refreshes elastic membership without changing the selected node", async () => {
  useStore.setState({ fleetNodes: [], fleetMode: true, fleetError: null });
  useStore.getState().selectNode("selected");
  vi.spyOn(api, "getFleet").mockResolvedValue({
    machines: [
      {
        name: "new-worker",
        label: "New",
        host: "worker",
        online: true,
        sessions: [],
      },
    ],
  });
  await useStore.getState().refreshFleet();
  expect(useStore.getState().fleetNodes.map((node) => node.name)).toEqual([
    "new-worker",
  ]);
  expect(useStore.getState().activeNode).toBe("selected");
});

it("keeps last known membership on registry failure without standalone fallback", async () => {
  const nodes = [
    {
      name: "selected",
      label: "Selected",
      host: "worker",
      online: true,
      sessions: [],
    },
  ];
  useStore.setState({ fleetMode: true, fleetNodes: nodes });
  useStore.getState().selectNode("selected");
  vi.spyOn(api, "getFleet").mockRejectedValue(
    Object.assign(new Error("denied"), { status: 403 }),
  );
  await useStore.getState().refreshFleet();
  expect(useStore.getState().fleetNodes).toEqual(nodes);
  expect(useStore.getState().activeNode).toBe("selected");
  expect(useStore.getState().fleetError).toBe("denied");
});

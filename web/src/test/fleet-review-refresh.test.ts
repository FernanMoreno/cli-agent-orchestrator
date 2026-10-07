import { afterEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { useStore } from "../store";

const node = (name: string) => ({
  name,
  label: name,
  host: "worker",
  online: true,
  sessions: [],
});
afterEach(() => {
  vi.restoreAllMocks();
  useStore.setState({ fleetMode: false, fleetNodes: [], fleetError: null });
});

it("latest overlapping poll owns membership even when transport ignores abort", async () => {
  const replies: ((value: any) => void)[] = [];
  vi.spyOn(api, "getFleet").mockImplementation(
    () => new Promise((resolve) => replies.push(resolve)),
  );
  useStore.setState({ fleetMode: true, fleetNodes: [node("initial")] });
  const first = useStore.getState().refreshFleet();
  const second = useStore.getState().refreshFleet();
  replies[1]({ machines: [node("fresh")] });
  await second;
  replies[0]({ machines: [node("stale")] });
  await first;
  expect(useStore.getState().fleetNodes[0].name).toBe("fresh");
});

it("stopped Dashboard owner aborts its poll and rejects late publication", async () => {
  let finish!: (value: any) => void;
  let signal: AbortSignal | undefined;
  vi.spyOn(api, "getFleet").mockImplementation((...args: any[]) => {
    signal = args[0];
    return new Promise((resolve) => {
      finish = resolve;
    });
  });
  useStore.setState({ fleetMode: true, fleetNodes: [node("initial")] });
  const pending = useStore.getState().refreshFleet();
  useStore.getState().stopFleetRefresh();
  expect(signal?.aborted).toBe(true);
  finish({ machines: [node("stale")] });
  await pending;
  expect(useStore.getState().fleetNodes[0].name).toBe("initial");
});

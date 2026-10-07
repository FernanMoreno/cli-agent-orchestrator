import { cleanup, render, waitFor, act } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TerminalView } from "../components/TerminalView";
import { initializeBrowserAuth } from "../auth";
let input: (data: string) => void;
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
    onData(fn: (data: string) => void) {
      input = fn;
    }
    focus() {}
    dispose() {}
  },
}));
vi.mock("@xterm/addon-fit", () => ({
  FitAddon: class {
    fit() {}
  },
}));
class Socket {
  static OPEN = 1;
  static instances: Socket[] = [];
  readyState = 1;
  binaryType = "";
  onopen: (() => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onclose: ((e: CloseEvent) => void) | null = null;
  sent: string[] = [];
  constructor(public url: string) {
    Socket.instances.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {}
}
const session = {
  session_id: "one",
  username: "operator",
  remembered: true,
  access_expires_at: 4600,
  idle_expires_at: 9000,
  absolute_expires_at: 10000,
  server_time: 1000,
  session_revision: 1,
};
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  Socket.instances = [];
});
it("requests a fresh scoped ticket after lease close without replaying input", async () => {
  let ticket = 0;
  const fetch = vi.fn(
    async (url: string) =>
      new Response(
        JSON.stringify(
          url === "/auth/config"
            ? { mode: "local_password" }
            : url.endsWith("/ws/ticket")
              ? { ticket: `single-${++ticket}` }
              : session,
        ),
      ),
  );
  vi.stubGlobal("fetch", fetch);
  vi.stubGlobal("WebSocket", Socket);
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  await initializeBrowserAuth();
  render(<TerminalView terminalId="term" onClose={() => {}} />);
  await waitFor(() => expect(Socket.instances).toHaveLength(1));
  expect(new URL(Socket.instances[0].url).searchParams.get("ticket")).toBe(
    "single-1",
  );
  await act(async () => {
    input("once");
    await Promise.resolve();
  });
  act(() => Socket.instances[0].onclose?.({ code: 4401 } as CloseEvent));
  await waitFor(() => expect(Socket.instances).toHaveLength(2), {
    timeout: 4000,
  });
  expect(new URL(Socket.instances[1].url).searchParams.get("ticket")).toBe(
    "single-2",
  );
  expect(
    fetch.mock.calls.filter((call) => call[0].endsWith("/ws/ticket")),
  ).toHaveLength(2);
  expect(Socket.instances[0].sent).toContain(
    JSON.stringify({ type: "input", data: "once" }),
  );
  expect(
    Socket.instances[1].sent.some(
      (value) => JSON.parse(value).type === "input",
    ),
  ).toBe(false);
});

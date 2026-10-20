import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { api, getNodeEpoch, setActiveNode } from "../api";
import { AgentPanel } from "../components/AgentPanel";
import { useStore } from "../store";

vi.mock("../components/TerminalView", () => ({
  TerminalView: ({ terminalId, expectedInteractionIncarnationId }: {
    terminalId: string;
    expectedInteractionIncarnationId?: string;
  }) => (
    <div data-testid="opened-terminal" data-incarnation={expectedInteractionIncarnationId}>{terminalId}</div>
  ),
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  setActiveNode(null);
  useStore.setState({
    sessions: [],
    connected: false,
    activeSession: null,
    activeSessionDetail: null,
    terminalStatuses: {},
    nodeEpoch: getNodeEpoch(),
  });
});

describe("manual question terminal navigation", () => {
  it("opens the exact source terminal and sends no input", async () => {
    const terminal = {
      id: "source-terminal",
      tmux_session: "session-owned",
      tmux_window: "agent-3",
      provider: "codex",
      agent_profile: "reviewer",
      session_incarnation_id: "incarnation-source",
      created_at: null,
      last_active: null,
    };
    const sessionDetail = {
      session: { id: "session-owned", name: "Owned session", status: "active" },
      terminals: [terminal],
    };
    vi.spyOn(api, "listProviders").mockResolvedValue([]);
    vi.spyOn(api, "listProfiles").mockResolvedValue([]);
    vi.spyOn(api, "listSessions").mockResolvedValue([sessionDetail.session]);
    vi.spyOn(api, "getSession").mockResolvedValue(sessionDetail);
    vi.spyOn(api, "getCurrentTerminal").mockResolvedValue({
      id: "source-terminal",
      session_name: "session-owned",
      session_incarnation_id: "incarnation-source",
      provider: "codex",
      agent_profile: "reviewer",
    });
    vi.spyOn(api, "getTerminalStatus").mockResolvedValue(null);
    vi.spyOn(api, "getWorkingDirectory").mockResolvedValue({ working_directory: null });
    const onHandled = vi.fn();
    const sendInput = vi.spyOn(api, "sendInput").mockResolvedValue({ success: true });

    render(
      <AgentPanel
        manualTerminalId="source-terminal"
        manualTerminalIncarnationId="incarnation-source"
        onManualTerminalHandled={onHandled}
      />,
    );

    expect(await screen.findByTestId("opened-terminal")).toHaveTextContent("source-terminal");
    expect(screen.getByTestId("opened-terminal")).toHaveAttribute("data-incarnation", "incarnation-source");
    await waitFor(() => expect(onHandled).toHaveBeenCalledWith("source-terminal", "incarnation-source"));
    expect(useStore.getState().activeSession).toBe("session-owned");
    expect(sendInput).not.toHaveBeenCalled();
  });

  it("does not open a replacement terminal that reused the question source id", async () => {
    const terminal = {
      id: "source-terminal",
      tmux_session: "session-owned",
      tmux_window: "agent-3",
      provider: "codex",
      agent_profile: "reviewer",
      session_incarnation_id: "incarnation-replacement",
      created_at: null,
      last_active: null,
    };
    const sessionDetail = {
      session: { id: "session-owned", name: "Owned session", status: "active" },
      terminals: [terminal],
    };
    vi.spyOn(api, "listProviders").mockResolvedValue([]);
    vi.spyOn(api, "listProfiles").mockResolvedValue([]);
    vi.spyOn(api, "listSessions").mockResolvedValue([sessionDetail.session]);
    vi.spyOn(api, "getSession").mockResolvedValue(sessionDetail);
    vi.spyOn(api, "getCurrentTerminal").mockResolvedValue({
      id: "source-terminal",
      session_name: "session-owned",
      session_incarnation_id: "incarnation-replacement",
      provider: "codex",
      agent_profile: "reviewer",
    });
    const onHandled = vi.fn();
    render(
      <AgentPanel
        manualTerminalId="source-terminal"
        manualTerminalIncarnationId="incarnation-original"
        onManualTerminalHandled={onHandled}
      />,
    );

    await waitFor(() =>
      expect(useStore.getState().snackbar?.message).toBe(
        "Source terminal incarnation changed. No terminal was opened.",
      ),
    );
    expect(screen.queryByTestId("opened-terminal")).toBeNull();
    expect(api.getSession).not.toHaveBeenCalled();
    await waitFor(() => expect(onHandled).toHaveBeenCalledWith("source-terminal", "incarnation-original"));
  });

  it("fails closed when a manual terminal request has no source incarnation", async () => {
    vi.spyOn(api, "listProviders").mockResolvedValue([]);
    vi.spyOn(api, "listProfiles").mockResolvedValue([]);
    vi.spyOn(api, "listSessions").mockResolvedValue([]);
    const currentTerminal = vi.spyOn(api, "getCurrentTerminal");
    const onHandled = vi.fn();
    render(
      <AgentPanel
        manualTerminalId="source-terminal"
        onManualTerminalHandled={onHandled}
      />,
    );

    await waitFor(() =>
      expect(useStore.getState().snackbar?.message).toMatch(
        /Could not verify the source terminal incarnation/i,
      ),
    );
    expect(screen.queryByTestId("opened-terminal")).toBeNull();
    expect(currentTerminal).not.toHaveBeenCalled();
    await waitFor(() => expect(onHandled).toHaveBeenCalledWith("source-terminal", ""));
  });
});

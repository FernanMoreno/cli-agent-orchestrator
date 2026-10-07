// Agent detail view (ui://cao/agent).
//
// Renders a single agent's status + a tail of its terminal output, and exposes
// the per-agent TaskControl (the choke-point gestures). Hydrates from the
// initial tool result and re-fetches on re-mount.

import React, { useEffect, useRef, useState } from "react";
import { HeaderBar } from "../shared/HeaderBar";
import { describeGesture, McpApp } from "../shared/mcpApp";
import { TaskControl } from "../shared/TaskControl";
import type { AgentDetailSnapshot, SubmitCommandKind } from "../shared/types";

const POLL_INTERVAL_MS = 30_000;

export interface AgentViewProps {
  app?: McpApp;
  terminalId?: string;
  initialSnapshot?: AgentDetailSnapshot;
}

export function AgentView({
  app,
  terminalId,
  initialSnapshot,
}: AgentViewProps): JSX.Element {
  const [snapshot, setSnapshot] = useState<AgentDetailSnapshot | null>(
    initialSnapshot ?? null,
  );
  const tidRef = useRef(terminalId ?? initialSnapshot?.terminal_id);
  const [connectionError, setConnectionError] = useState(false);
  const [recoveryBusy, setRecoveryBusy] = useState(false);
  const [recoveryError, setRecoveryError] = useState<string | null>(null);

  useEffect(() => {
    if (!app) return;
    let stop: (() => void) | undefined;

    app.onToolResult((result) => {
      const snap = (result?.structuredContent ?? result) as
        AgentDetailSnapshot | undefined;
      if (snap && snap.terminal_id) {
        tidRef.current = snap.terminal_id;
        setSnapshot(snap);
      }
    });

    void app
      .connect()
      .then(() => {
        const tid = tidRef.current;
        if (!tid) return;
        stop = app.startPolling(
          "render_agent_view",
          POLL_INTERVAL_MS,
          (snap) => {
            if (snap && (snap as AgentDetailSnapshot).terminal_id) {
              setSnapshot(snap as AgentDetailSnapshot);
            }
          },
          { terminal_id: tid },
        );
      })
      .catch(() => setConnectionError(true));

    return () => {
      if (stop) stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [app, terminalId]);

  async function handleSubmit(
    kind: SubmitCommandKind,
    payload: Record<string, unknown>,
  ) {
    if (!app) return { success: false, error: "not connected" };
    // TaskControl already builds the correct payload (terminal_id / session_name)
    // via buildGesturePayload, so route it through the choke point as-is.
    const result = await app.submitCommand(kind, payload);
    const turn = result.turn as AgentDetailSnapshot["turn"];
    if (
      turn &&
      typeof turn.state === "string" &&
      Array.isArray(turn.allowed_actions)
    ) {
      setSnapshot((current) => {
        if (
          !current ||
          current.terminal_id !== payload.terminal_id ||
          current.turn?.generation !== payload.generation ||
          turn.terminal_id !== current.terminal_id
        )
          return current;
        return { ...current, turn };
      });
    }
    if (result.success) {
      // Exactly one token-efficient, body-free note per material action,
      // described with a Semantic_Primitive. silentlyNoteToModel never throws,
      // so a failed note cannot block the iframe.
      void app.silentlyNoteToModel(
        describeGesture(kind, tidRef.current ?? undefined),
      );
    }
    return result;
  }

  async function recover(kind: "verify_turn" | "cancel_turn") {
    const generation = snapshot?.turn?.generation;
    if (!generation || recoveryBusy) return;
    setRecoveryBusy(true);
    setRecoveryError(null);
    try {
      const result = await handleSubmit(kind, {
        terminal_id: snapshot.terminal_id,
        generation,
      });
      if (!result.success)
        setRecoveryError(
          String(
            result.error ?? "Recovery is pending; inspect the current turn",
          ),
        );
    } catch {
      setRecoveryError(
        "Recovery response unavailable; inspect the current turn before another action",
      );
    } finally {
      setRecoveryBusy(false);
    }
  }

  if (!snapshot) {
    return (
      <div className="cao-root">
        <HeaderBar title="Agent" />
        {connectionError && (
          <p role="alert">
            Agent connection unavailable. Reopen this view from a trusted host.
          </p>
        )}
        <div className="cao-events-empty" data-testid="agent-loading">
          Loading agent…
        </div>
      </div>
    );
  }

  return (
    <div className="cao-root">
      <HeaderBar title={snapshot.agent_profile ?? snapshot.terminal_id} />
      {connectionError && (
        <p role="alert">
          Agent connection unavailable. Reopen this view from a trusted host.
        </p>
      )}
      <div className="cao-card" data-testid="agent-detail">
        <div className="cao-card-head">
          <span className="cao-card-title">{snapshot.terminal_id}</span>
          <span
            className={`cao-status cao-status-${(snapshot.status ?? "unknown").toLowerCase()}`}
            data-testid="agent-status"
          >
            {snapshot.status ?? "unknown"}
          </span>
        </div>
        <pre className="cao-output" data-testid="agent-output">
          {snapshot.output_tail}
        </pre>
        {snapshot.output_error && (
          <p role="alert" data-testid="agent-output-error">
            {snapshot.output_error.message}
          </p>
        )}
        {snapshot.turn && (
          <div data-testid="agent-turn-state">
            <p>
              Turn: {snapshot.turn.state}
              {snapshot.turn.reason ? ` · ${snapshot.turn.reason}` : ""}
            </p>
            <p>Verification attempts: {snapshot.turn.attempts}</p>
            {snapshot.turn.generation &&
              snapshot.scopes.includes("cao:write") &&
              snapshot.turn.allowed_actions.includes("verify") && (
                <button
                  disabled={recoveryBusy || !app}
                  onClick={() => void recover("verify_turn")}
                >
                  Verify turn
                </button>
              )}
            {snapshot.turn.generation &&
              snapshot.scopes.includes("cao:write") &&
              snapshot.turn.allowed_actions.includes("cancel") && (
                <button
                  disabled={recoveryBusy || !app}
                  onClick={() => void recover("cancel_turn")}
                >
                  Cancel turn
                </button>
              )}
            {recoveryError && <p role="alert">{recoveryError}</p>}
          </div>
        )}
      </div>
      <TaskControl
        onSubmit={handleSubmit}
        target={snapshot.terminal_id}
        scopes={snapshot.scopes}
        activeTurn={Boolean(
          snapshot.turn &&
          ["pending", "verifying", "reconcile", "cancelling"].includes(
            snapshot.turn.state,
          ),
        )}
      />
    </div>
  );
}

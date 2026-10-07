import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { ApiError, TerminalTurn } from "../api";

export function TurnRecoveryPanel({
  terminalId,
  onStateChange,
}: {
  terminalId: string;
  onStateChange?: (state: string | null) => void;
}) {
  const [turn, setTurn] = useState<TerminalTurn | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const actionPending = useRef(false);
  const revision = useRef(0);
  const alive = useRef(true);
  const accept = (value: TerminalTurn) => {
    if (!alive.current || value?.terminal_id !== terminalId) return;
    setTurn(value);
    onStateChange?.(value.state);
  };

  useEffect(() => {
    alive.current = true;
    setTurn(null);
    setError(null);
    setBusy(false);
    actionPending.current = false;
    onStateChange?.(null);
    // Older integrations may provide a partial API object. The real API always has this method.
    if (typeof api.getTerminalTurn !== "function") return;
    let current = true;
    let polling = false;
    const poll = async () => {
      if (actionPending.current || polling) return;
      polling = true;
      const version = ++revision.current;
      try {
        const value = await api.getTerminalTurn(terminalId);
        if (current && version === revision.current) {
          accept(value);
          setError(null);
        }
      } catch (cause) {
        if (current && version === revision.current) {
          setTurn(null);
          onStateChange?.(null);
          const failure = cause as ApiError;
          setError(failure.detail ?? "Turn recovery unavailable");
        }
      } finally {
        polling = false;
      }
    };
    void poll();
    const timer = setInterval(() => {
      void poll();
    }, 3000);
    return () => {
      current = false;
      alive.current = false;
      clearInterval(timer);
      revision.current++;
    };
  }, [terminalId]);

  const recover = async (action: "verify" | "cancel") => {
    if (!turn?.generation || actionPending.current) return;
    const generation = turn.generation;
    actionPending.current = true;
    const version = ++revision.current;
    setBusy(true);
    setError(null);
    try {
      const value = await (action === "verify"
        ? api.verifyTerminalTurn(terminalId, generation)
        : api.cancelTerminalTurn(terminalId, generation));
      if (version === revision.current) accept(value);
    } catch (cause) {
      if (!alive.current || version !== revision.current) return;
      const failure = cause as ApiError;
      setError(
        failure.status === 409
          ? "Turn changed or recovery is blocked. Review the current diagnosis before trying again."
          : (failure.detail ?? "Turn recovery failed"),
      );
      // Refresh identity after a conflict. Never replay an action against a new generation.
      try {
        const value = await api.getTerminalTurn(terminalId);
        if (version === revision.current) accept(value);
      } catch {
        if (version === revision.current) setTurn(null);
      }
    } finally {
      if (version === revision.current) {
        actionPending.current = false;
        if (alive.current) setBusy(false);
      }
    }
  };

  if ((!turn || turn.state === "none") && !error) return null;
  return (
    <section
      aria-label={`Turn recovery: ${terminalId}`}
      className="flex items-center gap-3 px-4 py-2 bg-gray-900 border-b border-gray-700/50 shrink-0 text-xs text-gray-300"
    >
      {turn && turn.state !== "none" && (
        <>
          <span>Turn: {turn.state}</span>
          {turn.reason && <span>{turn.reason}</span>}
          <span>Verification attempts: {turn.attempts}</span>
          {turn.generation &&
            turn.allowed_actions.map((action) => (
              <button
                key={action}
                type="button"
                disabled={busy}
                className="px-2 py-1 bg-gray-700 rounded disabled:opacity-50"
                onClick={() => {
                  void recover(action);
                }}
              >
                {action === "verify" ? "Verify turn" : "Cancel turn"}
              </button>
            ))}
        </>
      )}
      {error && <span role="alert">{error}</span>}
    </section>
  );
}

// Execution map (spec 017, first slice): an observation-only view of agents,
// communication and required waits built from existing read contracts.
// Nothing here starts, resends, cancels or answers work.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import {
  api,
  getNodeEpoch,
  type ApiError,
  type InboxMessage,
  type SessionDetail,
} from "../../api";
import {
  buildExecutionMap,
  processLabel,
  type ExecutionMap,
  type MapEdge,
  type MapNode,
  type SourceResult,
} from "./executionMap";

/** Bounded fan-out: one inbox page per visible agent. */
const MAX_AGENTS_FOR_INBOX = 50;
const INBOX_PAGE = 50;

function failure(e: unknown): { ok: false; reason: "error" | "unavailable"; message: string } {
  const err = e as ApiError;
  if (err?.status === 404 || err?.status === 501) {
    return { ok: false, reason: "unavailable", message: "Not available on this server." };
  }
  if (err?.status === 401 || err?.status === 403) {
    return { ok: false, reason: "unavailable", message: "Not permitted for your access." };
  }
  return { ok: false, reason: "error", message: err?.message || "Could not be read." };
}

async function settle<T>(p: Promise<T>): Promise<SourceResult<T>> {
  try {
    return { ok: true, value: await p };
  } catch (e) {
    return failure(e);
  }
}

export async function loadExecutionMap(): Promise<ExecutionMap> {
  const sessionsResult = await settle(
    (async () => {
      const list = await api.listSessions();
      const details: SessionDetail[] = [];
      for (const s of list) {
        try {
          details.push(await api.getSession(s.name));
        } catch (e) {
          // A session removed between list and detail is simply gone.
          if ((e as ApiError)?.status !== 404) throw e;
        }
      }
      return details;
    })(),
  );
  const inbox: Record<string, SourceResult<InboxMessage[]>> = {};
  if (sessionsResult.ok) {
    const ids = sessionsResult.value
      .flatMap((d) => d.terminals.map((t) => t.id))
      .slice(0, MAX_AGENTS_FOR_INBOX);
    await Promise.all(
      ids.map(async (id) => {
        inbox[id] = await settle(api.getInboxMessages(id, INBOX_PAGE));
      }),
    );
  }
  const waitGraph = await settle(api.getInteractionWaitGraph());
  return buildExecutionMap({ sessions: sessionsResult, inbox, waitGraph });
}

type LoadState =
  | { phase: "loading" }
  | { phase: "ready"; map: ExecutionMap; observedAt: Date }
  | { phase: "failed"; message: string };

function nodeTitle(n: MapNode): string {
  if (n.kind === "agent") return n.profile ? `${n.profile} (${n.provider})` : n.provider;
  return n.label ?? (n.kind === "task" ? "Task" : "Question group");
}

function edgeLabel(e: MapEdge): string {
  if (e.type === "communication") return `message, ${e.deliveryState}`;
  return `${e.required ? "required" : "optional"} wait${e.cycle ? ", cycle confirmed" : ""}`;
}

export function ExecutionMapPanel({
  load = loadExecutionMap,
}: {
  load?: () => Promise<ExecutionMap>;
}) {
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [selected, setSelected] = useState<string | null>(null);
  const generation = useRef(0);

  const refresh = useCallback(async () => {
    const mine = ++generation.current;
    const epoch = getNodeEpoch();
    setState((s) => (s.phase === "ready" ? s : { phase: "loading" }));
    try {
      const map = await load();
      // Discard late answers from a previous load or another fleet node.
      if (mine !== generation.current || epoch !== getNodeEpoch()) return;
      setState({ phase: "ready", map, observedAt: new Date() });
    } catch (e) {
      if (mine !== generation.current || epoch !== getNodeEpoch()) return;
      setState({ phase: "failed", message: (e as Error)?.message || "Could not load." });
    }
  }, [load]);

  useEffect(() => {
    void refresh();
    return () => {
      generation.current++;
    };
  }, [refresh]);

  const map = state.phase === "ready" ? state.map : null;
  const byId = useMemo(
    () => new Map((map?.nodes ?? []).map((n) => [n.id, n])),
    [map],
  );
  const selectedNode = selected ? byId.get(selected) : undefined;
  const selectedEdges = useMemo(
    () => (map && selected ? map.edges.filter((e) => e.from === selected || e.to === selected) : []),
    [map, selected],
  );
  const waits = (map?.nodes ?? []).filter((n) => n.kind !== "agent");

  return (
    <section aria-labelledby="execution-map-title" className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="execution-map-title" className="text-lg font-semibold text-white">
            Execution map
          </h2>
          <p className="text-xs text-gray-400">
            Observation only. Snapshot, not live:{" "}
            {state.phase === "ready" ? (
              <time dateTime={state.observedAt.toISOString()}>
                observed {state.observedAt.toLocaleTimeString()}
              </time>
            ) : (
              "not observed yet"
            )}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          className="flex items-center gap-1.5 rounded-md border border-gray-700 px-3 py-1.5 text-sm text-gray-200 hover:bg-gray-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
        >
          <RefreshCw size={14} aria-hidden="true" /> Refresh
        </button>
      </header>

      {state.phase === "loading" && <p role="status">Loading execution map…</p>}
      {state.phase === "failed" && (
        <p role="alert" className="text-red-300">
          Could not load the execution map: {state.message}
        </p>
      )}
      {map?.failed && (
        <p role="alert" className="text-red-300">
          No source could be read. This is not the same as no activity.
        </p>
      )}
      {map?.empty && (
        <p role="status" className="text-gray-300">
          No agents are running in visible sessions. Launch an agent from Agents to see it here.
        </p>
      )}

      {map && (
        <>
          <details className="rounded-md border border-gray-800 p-3 text-xs text-gray-300">
            <summary className="cursor-pointer">
              Coverage
              {map.coverage.some((c) => c.state !== "complete") ? " — some sources limited" : ""}
            </summary>
            <ul className="mt-2 space-y-1">
              {map.coverage.map((c) => (
                <li key={c.source}>
                  <strong>{c.source}</strong>: {c.state} — {c.note}
                </li>
              ))}
              {map.hiddenEndpointEdges > 0 && (
                <li>{map.hiddenEndpointEdges} relation(s) involve agents outside the visible sessions and are not shown.</li>
              )}
            </ul>
          </details>

          <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <div>
              {map.sessions.map((s) => (
                <div key={s.name} className="mb-3">
                  <h3 className="text-sm font-medium text-gray-300">
                    Session {s.name} <span className="text-gray-500">({s.status})</span>
                  </h3>
                  <ul aria-label={`Agents in session ${s.name}`} className="mt-1 space-y-1">
                    {s.agentIds.map((id) => {
                      const n = byId.get(id);
                      if (!n || n.kind !== "agent") return null;
                      return (
                        <li key={id}>
                          <button
                            type="button"
                            aria-pressed={selected === id}
                            onClick={() => setSelected(id)}
                            className={`w-full rounded-md border px-3 py-2 text-left text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${selected === id ? "border-emerald-500 bg-emerald-500/10" : "border-gray-800 hover:bg-gray-800/60"}`}
                          >
                            <span className="text-white">{nodeTitle(n)}</span>{" "}
                            <span className="text-gray-400">· {processLabel(n.processStatus)}</span>
                            <span className="block font-mono text-xs text-gray-500">{id}</span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ))}
              {waits.length > 0 && (
                <div>
                  <h3 className="text-sm font-medium text-gray-300">Waiting on operator</h3>
                  <ul aria-label="Waits" className="mt-1 space-y-1">
                    {waits.map((n) => (
                      <li key={n.id}>
                        <button
                          type="button"
                          aria-pressed={selected === n.id}
                          onClick={() => setSelected(n.id)}
                          className="w-full rounded-md border border-amber-700/60 px-3 py-2 text-left text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                        >
                          {nodeTitle(n)} <span className="text-gray-400">· {n.state}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                  <p className="mt-1 text-xs text-gray-500">Answer from Questions. The map does not answer.</p>
                </div>
              )}
            </div>

            <aside aria-label="Selection detail" className="rounded-md border border-gray-800 p-3 text-sm">
              {!selectedNode ? (
                <p className="text-gray-400">Select an agent or wait to see its relations.</p>
              ) : (
                <>
                  <h3 className="font-medium text-white">{nodeTitle(selectedNode)}</h3>
                  <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                    <dt className="text-gray-500">Id</dt>
                    <dd className="font-mono">{selectedNode.id}</dd>
                    {selectedNode.kind === "agent" ? (
                      <>
                        <dt className="text-gray-500">Session</dt>
                        <dd>{selectedNode.sessionName}</dd>
                        <dt className="text-gray-500">Process</dt>
                        <dd>{processLabel(selectedNode.processStatus)} (not a task result)</dd>
                        <dt className="text-gray-500">Incarnation</dt>
                        <dd className="font-mono">{selectedNode.incarnationId ?? "unknown"}</dd>
                        <dt className="text-gray-500">Last active</dt>
                        <dd>{selectedNode.lastActive ?? "unknown"}</dd>
                      </>
                    ) : (
                      <>
                        <dt className="text-gray-500">State</dt>
                        <dd>{selectedNode.state}</dd>
                      </>
                    )}
                  </dl>
                  <h4 className="mt-3 text-xs font-medium uppercase text-gray-400">Relations</h4>
                  {selectedEdges.length === 0 ? (
                    <p className="text-xs text-gray-500">No recorded relations in this snapshot.</p>
                  ) : (
                    <table className="mt-1 w-full text-xs">
                      <caption className="sr-only">Relations of the selected element</caption>
                      <thead>
                        <tr className="text-left text-gray-500">
                          <th scope="col">Direction</th>
                          <th scope="col">Other end</th>
                          <th scope="col">Type</th>
                          <th scope="col">Source</th>
                        </tr>
                      </thead>
                      <tbody>
                        {selectedEdges.map((e) => {
                          const outgoing = e.from === selected;
                          const other = byId.get(outgoing ? e.to : e.from);
                          return (
                            <tr key={e.id}>
                              <td>{outgoing ? "to" : "from"}</td>
                              <td>{other ? nodeTitle(other) : "unknown"}</td>
                              <td>{edgeLabel(e)}</td>
                              <td>{e.source}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  )}
                </>
              )}
            </aside>
          </div>
        </>
      )}
    </section>
  );
}

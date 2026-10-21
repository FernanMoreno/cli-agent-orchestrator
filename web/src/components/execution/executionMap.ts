// Pure projection for the execution map (spec 017, first slice).
//
// Composes the read contracts that exist today -- session detail (agents),
// per-terminal inbox (communication) and the 016 interaction wait graph
// (required operator waits) -- into nodes, edges and per-source coverage.
// It never invents relations: delegation edges need `caller_id`, which the
// session projection does not expose yet (see specs/017 plan.md, T010).
import type {
  InboxMessage,
  InteractionWaitGraph,
  SessionDetail,
} from "../../api";

export type SourceResult<T> =
  | { ok: true; value: T }
  | { ok: false; reason: "error" | "unavailable"; message: string };

export interface AgentNode {
  kind: "agent";
  id: string;
  sessionName: string;
  provider: string;
  profile: string | null;
  incarnationId: string | null;
  /** Process status as observed by the server; never a task outcome. */
  processStatus: string;
  lastActive: string | null;
}

export interface WaitNode {
  kind: "task" | "question_group";
  id: string;
  state: string;
  label: string | null;
}

export type MapNode = AgentNode | WaitNode;

export interface MapEdge {
  id: string;
  type: "communication" | "waits_on";
  from: string;
  to: string;
  source: "inbox" | "interaction-wait-graph";
  /** Delivery state for communication; never implies task completion. */
  deliveryState?: InboxMessage["status"];
  required?: boolean;
  cycle?: boolean;
  createdAt?: string | null;
}

export interface SourceCoverage {
  source: "sessions" | "inbox" | "interaction-wait-graph";
  state: "complete" | "partial" | "unavailable" | "error";
  note: string;
}

export interface ExecutionMap {
  sessions: { name: string; status: string; agentIds: string[] }[];
  nodes: MapNode[];
  edges: MapEdge[];
  /** Relations whose other endpoint is outside the visible agents. */
  hiddenEndpointEdges: number;
  coverage: SourceCoverage[];
  /** No visible agents and no visible waits, with sessions loaded. */
  empty: boolean;
  /** Every source failed: nothing can be stated about execution. */
  failed: boolean;
}

export interface ExecutionMapInput {
  sessions: SourceResult<SessionDetail[]>;
  /** Keyed by receiver terminal id. */
  inbox: Record<string, SourceResult<InboxMessage[]>>;
  waitGraph: SourceResult<InteractionWaitGraph>;
}

export function buildExecutionMap(input: ExecutionMapInput): ExecutionMap {
  const coverage: SourceCoverage[] = [];
  const nodes = new Map<string, MapNode>();
  const sessions: ExecutionMap["sessions"] = [];

  if (input.sessions.ok) {
    for (const detail of input.sessions.value) {
      const agentIds: string[] = [];
      for (const t of detail.terminals) {
        const row = t as typeof t & { status?: string | null };
        if (!nodes.has(t.id)) {
          nodes.set(t.id, {
            kind: "agent",
            id: t.id,
            sessionName: detail.session.name,
            provider: t.provider,
            profile: t.agent_profile,
            incarnationId: t.session_incarnation_id ?? null,
            processStatus: row.status ?? "unknown",
            lastActive: t.last_active,
          });
        }
        agentIds.push(t.id);
      }
      sessions.push({
        name: detail.session.name,
        status: detail.session.status,
        agentIds,
      });
    }
    coverage.push({
      source: "sessions",
      state: "complete",
      note: "Agents of visible sessions. Delegation (creator) links are not exposed by the server yet.",
    });
  } else {
    coverage.push({
      source: "sessions",
      state: input.sessions.reason,
      note: input.sessions.message,
    });
  }

  const edges = new Map<string, MapEdge>();
  let hiddenEndpointEdges = 0;
  const inboxEntries = Object.entries(input.inbox);
  let inboxFailures = 0;
  for (const [, result] of inboxEntries) {
    if (!result.ok) {
      inboxFailures++;
      continue;
    }
    for (const m of result.value) {
      const id = `inbox:${m.id}`;
      if (edges.has(id)) continue;
      if (!nodes.has(m.sender_id) || !nodes.has(m.receiver_id)) {
        hiddenEndpointEdges++;
        continue;
      }
      // Message text is deliberately not copied (ambient map, FR-023).
      edges.set(id, {
        id,
        type: "communication",
        from: m.sender_id,
        to: m.receiver_id,
        source: "inbox",
        deliveryState: m.status,
        createdAt: m.created_at,
      });
    }
  }
  if (input.sessions.ok) {
    coverage.push({
      source: "inbox",
      state:
        inboxFailures === 0
          ? "complete"
          : inboxFailures === inboxEntries.length
            ? "error"
            : "partial",
      note:
        inboxFailures === 0
          ? "Recent messages per agent (bounded page)."
          : `${inboxFailures} of ${inboxEntries.length} agent inboxes could not be read.`,
    });
  }

  if (input.waitGraph.ok) {
    for (const n of input.waitGraph.value.nodes) {
      if (!nodes.has(n.id)) {
        nodes.set(n.id, { kind: n.kind, id: n.id, state: n.state, label: n.label });
      }
    }
    for (const e of input.waitGraph.value.edges) {
      const id = `wait:${e.from}->${e.to}`;
      if (edges.has(id)) continue;
      edges.set(id, {
        id,
        type: "waits_on",
        from: e.from,
        to: e.to,
        source: "interaction-wait-graph",
        required: e.required,
        cycle: e.cycle === true,
      });
    }
    coverage.push({
      source: "interaction-wait-graph",
      state: "complete",
      note: "Required waits you own. Not yet linked to a specific agent.",
    });
  } else {
    coverage.push({
      source: "interaction-wait-graph",
      state: input.waitGraph.reason,
      note: input.waitGraph.message,
    });
  }

  const failed =
    !input.sessions.ok && !input.waitGraph.ok;
  return {
    sessions,
    nodes: [...nodes.values()],
    edges: [...edges.values()],
    hiddenEndpointEdges,
    coverage,
    empty: !failed && nodes.size === 0 && input.sessions.ok,
    failed,
  };
}

const PROCESS_LABELS: Record<string, string> = {
  idle: "Idle",
  processing: "Processing",
  completed: "Turn completed",
  waiting_user_answer: "Waiting for answer (detail not available)",
  error: "Error",
  unknown: "Unknown",
};

export function processLabel(status: string): string {
  return PROCESS_LABELS[status.toLowerCase()] ?? status;
}

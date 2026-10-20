import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  BellRing,
  Check,
  ChevronDown,
  Clock3,
  Loader2,
  RefreshCw,
  Send,
  ShieldAlert,
  TerminalSquare,
} from "lucide-react";
import {
  api,
  AnswerReceipt,
  InteractionDetail,
  InteractionDeliveryState,
  InteractionLifecycle,
  InteractionMetadata,
  InteractionResponse,
  InteractionReconcileOutcome,
  InteractionWaitGraph,
  ProviderInteractionCapability,
  getNodeEpoch,
} from "../api";
import { useStore } from "../store";

const PAGE_SIZE = 100;
const SNAPSHOT_REFRESH_MS = 1000;
const DETAIL_REFRESH_MS = 1000;

type LifecycleFilter = "" | "attention" | InteractionLifecycle;

const LIFECYCLE_FILTERS: { value: LifecycleFilter; label: string }[] = [
  { value: "attention", label: "Needs attention" },
  { value: "open", label: "Open" },
  { value: "", label: "All states" },
  { value: "answered", label: "Answered" },
  { value: "expired", label: "Expired" },
  { value: "cancelled", label: "Cancelled" },
  { value: "stale", label: "Stale" },
  { value: "manual_only", label: "Manual only" },
  { value: "reconciled_external", label: "Reconciled outside CAO" },
];

function lifecycleStatuses(filter: LifecycleFilter): InteractionLifecycle[] | undefined {
  if (filter === "attention") return ["open", "manual_only"];
  return filter ? [filter] : undefined;
}

function matchesLifecycleFilter(
  lifecycle: InteractionLifecycle,
  filter: LifecycleFilter,
) {
  if (!filter) return true;
  if (filter === "attention") return lifecycle === "open" || lifecycle === "manual_only";
  return lifecycle === filter;
}

const LIFECYCLE_LABELS: Record<InteractionLifecycle, string> = {
  open: "Open",
  answered: "Answered",
  expired: "Expired",
  cancelled: "Cancelled",
  stale: "Stale",
  manual_only: "Manual handling",
  reconciled_external: "Reconciled outside CAO",
};

const DELIVERY_LABELS: Record<InteractionDeliveryState, string> = {
  accepted: "Answer accepted; delivery is not confirmed yet",
  prepared: "Answer prepared for delivery",
  claimed: "Delivery started; outcome is not confirmed yet",
  returned_to_mcp: "Returned to agent tool; delivery is confirmed",
  agent_acknowledged: "Agent acknowledgement observed",
  confirmed: "Provider delivery confirmed",
  uncertain: "Delivery outcome is uncertain",
  no_effect: "No provider effect was confirmed",
  reconciled: "Delivery reconciled",
};

type PanelError = {
  status?: number;
  kind?: string;
};

type AnswerAttempt = {
  questionId: string;
  version: number;
  idempotencyKey: string;
  response: InteractionResponse;
  state: "submitting" | "uncertain" | "conflict";
};

interface QuestionsPanelProps {
  onOpenManualTerminal: (terminalId: string, incarnationId: string) => void;
}

function asPanelError(error: unknown): PanelError {
  if (!error || typeof error !== "object") return {};
  const value = error as { status?: unknown; kind?: unknown };
  return {
    status: typeof value.status === "number" ? value.status : undefined,
    kind: typeof value.kind === "string" ? value.kind : undefined,
  };
}

function errorMessage(error: unknown, operation: "list" | "detail" | "answer" | "cancel" | "reconcile" | "claim" | "delegation") {
  const { status } = asPanelError(error);
  if (status === 401)
    return "Your session is no longer authorized. Sign in again before continuing.";
  if (status === 403) {
    if (operation === "claim") return "This account cannot claim content access. CAO admin permission is required.";
    if (operation === "delegation") return "Only the current content owner can approve or revoke this temporary delegation.";
    return "Metadata is visible, but question content is restricted for this account. CAO did not return the prompt or answer.";
  }
  if (status === 404)
    return "This request is no longer visible to your account.";
  if (status === 409)
    return "The question changed while you were reviewing it. Refresh it before taking another action.";
  if (status === 410)
    return "This request is stale or expired and can no longer be answered.";
  if (status === 422) {
    if (operation === "answer")
      return "CAO rejected this response. Check the current question and do not include passwords or access codes.";
    return "CAO rejected this action. Review the current request and try again.";
  }
  if (status === 429)
    return "The interaction service is at capacity. Existing questions remain available; refresh before trying again.";
  if (operation === "answer")
    return "The answer result could not be confirmed. CAO will not resend it automatically.";
  if (operation === "cancel")
    return "The cancellation result could not be confirmed. Refresh the request before trying again.";
  if (operation === "reconcile")
    return "The reconciliation result could not be confirmed. Refresh the request before trying again.";
  if (operation === "claim")
    return "Content access could not be confirmed. Refresh the request before trying again.";
  if (operation === "delegation")
    return "The delegation result could not be confirmed. Refresh the request before trying again.";
  if (operation === "detail")
    return "Question content could not be loaded. The metadata remains visible.";
  return "Questions could not be refreshed. Try again when CAO is available.";
}

function localErrorKind(error: unknown) {
  return asPanelError(error).kind;
}

function makeIdempotencyKey(): string {
  try {
    if (typeof crypto !== "undefined" && "randomUUID" in crypto)
      return crypto.randomUUID();
  } catch {
    // Fall back to a non-secret per-intent identifier supported by older browsers.
  }
  return `cao-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
}

function mergePage(
  current: InteractionMetadata[],
  incoming: InteractionMetadata[],
  append: boolean,
  selectedId: string | null,
  statusFilter: LifecycleFilter,
): InteractionMetadata[] {
  const byId = new Map<string, InteractionMetadata>();
  if (append) current.forEach((item) => byId.set(item.question_id, item));
  incoming.forEach((item) => byId.set(item.question_id, item));
  const incomingIds = new Set(incoming.map((item) => item.question_id));
  const keepExisting = current.filter(
    (item) =>
      item.question_id === selectedId ||
      incomingIds.has(item.question_id) ||
      matchesLifecycleFilter(item.lifecycle, statusFilter),
  );
  keepExisting.forEach((item) => {
    if (!byId.has(item.question_id)) byId.set(item.question_id, item);
  });
  return [...byId.values()].sort((a, b) => b.created_at.localeCompare(a.created_at));
}

function formatTime(value: string | null) {
  if (!value) return "No expiry set";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function lifecycleTone(status: InteractionLifecycle) {
  if (status === "open") return "bg-amber-400/10 text-amber-300";
  if (status === "answered" || status === "reconciled_external")
    return "bg-emerald-400/10 text-emerald-300";
  if (status === "stale" || status === "expired" || status === "cancelled")
    return "bg-gray-700 text-gray-300";
  return "bg-violet-400/10 text-violet-300";
}

function LifecycleBadge({ status }: { status: InteractionLifecycle }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${lifecycleTone(status)}`}>
      {LIFECYCLE_LABELS[status]}
    </span>
  );
}

function kindLabel(kind: InteractionMetadata["kind"]) {
  const labels: Record<InteractionMetadata["kind"], string> = {
    text: "Text response",
    single_choice: "Choose one option",
    multi_choice: "Choose multiple options",
    choice_or_text: "Choose an option or write a response",
    approval: "Declared approval choice",
    credential: "Credential prompt",
    native_unknown: "Unrecognized provider dialog",
  };
  return labels[kind];
}

function manualReasonLabel(reason: string) {
  if (reason === "native_dialog_manual_action") {
    return "Native provider dialogs require manual action in the exact terminal.";
  }
  return reason;
}

function deliveryDescription(state: InteractionDeliveryState | null) {
  return state === null ? "No answer / no delivery yet" : DELIVERY_LABELS[state];
}

function GroupProgress({ item }: { item: InteractionMetadata }) {
  if (!item.group_id || !item.group_progress) return null;
  const progress = item.group_progress;
  return (
    <p className="mt-2 text-xs text-gray-400">
      {progress.required_answered} of {progress.required_total} required answers complete
      <span aria-hidden="true"> · </span>{progress.total} questions in group
      <span className="sr-only">. Optional questions do not block continuation.</span>
    </p>
  );
}

function sourceTitle(item: InteractionMetadata) {
  return `${item.source.provider} · terminal ${item.source.terminal_id} · question ${item.question_id}`;
}

function InteractionCard({
  item,
  selected,
  onSelect,
}: {
  item: InteractionMetadata;
  selected: boolean;
  onSelect: () => void;
}) {
  const task = item.task?.work_item_id ?? item.task?.attempt_id ?? item.task?.session_name;
  return (
    <li>
      <button
        type="button"
        aria-pressed={selected}
        aria-label={`${item.source.provider} terminal ${item.source.terminal_id}, question ${item.question_id}, ${item.required ? "required" : "optional"}`}
        onClick={onSelect}
        className={`w-full rounded-xl border px-4 py-3 text-left transition-colors ${
          selected
            ? "border-emerald-500/70 bg-emerald-950/40"
            : "border-gray-800 bg-gray-900/60 hover:border-gray-700 hover:bg-gray-900"
        }`}
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-sm font-medium text-white">{item.source.provider}</span>
          <LifecycleBadge status={item.lifecycle} />
        </div>
        <p className="mt-1 text-xs text-gray-400">Terminal {item.source.terminal_id}</p>
        <p className="mt-1 break-all text-[11px] font-mono text-gray-600">Question {item.question_id}</p>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-gray-400">
          <span>{kindLabel(item.kind)}</span>
          <span aria-hidden="true">·</span>
          <span className={item.required ? "text-amber-300" : "text-gray-400"}>
            {item.required ? "Required" : "Optional"}
          </span>
        </div>
        {task && <p className="mt-2 truncate text-xs text-gray-500">Task/session: {task}</p>}
        <p className="mt-1 truncate text-xs text-gray-500">
          Recipient: {item.recipient.label ?? item.recipient.kind}
        </p>
        <GroupProgress item={item} />
      </button>
    </li>
  );
}

function HistorySection({ questionId }: { questionId: string }) {
  const [events, setEvents] = useState<Awaited<ReturnType<typeof api.getInteractionHistory>>["events"]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const generation = useRef(0);

  const toggle = async () => {
    const nextOpen = !open;
    setOpen(nextOpen);
    if (!nextOpen) return;
    const nextGeneration = ++generation.current;
    setLoading(true);
    setError(null);
    try {
      const result = await api.getInteractionHistory(questionId);
      if (generation.current !== nextGeneration) return;
      setEvents(result.events);
    } catch (cause) {
      if (generation.current !== nextGeneration) return;
      setError(errorMessage(cause, "detail"));
    } finally {
      if (generation.current === nextGeneration) setLoading(false);
    }
  };

  return (
    <section className="mt-4 rounded-xl border border-gray-800 bg-gray-900/50">
      <button
        type="button"
        aria-expanded={open}
        aria-label={open ? "Hide interaction history" : "Show interaction history"}
        onClick={() => void toggle()}
        className="flex w-full items-center justify-between px-4 py-3 text-left text-sm font-medium text-gray-200"
      >
        <span>Interaction history</span>
        <ChevronDown size={16} className={open ? "rotate-180 transition-transform" : "transition-transform"} />
      </button>
      {open && (
        <div className="border-t border-gray-800 px-4 py-3">
          {loading && <p role="status" className="text-xs text-gray-500">Loading history…</p>}
          {error && <p role="alert" className="text-xs text-amber-300">{error}</p>}
          {!loading && !error && events.length === 0 && (
            <p className="text-xs text-gray-500">No recorded transitions yet.</p>
          )}
          <ol className="space-y-3">
            {events.map((event) => {
              const allowed = ["reason_code", "delivery_state", "lifecycle", "recipient_kind", "version"];
              const summary = allowed
                .filter((key) => ["string", "number", "boolean"].includes(typeof event.safe_metadata[key]))
                .map((key) => `${key.replace(/_/g, " ")}: ${String(event.safe_metadata[key])}`)
                .join(" · ");
              return (
                <li key={`${event.sequence}-${event.event_type}`} className="border-l border-gray-700 pl-3">
                <p className="text-sm text-gray-200">{event.event_type.replace(/_/g, " ")}</p>
                  <p className="mt-0.5 text-xs text-gray-500">
                    {formatTime(event.occurred_at)} · {event.actor_label ?? event.actor_kind}
                  </p>
                  {summary && <p className="mt-1 text-xs text-gray-400">{summary}</p>}
                </li>
              );
            })}
          </ol>
        </div>
      )}
    </section>
  );
}

function WaitGraphSection({
  graph,
  loading,
  error,
  onRefresh,
  onSelectGroup,
}: {
  graph: InteractionWaitGraph | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  onSelectGroup: (groupId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const labels = new Map((graph?.nodes ?? []).map((node) => [node.id, node.label ?? node.id]));
  const hasCycles = graph?.edges.some((edge) => edge.cycle) ?? false;
  return (
    <section className="rounded-xl border border-gray-800 bg-gray-900/50">
      <button
        type="button"
        aria-expanded={open}
        aria-label={open ? "Hide wait graph" : "Show wait graph"}
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center justify-between px-4 py-3 text-left text-sm font-medium text-gray-200"
      >
        <span className="flex items-center gap-2"><Clock3 size={15} /> Work waiting on questions</span>
        <ChevronDown size={16} className={open ? "rotate-180 transition-transform" : "transition-transform"} />
      </button>
      {open && (
        <div role="region" aria-label="Wait graph" className="border-t border-gray-800 px-4 py-3">
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs text-gray-500">Required questions pause only the work that depends on them.</p>
            <button type="button" onClick={onRefresh} className="shrink-0 text-xs text-emerald-300 hover:text-emerald-200">
              Refresh graph
            </button>
          </div>
          {loading && <p role="status" className="mt-3 text-xs text-gray-500">Loading wait graph…</p>}
          {error && <p role="alert" className="mt-3 text-xs text-amber-300">{error}</p>}
          {!loading && !error && graph?.nodes.length === 0 && (
            <p className="mt-3 text-xs text-gray-500">No work is currently waiting for a question.</p>
          )}
          {hasCycles && (
            <p className="mt-3 flex items-center gap-2 text-sm text-amber-300">
              <AlertTriangle size={15} /> Cycle detected. Review the listed participants; CAO will not invent an answer to release the wait.
            </p>
          )}
          <ul className="mt-3 space-y-2">
            {(graph?.edges ?? []).map((edge, index) => {
              const groupNode = graph?.nodes.find(
                (node) => node.kind === "question_group" && (node.id === edge.from || node.id === edge.to),
              );
              const from = labels.get(edge.from) ?? edge.from;
              const to = labels.get(edge.to) ?? edge.to;
              return (
                <li key={`${edge.from}-${edge.to}-${index}`} className="rounded-lg bg-gray-950/70 px-3 py-2 text-xs text-gray-300">
                  <span>{from} → {to}</span>
                  <span className="ml-2 text-gray-500">{edge.required ? "required wait" : "optional question"}</span>
                  {edge.cycle && <span className="ml-2 font-medium text-amber-300">Cycle detected</span>}
                  {groupNode && (
                    <button type="button" onClick={() => onSelectGroup(groupNode.id)} className="ml-3 text-emerald-300 hover:text-emerald-200">
                      View group questions
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </section>
  );
}

function CapabilitySection({
  capabilities,
  loading,
  error,
}: {
  capabilities: ProviderInteractionCapability[];
  loading: boolean;
  error: string | null;
}) {
  const [open, setOpen] = useState(false);
  return (
    <section className="rounded-xl border border-gray-800 bg-gray-900/50">
      <button
        type="button"
        aria-expanded={open}
        aria-label={open ? "Hide provider support" : "Provider support"}
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center justify-between px-4 py-3 text-left text-sm font-medium text-gray-200"
      >
        <span>Provider support</span>
        <ChevronDown size={16} className={open ? "rotate-180 transition-transform" : "transition-transform"} />
      </button>
      {open && (
        <div className="border-t border-gray-800 px-4 py-3">
          {loading && <p role="status" className="text-xs text-gray-500">Loading provider support…</p>}
          {error && <p role="alert" className="text-xs text-amber-300">{error}</p>}
          {!loading && !error && capabilities.length === 0 && (
            <p className="text-xs leading-relaxed text-gray-400">
              No provider support records are available. Explicit questions require a terminal with the source-bound CAO MCP channel configured. This view has no automatic native-dialog response; handle provider dialogs in the agent terminal.
            </p>
          )}
          <ul className="grid gap-3 md:grid-cols-2">
            {capabilities.map((item) => (
              <li key={`${item.provider}-${item.provider_version ?? "unknown"}-${item.execution_mode}`}>
                <article aria-label={`${item.provider} ${item.execution_mode}`} className="rounded-lg border border-gray-800 bg-gray-950/60 p-3">
                  <h3 className="text-sm font-semibold text-white">
                    {item.provider}{" "}
                    <span className="font-normal text-gray-500">
                      {item.provider_version ?? "version not reported"} · {item.execution_mode}
                    </span>
                  </h3>
                  <ul className="mt-2 space-y-1 text-xs text-gray-300">
                    <li>Explicit questions: {item.emits_explicit ? "supported" : "not supported"}</li>
                    <li>Native dialog detection: {item.detects_native ? "supported" : "not supported"}</li>
                    <li>
                      Native response: {item.responds_native.length
                        ? item.responds_native.join(", ")
                        : "manual only"}
                    </li>
                  </ul>
                  {item.manual_reason && <p className="mt-2 text-xs text-gray-400">{manualReasonLabel(item.manual_reason)}</p>}
                  {item.required_mcp_tools?.length ? (
                    <p className="mt-2 text-[11px] text-gray-500">Required tools: {item.required_mcp_tools.join(", ")}</p>
                  ) : null}
                </article>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

export function QuestionsPanel({ onOpenManualTerminal }: QuestionsPanelProps) {
  const nodeEpoch = useStore((state) => state.nodeEpoch);
  const [rows, setRows] = useState<InteractionMetadata[]>([]);
  const [dataEpoch, setDataEpoch] = useState(() => getNodeEpoch());
  const [statusFilter, setStatusFilter] = useState<LifecycleFilter>("attention");
  const [agentFilter, setAgentFilter] = useState("");
  const [taskFilter, setTaskFilter] = useState("");
  const [recipientFilter, setRecipientFilter] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingList, setLoadingList] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<InteractionDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailErrorStatus, setDetailErrorStatus] = useState<number | undefined>();
  const [detailErrorKind, setDetailErrorKind] = useState<string | undefined>();
  const [detailRefreshCounter, setDetailRefreshCounter] = useState(0);
  const [listRefreshCounter, setListRefreshCounter] = useState(0);
  const [textAnswer, setTextAnswer] = useState("");
  const [selectedOptions, setSelectedOptions] = useState<string[]>([]);
  const [freeTextMode, setFreeTextMode] = useState(false);
  const [answerAttempt, setAnswerAttempt] = useState<AnswerAttempt | null>(null);
  const [answerMessage, setAnswerMessage] = useState<string | null>(null);
  const [operationMessage, setOperationMessage] = useState<string | null>(null);
  const [pendingOperation, setPendingOperation] = useState<"answer" | "cancel" | "reconcile" | "claim" | "delegation" | null>(null);
  const [cancelFormOpen, setCancelFormOpen] = useState(false);
  const [cancelReason, setCancelReason] = useState("");
  const [reconcileFormOpen, setReconcileFormOpen] = useState(false);
  const [reconcileOutcome, setReconcileOutcome] = useState<InteractionReconcileOutcome>("answered_outside_cao");
  const [evidenceCode, setEvidenceCode] = useState("");
  const [evidenceNote, setEvidenceNote] = useState("");
  const [contentOwnerReason, setContentOwnerReason] = useState("");
  const [contentOwnerError, setContentOwnerError] = useState<string | null>(null);
  const [delegationFormOpen, setDelegationFormOpen] = useState(false);
  const [delegationReason, setDelegationReason] = useState("");
  const [delegationExpiry, setDelegationExpiry] = useState(60);
  const [waitGraph, setWaitGraph] = useState<InteractionWaitGraph | null>(null);
  const [waitGraphLoading, setWaitGraphLoading] = useState(false);
  const [waitGraphError, setWaitGraphError] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<ProviderInteractionCapability[]>([]);
  const [capabilitiesLoading, setCapabilitiesLoading] = useState(true);
  const [capabilitiesError, setCapabilitiesError] = useState<string | null>(null);
  const listController = useRef<AbortController | null>(null);
  const refreshListRef = useRef<() => void>(() => {});
  const selectedIdRef = useRef<string | null>(null);
  const listEpochRef = useRef<number | null>(null);
  const lastAnswerAttempt = useRef<AnswerAttempt | null>(null);

  selectedIdRef.current = selectedId;

  const currentEpoch = dataEpoch === nodeEpoch && dataEpoch === getNodeEpoch();
  const selectedMetadata = currentEpoch
    ? rows.find((item) => item.question_id === selectedId) ?? null
    : null;
  const selectedVersion = selectedMetadata?.version;
  const currentDetail =
    currentEpoch &&
    detail?.question_id === selectedId &&
    detail.version === selectedVersion
      ? detail
      : null;

  useEffect(() => {
    const epoch = nodeEpoch;
    const controller = new AbortController();
    let alive = true;
    let requestActive = false;
    const nodeChanged = listEpochRef.current !== epoch;
    listEpochRef.current = epoch;
    listController.current?.abort();
    listController.current = controller;
    if (nodeChanged) {
      selectedIdRef.current = null;
      setDataEpoch(epoch);
      setRows([]);
      setSelectedId(null);
      setDetail(null);
      setDetailError(null);
    }
    setCursor(null);
    setListError(null);
    setLoadingList(true);

    const loadFirstPage = async () => {
      if (requestActive) return;
      requestActive = true;
      try {
        const result = await api.listInteractions(
          {
            status: lifecycleStatuses(statusFilter),
            limit: PAGE_SIZE,
          },
          controller.signal,
        );
        if (!alive || epoch !== getNodeEpoch()) return;
        setRows((previous) =>
          mergePage(previous, result.items, false, selectedIdRef.current, statusFilter),
        );
        setCursor(result.next_cursor);
        setListError(null);
        setDataEpoch(epoch);
      } catch (error) {
        if (!alive || controller.signal.aborted || epoch !== getNodeEpoch()) return;
        setListError(errorMessage(error, "list"));
      } finally {
        if (alive && epoch === getNodeEpoch()) setLoadingList(false);
        requestActive = false;
      }
    };
    refreshListRef.current = () => void loadFirstPage();
    void loadFirstPage();
    const timer = window.setInterval(() => void loadFirstPage(), SNAPSHOT_REFRESH_MS);
    return () => {
      alive = false;
      controller.abort();
      window.clearInterval(timer);
      if (listController.current === controller) listController.current = null;
    };
  }, [nodeEpoch, statusFilter, listRefreshCounter]);

  useEffect(() => {
    if (!selectedId || selectedVersion == null || !currentEpoch) return;
    const epoch = nodeEpoch;
    const version = selectedVersion;
    const controller = new AbortController();
    let alive = true;
    let inFlight = false;
    setDetail(null);
    setDetailError(null);
    setDetailErrorStatus(undefined);
    setDetailErrorKind(undefined);
    setLoadingDetail(true);
    setAnswerMessage(null);
    setOperationMessage(null);

    const loadDetail = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const result = await api.getInteractionDetail(
          selectedId,
          version,
          controller.signal,
        );
        if (!alive || epoch !== getNodeEpoch()) return;
        if (result.version !== version || result.question_id !== selectedId) {
          setDetail(null);
          setDetailError("The question version changed. Refresh the list and review the current question.");
          setDetailErrorStatus(410);
          setListRefreshCounter((value) => value + 1);
          return;
        }
        setDetail(result);
        setDetailError(null);
        setDetailErrorStatus(undefined);
        setDetailErrorKind(undefined);
      } catch (error) {
        if (!alive || controller.signal.aborted || epoch !== getNodeEpoch()) return;
        const problem = asPanelError(error);
        setDetail(null);
        setDetailError(errorMessage(error, "detail"));
        setDetailErrorStatus(problem.status);
        setDetailErrorKind(problem.kind);
      } finally {
        if (alive && epoch === getNodeEpoch()) setLoadingDetail(false);
        inFlight = false;
      }
    };
    void loadDetail();
    const timer = window.setInterval(() => void loadDetail(), DETAIL_REFRESH_MS);
    return () => {
      alive = false;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [selectedId, selectedVersion, nodeEpoch, currentEpoch, detailRefreshCounter]);

  useEffect(() => {
    if (!currentEpoch || !selectedId || loadingList || listError) return;
    if (!rows.some((item) => item.question_id === selectedId)) {
      setSelectedId(null);
      setDetail(null);
    }
  }, [currentEpoch, selectedId, rows, loadingList, listError]);

  useEffect(() => {
    let alive = true;
    const epoch = nodeEpoch;
    setCapabilitiesLoading(true);
    api
      .getInteractionCapabilities()
      .then((result) => {
        if (!alive || epoch !== getNodeEpoch()) return;
        setCapabilities(result.items);
        setCapabilitiesError(null);
      })
      .catch((error) => {
        if (!alive || epoch !== getNodeEpoch()) return;
        setCapabilitiesError(errorMessage(error, "list"));
      })
      .finally(() => {
        if (alive && epoch === getNodeEpoch()) setCapabilitiesLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [nodeEpoch]);

  const refreshWaitGraph = async () => {
    const epoch = getNodeEpoch();
    setWaitGraphLoading(true);
    setWaitGraphError(null);
    try {
      const result = await api.getInteractionWaitGraph();
      if (epoch !== getNodeEpoch()) return;
      setWaitGraph(result);
    } catch (error) {
      if (epoch !== getNodeEpoch()) return;
      setWaitGraphError(errorMessage(error, "list"));
    } finally {
      if (epoch === getNodeEpoch()) setWaitGraphLoading(false);
    }
  };

  useEffect(() => {
    void refreshWaitGraph();
    // Wait graph is refreshed explicitly by the operator and after mutations.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeEpoch]);

  const selectQuestion = (questionId: string) => {
    setSelectedId(questionId);
    setTextAnswer("");
    setSelectedOptions([]);
    setFreeTextMode(false);
    setAnswerAttempt(null);
    lastAnswerAttempt.current = null;
    setAnswerMessage(null);
    setOperationMessage(null);
    setCancelFormOpen(false);
    setReconcileFormOpen(false);
    setDelegationFormOpen(false);
    setDelegationReason("");
  };

  const loadMore = async () => {
    if (!cursor || loadingMore) return;
    const epoch = getNodeEpoch();
    const controller = new AbortController();
    setLoadingMore(true);
    try {
      const result = await api.listInteractions(
        {
          status: lifecycleStatuses(statusFilter),
          cursor,
          limit: PAGE_SIZE,
        },
        controller.signal,
      );
      if (epoch !== getNodeEpoch()) return;
      setRows((previous) => mergePage(previous, result.items, true, selectedIdRef.current, statusFilter));
      setCursor(result.next_cursor);
      setListError(null);
    } catch (error) {
      if (epoch !== getNodeEpoch()) return;
      setListError(errorMessage(error, "list"));
    } finally {
      if (epoch === getNodeEpoch()) setLoadingMore(false);
    }
  };

  const visibleRows = currentEpoch
    ? rows.filter((item) => {
        const agent = `${item.source.provider} ${item.source.provider_version ?? ""} ${item.source.terminal_id} ${item.source.execution_mode}`.toLowerCase();
        const task = `${item.task?.work_item_id ?? ""} ${item.task?.attempt_id ?? ""} ${item.task?.session_name ?? ""}`.toLowerCase();
        const recipient = `${item.recipient.kind} ${item.recipient.label ?? ""} ${item.recipient.reason}`.toLowerCase();
        return (
          (!agentFilter.trim() || agent.includes(agentFilter.trim().toLowerCase())) &&
          (!taskFilter.trim() || task.includes(taskFilter.trim().toLowerCase())) &&
          (!recipientFilter.trim() || recipient.includes(recipientFilter.trim().toLowerCase())) &&
          (matchesLifecycleFilter(item.lifecycle, statusFilter) || item.question_id === selectedId)
        );
      })
    : [];

  const loadQuestionDetailNow = async () => {
    if (!selectedId || selectedVersion == null) return;
    setDetailRefreshCounter((value) => value + 1);
    setListRefreshCounter((value) => value + 1);
  };

  const answerable = Boolean(
    currentDetail &&
      currentDetail.lifecycle === "open" &&
      currentDetail.can_answer &&
      currentDetail.prompt !== null &&
      currentDetail.kind !== "native_unknown" &&
      currentDetail.kind !== "credential" &&
      !currentDetail.answer_receipt,
  );
  const manualOnly = Boolean(
    currentDetail &&
      (currentDetail.lifecycle === "manual_only" ||
        currentDetail.kind === "credential" ||
        currentDetail.kind === "native_unknown" ||
        currentDetail.origin === "native" && !currentDetail.can_answer),
  );
  const explicitCredential = Boolean(
    currentDetail?.origin === "explicit" && currentDetail.kind === "credential",
  );
  const delegationCandidate = currentDetail?.delegation_candidate ?? null;
  const currentDelegation = currentDetail?.delegation ?? null;
  const delegationAllowed = Boolean(
    currentDetail &&
      currentDetail.lifecycle === "open" &&
      delegationCandidate &&
      currentDetail.origin === "explicit" &&
      currentDetail.kind !== "approval" &&
      currentDetail.kind !== "credential" &&
      currentDetail.kind !== "native_unknown",
  );
  const acceptedReceipt = currentDetail?.answer_receipt ?? null;
  const pendingAnswerAttempt = answerAttempt?.questionId === selectedId &&
    answerAttempt.version === selectedVersion ? answerAttempt : null;

  const buildCurrentResponse = (): InteractionResponse | null => {
    if (!currentDetail) return null;
    if (currentDetail.kind === "text") {
      if (!textAnswer.trim() || textAnswer.length > 4096) return null;
      return { kind: "text", text: textAnswer };
    }
    if (currentDetail.kind === "single_choice" || currentDetail.kind === "approval") {
      if (selectedOptions.length !== 1) return null;
      return { kind: "options", option_ids: selectedOptions };
    }
    if (currentDetail.kind === "multi_choice") {
      if (selectedOptions.length === 0) return null;
      return { kind: "options", option_ids: selectedOptions };
    }
    if (currentDetail.kind === "choice_or_text") {
      if (freeTextMode) {
        if (!textAnswer.trim() || textAnswer.length > 4096) return null;
        return { kind: "free_text", text: textAnswer };
      }
      if (selectedOptions.length !== 1) return null;
      return { kind: "options", option_ids: selectedOptions };
    }
    return null;
  };

  const submitAnswer = async (retry?: AnswerAttempt) => {
    if (!currentDetail || pendingOperation) return;
    const response = retry?.response ?? buildCurrentResponse();
    if (!response) return;
    const serialized = JSON.stringify(response);
    const previous = lastAnswerAttempt.current;
    const attempt = retry ?? (
      previous &&
      previous.questionId === currentDetail.question_id &&
      previous.version === currentDetail.version &&
      JSON.stringify(previous.response) === serialized
        ? previous
        : {
            questionId: currentDetail.question_id,
            version: currentDetail.version,
            idempotencyKey: makeIdempotencyKey(),
            response,
            state: "submitting" as const,
          }
    );
    const submitted: AnswerAttempt = { ...attempt, state: "submitting" };
    lastAnswerAttempt.current = submitted;
    setAnswerAttempt(submitted);
    setPendingOperation("answer");
    setAnswerMessage(null);
    setOperationMessage(null);
    try {
      const receipt = await api.answerInteraction(
        currentDetail.question_id,
        currentDetail.version,
        submitted.idempotencyKey,
        submitted.response,
      );
      if (getNodeEpoch() !== nodeEpoch) return;
      const validReceipt: AnswerReceipt = receipt;
      setDetail((old) =>
        old && old.question_id === validReceipt.question_id && old.version === validReceipt.version
          ? {
              ...old,
              lifecycle: validReceipt.lifecycle,
              delivery_state: validReceipt.delivery_state,
              answer_receipt: validReceipt,
            }
          : old,
      );
      setRows((old) =>
        old.map((item) =>
          item.question_id === validReceipt.question_id
            ? { ...item, version: validReceipt.version, lifecycle: validReceipt.lifecycle, delivery_state: validReceipt.delivery_state }
            : item,
        ),
      );
      setAnswerAttempt(null);
      lastAnswerAttempt.current = null;
      setTextAnswer("");
      setSelectedOptions([]);
      setFreeTextMode(false);
      setAnswerMessage(null);
      setOperationMessage("Answer recorded. Delivery and agent acceptance are shown separately below.");
      refreshListRef.current();
      void refreshWaitGraph();
    } catch (error) {
      if (getNodeEpoch() !== nodeEpoch) return;
      const problem = asPanelError(error);
      const message = errorMessage(error, "answer");
      setAnswerMessage(message);
      if (problem.status == null) {
        const uncertain: AnswerAttempt = { ...submitted, state: "uncertain" };
        lastAnswerAttempt.current = uncertain;
        setAnswerAttempt(uncertain);
      } else if (problem.status === 409 || problem.status === 410) {
        const conflict: AnswerAttempt = { ...submitted, state: "conflict" };
        lastAnswerAttempt.current = conflict;
        setAnswerAttempt(conflict);
        setListRefreshCounter((value) => value + 1);
        setDetailRefreshCounter((value) => value + 1);
      } else {
        setAnswerAttempt(null);
        lastAnswerAttempt.current = null;
        if (problem.status === 403) {
          setDetail(null);
          setDetailError(message);
          setDetailErrorStatus(403);
          setDetailErrorKind(problem.kind);
        }
        if (problem.status === 422) setAnswerMessage(message);
      }
      // Read current state after every failed mutation. This is safe even if the
      // POST response was lost; it never repeats an answer effect.
      setDetailRefreshCounter((value) => value + 1);
      refreshListRef.current();
    } finally {
      if (getNodeEpoch() === nodeEpoch) setPendingOperation(null);
    }
  };

  const confirmCancel = async () => {
    if (!currentDetail || !cancelReason.trim() || pendingOperation) return;
    setPendingOperation("cancel");
    setOperationMessage(null);
    setAnswerMessage(null);
    try {
      await api.cancelInteraction(currentDetail.question_id, currentDetail.version, cancelReason.trim());
      if (getNodeEpoch() !== nodeEpoch) return;
      setDetail((old) => old ? { ...old, lifecycle: "cancelled" } : old);
      setRows((old) => old.map((item) => item.question_id === currentDetail.question_id
        ? { ...item, lifecycle: "cancelled" }
        : item));
      setCancelFormOpen(false);
      setCancelReason("");
      setOperationMessage("Question cancelled. No answer was sent.");
      refreshListRef.current();
      void refreshWaitGraph();
    } catch (error) {
      if (getNodeEpoch() !== nodeEpoch) return;
      setOperationMessage(errorMessage(error, "cancel"));
      setDetailRefreshCounter((value) => value + 1);
      setListRefreshCounter((value) => value + 1);
    } finally {
      if (getNodeEpoch() === nodeEpoch) setPendingOperation(null);
    }
  };

  const submitReconciliation = async () => {
    if (!currentDetail || !evidenceCode.trim() || pendingOperation) return;
    setPendingOperation("reconcile");
    setOperationMessage(null);
    try {
      await api.reconcileInteraction(currentDetail.question_id, currentDetail.version, {
        outcome: reconcileOutcome,
        evidence_code: evidenceCode.trim(),
        ...(evidenceNote.trim() ? { note: evidenceNote.trim() } : {}),
      });
      if (getNodeEpoch() !== nodeEpoch) return;
      setReconcileFormOpen(false);
      setEvidenceCode("");
      setEvidenceNote("");
      setOperationMessage("Reconciliation recorded. CAO did not resend the answer.");
      setDetailRefreshCounter((value) => value + 1);
      setListRefreshCounter((value) => value + 1);
      void refreshWaitGraph();
    } catch (error) {
      if (getNodeEpoch() !== nodeEpoch) return;
      setOperationMessage(errorMessage(error, "reconcile"));
      setDetailRefreshCounter((value) => value + 1);
      setListRefreshCounter((value) => value + 1);
    } finally {
      if (getNodeEpoch() === nodeEpoch) setPendingOperation(null);
    }
  };

  const claimContentAccess = async () => {
    if (!selectedMetadata || !contentOwnerReason.trim() || pendingOperation) return;
    const epoch = getNodeEpoch();
    setPendingOperation("claim");
    setContentOwnerError(null);
    try {
      await api.claimInteractionContentOwner(
        selectedMetadata.source.terminal_id,
        selectedMetadata.source.incarnation_id,
        contentOwnerReason.trim(),
      );
      if (epoch !== getNodeEpoch()) return;
      setContentOwnerReason("");
      setDetailRefreshCounter((value) => value + 1);
    } catch (error) {
      if (epoch !== getNodeEpoch()) return;
      setContentOwnerError(errorMessage(error, "claim"));
    } finally {
      if (epoch === getNodeEpoch()) setPendingOperation(null);
    }
  };

  const submitDelegation = async () => {
    if (!currentDetail || !delegationCandidate || !delegationReason.trim() || pendingOperation) return;
    const epoch = getNodeEpoch();
    setPendingOperation("delegation");
    setOperationMessage(null);
    setAnswerMessage(null);
    try {
      await api.delegateInteraction(
        currentDetail.question_id,
        currentDetail.version,
        delegationCandidate.assignment_id,
        delegationReason.trim(),
        delegationExpiry,
      );
      if (epoch !== getNodeEpoch()) return;
      setDelegationFormOpen(false);
      setDelegationReason("");
      setOperationMessage("Temporary clarification delegation recorded. Refreshing its audited grant status.");
      setDetailRefreshCounter((value) => value + 1);
      setListRefreshCounter((value) => value + 1);
    } catch (error) {
      if (epoch !== getNodeEpoch()) return;
      setOperationMessage(errorMessage(error, "delegation"));
      setDetailRefreshCounter((value) => value + 1);
      setListRefreshCounter((value) => value + 1);
    } finally {
      if (epoch === getNodeEpoch()) setPendingOperation(null);
    }
  };

  const revokeDelegation = async () => {
    if (!currentDetail || !currentDelegation || !delegationReason.trim() || pendingOperation) return;
    const epoch = getNodeEpoch();
    setPendingOperation("delegation");
    setOperationMessage(null);
    try {
      await api.revokeInteractionDelegation(
        currentDetail.question_id,
        currentDetail.version,
        delegationReason.trim(),
      );
      if (epoch !== getNodeEpoch()) return;
      setDelegationFormOpen(false);
      setDelegationReason("");
      setOperationMessage("Delegation revocation recorded. Refreshing its audited grant status.");
      setDetailRefreshCounter((value) => value + 1);
      setListRefreshCounter((value) => value + 1);
    } catch (error) {
      if (epoch !== getNodeEpoch()) return;
      setOperationMessage(errorMessage(error, "delegation"));
      setDetailRefreshCounter((value) => value + 1);
      setListRefreshCounter((value) => value + 1);
    } finally {
      if (epoch === getNodeEpoch()) setPendingOperation(null);
    }
  };

  const chooseGroup = (groupId: string) => {
    const groupItem = rows.find((item) => item.group_id === groupId);
    if (!groupItem) return;
    setSelectedId(groupItem.question_id);
    setAgentFilter("");
    setTaskFilter("");
    setRecipientFilter("");
  };

  const formDisabled = !answerable || pendingOperation === "answer" || Boolean(pendingAnswerAttempt && pendingAnswerAttempt.state !== "submitting");
  const statusOptions = LIFECYCLE_FILTERS;

  return (
    <section className="space-y-5 text-gray-200" aria-labelledby="questions-heading">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <BellRing size={19} className="text-emerald-300" />
            <h1 id="questions-heading" className="text-xl font-semibold text-white">Questions</h1>
          </div>
          <p className="mt-1 max-w-3xl text-sm text-gray-400">
            Review agent requests and their delivery status. A response does not grant Work authorization or prove the agent continued.
          </p>
        </div>
        <button
          type="button"
          onClick={() => refreshListRef.current()}
          className="inline-flex items-center gap-2 rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-xs text-gray-200 hover:bg-gray-800"
        >
          <RefreshCw size={14} /> Refresh questions
        </button>
      </header>

      <div className="grid gap-5 xl:grid-cols-[minmax(19rem,0.8fr)_minmax(0,1.2fr)]">
        <section aria-labelledby="question-list-heading" className="min-w-0 rounded-2xl border border-gray-800 bg-gray-950/60 p-4">
          <div className="flex items-center justify-between gap-3">
            <h2 id="question-list-heading" className="text-sm font-semibold text-white">Interaction inbox</h2>
            <span className="text-xs text-gray-500">{visibleRows.length} loaded</span>
          </div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <label className="text-xs text-gray-400">
              Filter by agent or terminal
              <input
                type="search"
                value={agentFilter}
                onChange={(event) => setAgentFilter(event.target.value)}
                placeholder="Provider or terminal ID"
                className="mt-1 w-full rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-600 focus:border-emerald-500 focus:outline-none"
              />
            </label>
            <label className="text-xs text-gray-400">
              Filter by task or session
              <input
                type="search"
                value={taskFilter}
                onChange={(event) => setTaskFilter(event.target.value)}
                placeholder="Work item, attempt, or session"
                className="mt-1 w-full rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-600 focus:border-emerald-500 focus:outline-none"
              />
            </label>
            <label className="text-xs text-gray-400">
              Filter by recipient
              <input
                type="search"
                value={recipientFilter}
                onChange={(event) => setRecipientFilter(event.target.value)}
                placeholder="Person, supervisor, or reason"
                className="mt-1 w-full rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-600 focus:border-emerald-500 focus:outline-none"
              />
            </label>
            <label className="text-xs text-gray-400">
              Filter by lifecycle status
              <select
                aria-label="Filter by lifecycle status"
                value={statusFilter}
                onChange={(event) => setStatusFilter(event.target.value as LifecycleFilter)}
                className="mt-1 w-full rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-100 focus:border-emerald-500 focus:outline-none"
              >
                {statusOptions.map((option) => (
                  <option key={option.value || "all"} value={option.value}>{option.label}</option>
                ))}
              </select>
            </label>
          </div>

          {listError && <p role="alert" className="mt-3 rounded-lg border border-amber-900/60 bg-amber-950/30 px-3 py-2 text-sm text-amber-200">{listError}</p>}
          {loadingList && rows.length === 0 && (
            <p role="status" className="flex items-center gap-2 py-8 text-sm text-gray-500"><Loader2 size={16} className="animate-spin" />Loading questions…</p>
          )}
          {!loadingList && !listError && visibleRows.length === 0 && (
            <div className="py-8 text-center">
              <p className="text-sm text-gray-400">No questions match these filters.</p>
              <p className="mt-1 text-xs text-gray-600">Open optional questions stay visible until they expire or close.</p>
            </div>
          )}
          <ul className="mt-4 space-y-3">
            {visibleRows.map((item) => (
              <InteractionCard
                key={item.question_id}
                item={item}
                selected={item.question_id === selectedId}
                onSelect={() => selectQuestion(item.question_id)}
              />
            ))}
          </ul>
          {cursor && (
            <button
              type="button"
              disabled={loadingMore}
              onClick={() => void loadMore()}
              className="mt-4 w-full rounded-lg border border-gray-700 px-3 py-2 text-xs text-gray-300 hover:bg-gray-800 disabled:opacity-50"
            >
              {loadingMore ? "Loading…" : "Load more questions"}
            </button>
          )}
          <p className="mt-3 text-[11px] text-gray-600">The inbox refreshes every second. Question content loads separately under the current account’s access.</p>
        </section>

        <section aria-labelledby="question-detail-heading" className="min-w-0 rounded-2xl border border-gray-800 bg-gray-950/60 p-4 sm:p-5">
          {!selectedId && (
            <div className="flex min-h-60 flex-col items-center justify-center text-center">
              <BellRing size={28} className="text-gray-700" />
              <h2 id="question-detail-heading" className="mt-3 text-base font-semibold text-gray-300">Select a question</h2>
              <p className="mt-1 max-w-sm text-sm text-gray-500">Review its authorized content, recipient, task, and delivery state here.</p>
            </div>
          )}
          {selectedId && (
            <>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2 id="question-detail-heading" className="text-base font-semibold text-white">Question detail</h2>
                  {selectedMetadata && <p className="mt-1 break-all text-xs font-mono text-gray-500">{sourceTitle(selectedMetadata)}</p>}
                </div>
                {selectedMetadata && <LifecycleBadge status={selectedMetadata.lifecycle} />}
              </div>

              {loadingDetail && !currentDetail && (
                <p role="status" className="mt-6 flex items-center gap-2 text-sm text-gray-500"><Loader2 size={16} className="animate-spin" />Checking question access and version…</p>
              )}
              {detailError && (
                <div className="mt-5 rounded-xl border border-amber-900/60 bg-amber-950/25 p-4">
                  <p role="alert" className="flex items-start gap-2 text-sm text-amber-200">
                    <ShieldAlert size={17} className="mt-0.5 shrink-0" />
                    <span>{detailError}</span>
                  </p>
                  <button type="button" onClick={() => void loadQuestionDetailNow()} className="mt-3 text-xs text-emerald-300 hover:text-emerald-200">Refresh current question</button>
                  {detailErrorStatus === 403 && selectedMetadata && (
                    <form
                      className="mt-4 border-t border-amber-900/50 pt-4"
                      onSubmit={(event) => { event.preventDefault(); void claimContentAccess(); }}
                    >
                      <p className="text-xs text-gray-300">If this terminal has no content owner and your account has cao:admin, you can designate yourself for this exact source incarnation.</p>
                      <label className="mt-3 block text-xs text-gray-400">
                        Reason for content access
                        <textarea
                          aria-label="Reason for content access"
                          maxLength={512}
                          value={contentOwnerReason}
                          onChange={(event) => setContentOwnerReason(event.target.value)}
                          className="mt-1 min-h-16 w-full rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-100"
                        />
                      </label>
                      {contentOwnerError && <p role="alert" className="mt-2 text-xs text-amber-200">{contentOwnerError}</p>}
                      <button
                        type="submit"
                        disabled={!contentOwnerReason.trim() || pendingOperation === "claim"}
                        className="mt-3 rounded-lg border border-amber-800 px-3 py-2 text-xs text-amber-100 hover:bg-amber-950 disabled:opacity-50"
                      >
                        {pendingOperation === "claim" ? "Claiming…" : "Claim this source (admin only)"}
                      </button>
                    </form>
                  )}
                </div>
              )}

              {currentDetail && (
                <>
                  <div className="mt-5 grid gap-3 sm:grid-cols-2">
                    <div className="rounded-xl bg-gray-900/70 p-3">
                      <p className="text-[11px] uppercase tracking-wide text-gray-500">Source and task</p>
                      <p className="mt-1 text-sm text-gray-200">{currentDetail.source.provider} · {currentDetail.source.execution_mode}</p>
                      <p className="mt-1 break-all text-xs text-gray-400">Terminal {currentDetail.source.terminal_id}</p>
                      {currentDetail.task?.work_item_id && <p className="mt-1 break-all text-xs text-gray-400">Work item {currentDetail.task.work_item_id}</p>}
                      {currentDetail.task?.attempt_id && <p className="mt-1 break-all text-xs text-gray-500">Attempt {currentDetail.task.attempt_id}</p>}
                      {currentDetail.task?.session_name && <p className="mt-1 break-all text-xs text-gray-500">Session {currentDetail.task.session_name}</p>}
                    </div>
                    <div className="rounded-xl bg-gray-900/70 p-3">
                      <p className="text-[11px] uppercase tracking-wide text-gray-500">Recipient and deadline</p>
                      <p className="mt-1 text-sm text-gray-200">{currentDetail.recipient.label ?? currentDetail.recipient.kind}</p>
                      <p className="mt-1 text-xs text-gray-400">{currentDetail.recipient.reason}</p>
                      <p className="mt-2 text-xs text-gray-500">{currentDetail.required ? "Required for dependent work" : "Optional; does not block work"}</p>
                      <p className="mt-1 text-xs text-gray-500">Expires: {formatTime(currentDetail.expires_at)}</p>
                    </div>
                  </div>

                  <GroupProgress item={currentDetail} />

                  <div className="mt-5 rounded-xl border border-gray-800 bg-gray-900/50 p-4">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div>
                        <p className="text-xs text-gray-500">{currentDetail.origin === "native" ? "Observed provider dialog" : "Explicit agent question"} · {kindLabel(currentDetail.kind)}</p>
                        <p className="mt-1 text-[11px] text-gray-600">Version {currentDetail.version}</p>
                      </div>
                      {currentDetail.required ? (
                        <span className="rounded-full bg-amber-400/10 px-2 py-0.5 text-[11px] text-amber-300">Required</span>
                      ) : (
                        <span className="rounded-full bg-gray-800 px-2 py-0.5 text-[11px] text-gray-300">Optional</span>
                      )}
                    </div>
                    {currentDetail.kind === "credential" ? (
                      <p className="mt-4 rounded-lg bg-gray-950/70 p-3 text-sm text-gray-300">
                        {explicitCredential
                          ? "Credential prompt hidden for privacy. CAO cannot collect or send credentials. Use the provider’s private authentication flow or cancel this question; typing in this terminal will not answer it."
                          : "Credential prompt hidden for privacy. Use the provider’s private authentication flow in its exact terminal; CAO never collects passwords or access codes."}
                      </p>
                    ) : currentDetail.prompt !== null ? (
                      <div className="mt-4 whitespace-pre-wrap break-words rounded-lg bg-gray-950/70 p-3 text-sm leading-relaxed text-gray-100">{currentDetail.prompt}</div>
                    ) : (
                      <p className="mt-4 rounded-lg bg-gray-950/70 p-3 text-sm text-gray-400">Question content is unavailable for this view. CAO will not infer an answer from terminal output.</p>
                    )}
                    {currentDetail.group_progress && (
                      <div className="mt-3 rounded-lg border border-gray-800 px-3 py-2 text-xs text-gray-400">
                        Group progress: {currentDetail.group_progress.required_answered} of {currentDetail.group_progress.required_total} required answers complete; {currentDetail.group_progress.total} questions total.
                      </div>
                    )}
                  </div>

                  {manualOnly && (
                    <div className="mt-4 rounded-xl border border-violet-900/60 bg-violet-950/20 p-4">
                      <p className="flex items-start gap-2 text-sm text-violet-100">
                        <TerminalSquare size={17} className="mt-0.5 shrink-0" />
                        <span>{explicitCredential
                          ? "This explicit credential request is inspect-only in CAO. Use the provider’s private authentication flow or cancel the question. CAO cannot accept a password or code, and typing in this terminal will not answer the request."
                          : "Handle this provider-owned dialog in its exact terminal. CAO does not collect passwords or access codes, and it will not turn an unknown dialog into an approval."}</span>
                      </p>
                      {currentDetail.manual_reason && <p className="mt-2 text-xs text-violet-200/80">{manualReasonLabel(currentDetail.manual_reason)}</p>}
                      {!explicitCredential && <button
                        type="button"
                        onClick={() => onOpenManualTerminal(
                          currentDetail.source.terminal_id,
                          currentDetail.source.incarnation_id,
                        )}
                        className="mt-3 inline-flex items-center gap-2 rounded-lg border border-violet-800 px-3 py-2 text-xs font-medium text-violet-100 hover:bg-violet-900/40"
                      >
                        <TerminalSquare size={14} /> Open source terminal for manual handling
                      </button>}
                    </div>
                  )}

                  {(delegationCandidate || currentDelegation) &&
                    !manualOnly && (
                    <section aria-label="Temporary clarification delegation" className="mt-4 rounded-xl border border-sky-900/50 bg-sky-950/15 p-4">
                      <h3 className="text-sm font-medium text-sky-100">Temporary clarification delegation</h3>
                      <p className="mt-1 text-xs text-gray-400">
                        Questions are routed to a human by default. A work relationship only identifies a possible reviewer; it does not grant access. CAO checks that the current content owner approves this exact question.
                      </p>
                      {currentDelegation && (
                        <div className="mt-3 rounded-lg bg-gray-950/70 p-3 text-xs text-gray-300">
                          <p>Reviewer terminal {currentDelegation.terminal_id} · {currentDelegation.state}</p>
                          <p className="mt-1 text-gray-500">Grant expires {formatTime(currentDelegation.expires_at)}. Scope: this question only.</p>
                        </div>
                      )}
                      {delegationCandidate && currentDetail.origin === "explicit" && currentDetail.kind !== "approval" && (
                        <p className="mt-3 text-xs text-gray-300">
                          Suggested assignment: terminal {delegationCandidate.terminal_id}. It will expire automatically after at most five minutes.
                        </p>
                      )}
                      {delegationAllowed && currentDelegation?.state !== "active" && !delegationFormOpen && (
                        <button
                          type="button"
                          onClick={() => setDelegationFormOpen(true)}
                          className="mt-3 rounded-lg border border-sky-800 px-3 py-2 text-xs font-medium text-sky-100 hover:bg-sky-900/30"
                        >
                          Review and approve temporary delegation
                        </button>
                      )}
                      {currentDelegation?.state === "active" && currentDetail.origin === "explicit" && currentDetail.kind !== "approval" && !delegationFormOpen && (
                        <button
                          type="button"
                          onClick={() => setDelegationFormOpen(true)}
                          className="mt-3 rounded-lg border border-gray-700 px-3 py-2 text-xs text-gray-200 hover:bg-gray-800"
                        >
                          Revoke delegation
                        </button>
                      )}
                      {delegationFormOpen && (
                        <form
                          className="mt-3 space-y-3 border-t border-sky-900/40 pt-3"
                          onSubmit={(event) => {
                            event.preventDefault();
                            if (currentDelegation?.state === "active") void revokeDelegation();
                            else void submitDelegation();
                          }}
                        >
                          <p className="text-xs text-sky-100">
                            {currentDelegation?.state === "active"
                              ? `Review revocation for ${currentDelegation.terminal_id}.`
                              : `Review question ${currentDetail.question_id}, version ${currentDetail.version}, for candidate terminal ${delegationCandidate?.terminal_id ?? "unavailable"}.`}
                          </p>
                          {currentDelegation?.state !== "active" && (
                            <label className="block text-xs text-gray-400">
                              Grant duration (seconds)
                              <input
                                type="number"
                                min={1}
                                max={300}
                                value={delegationExpiry}
                                onChange={(event) => setDelegationExpiry(Math.max(1, Math.min(300, Number(event.target.value) || 1)))}
                                className="mt-1 block w-32 rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-100"
                              />
                            </label>
                          )}
                          <label className="block text-xs text-gray-400">
                            {currentDelegation?.state === "active" ? "Reason for revoking this grant" : "Reason for approving this grant"}
                            <textarea
                              aria-label={currentDelegation?.state === "active" ? "Reason for revoking delegation" : "Reason for approving delegation"}
                              maxLength={512}
                              value={delegationReason}
                              onChange={(event) => setDelegationReason(event.target.value)}
                              className="mt-1 min-h-16 w-full rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-100"
                            />
                          </label>
                          <div className="flex flex-wrap gap-2">
                            <button
                              type="submit"
                              disabled={!delegationReason.trim() || pendingOperation === "delegation" || (currentDelegation?.state !== "active" && !delegationAllowed)}
                              className="rounded-lg bg-sky-800 px-3 py-2 text-xs font-medium text-white hover:bg-sky-700 disabled:opacity-40"
                            >
                              {pendingOperation === "delegation" ? "Recording…" : currentDelegation?.state === "active" ? "Confirm revocation" : "Approve this delegation"}
                            </button>
                            <button type="button" onClick={() => setDelegationFormOpen(false)} className="rounded-lg border border-gray-700 px-3 py-2 text-xs text-gray-300 hover:bg-gray-800">Close</button>
                          </div>
                        </form>
                      )}
                    </section>
                  )}

                  {acceptedReceipt && (
                    <div className="mt-4 rounded-xl border border-emerald-900/60 bg-emerald-950/25 p-4">
                      <p className="flex items-center gap-2 text-sm font-medium text-emerald-200"><Check size={16} />Answer accepted</p>
                      <p className="mt-2 text-sm text-gray-200">{deliveryDescription(acceptedReceipt.delivery_state)}</p>
                      {acceptedReceipt.delivery_state !== "agent_acknowledged" && (
                        <p className="mt-1 text-xs text-gray-400">Agent acceptance has not been observed. The answer alone does not prove that work continued.</p>
                      )}
                      <p className="mt-2 text-xs text-gray-500">Accepted {formatTime(acceptedReceipt.accepted_at)}</p>
                    </div>
                  )}

                  {!acceptedReceipt && answerable && (
                    <form
                      className="mt-4 rounded-xl border border-gray-800 bg-gray-900/40 p-4"
                      onSubmit={(event) => { event.preventDefault(); void submitAnswer(); }}
                    >
                      <h3 className="text-sm font-semibold text-white">Your response</h3>
                      <p className="mt-2 text-[11px] text-gray-500">Do not enter passwords or authentication codes; use the provider terminal.</p>
                      {(currentDetail.kind === "text" || (currentDetail.kind === "choice_or_text" && freeTextMode)) && (
                        <label className="mt-3 block text-sm text-gray-300">
                          Your answer
                          <textarea
                            aria-label="Your answer"
                            maxLength={4096}
                            disabled={formDisabled}
                            value={textAnswer}
                            onChange={(event) => setTextAnswer(event.target.value)}
                            className="mt-1 min-h-28 w-full rounded-lg border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-100 focus:border-emerald-500 focus:outline-none disabled:opacity-50"
                          />
                        </label>
                      )}

                      {currentDetail.kind === "choice_or_text" && (
                        <label className="mt-3 flex cursor-pointer items-center gap-2 text-sm text-gray-200">
                          <input
                            type="radio"
                            name={`response-${currentDetail.question_id}`}
                            checked={freeTextMode}
                            disabled={formDisabled}
                            onChange={() => { setFreeTextMode(true); setSelectedOptions([]); }}
                          />
                          Write another answer
                        </label>
                      )}

                      {(currentDetail.kind === "single_choice" || currentDetail.kind === "approval" || currentDetail.kind === "choice_or_text") && (
                        <fieldset className="mt-3 space-y-2">
                          <legend className="mb-2 text-sm text-gray-300">{currentDetail.kind === "approval" ? "Choose one declared response" : "Choose one option"}</legend>
                          {currentDetail.options.map((option) => (
                            <label key={option.id} className={`flex cursor-pointer items-start gap-2 rounded-lg border border-gray-800 bg-gray-950/70 px-3 py-2 text-sm ${option.available ? "text-gray-200" : "text-gray-600"}`}>
                              <input
                                type="radio"
                                name={`response-${currentDetail.question_id}`}
                                value={option.id}
                                checked={!freeTextMode && selectedOptions[0] === option.id}
                                disabled={!option.available || formDisabled}
                                onChange={() => { setFreeTextMode(false); setSelectedOptions([option.id]); }}
                                className="mt-1"
                              />
                              <span>{option.label}{!option.available && <span className="ml-2 text-xs">(unavailable)</span>}</span>
                            </label>
                          ))}
                        </fieldset>
                      )}

                      {currentDetail.kind === "multi_choice" && (
                        <fieldset className="mt-3 space-y-2">
                          <legend className="mb-2 text-sm text-gray-300">Choose one or more options</legend>
                          {currentDetail.options.map((option) => (
                            <label key={option.id} className={`flex cursor-pointer items-start gap-2 rounded-lg border border-gray-800 bg-gray-950/70 px-3 py-2 text-sm ${option.available ? "text-gray-200" : "text-gray-600"}`}>
                              <input
                                type="checkbox"
                                value={option.id}
                                checked={selectedOptions.includes(option.id)}
                                disabled={!option.available || formDisabled}
                                onChange={(event) => setSelectedOptions((previous) => event.target.checked
                                  ? [...previous, option.id]
                                  : previous.filter((id) => id !== option.id))}
                                className="mt-1"
                              />
                              <span>{option.label}{!option.available && <span className="ml-2 text-xs">(unavailable)</span>}</span>
                            </label>
                          ))}
                        </fieldset>
                      )}

                      {currentDetail.kind === "approval" && (
                        <p className="mt-3 rounded-lg border border-amber-900/60 bg-amber-950/20 px-3 py-2 text-xs text-amber-100">
                          This response does not grant Work authorization or replace the approval required for a specific work revision.
                        </p>
                      )}
                      {answerMessage && <p role="alert" className="mt-3 rounded-lg bg-amber-950/30 px-3 py-2 text-sm text-amber-200">{answerMessage}</p>}
                      {operationMessage && <p role="status" className="mt-3 text-sm text-gray-300">{operationMessage}</p>}
                      {pendingAnswerAttempt?.state === "uncertain" && (
                        <div className="mt-3 rounded-lg border border-amber-900/60 bg-amber-950/20 p-3">
                          <p className="text-sm text-amber-100">The answer may already be recorded. Refresh the current status or retry this exact response with its same idempotency key.</p>
                          <div className="mt-3 flex flex-wrap gap-2">
                            <button type="button" onClick={() => void loadQuestionDetailNow()} className="rounded-lg border border-amber-800 px-3 py-2 text-xs text-amber-100">Refresh current status</button>
                            <button type="button" disabled={pendingOperation === "answer"} onClick={() => void submitAnswer(pendingAnswerAttempt)} className="rounded-lg bg-amber-700 px-3 py-2 text-xs font-medium text-white disabled:opacity-50">
                              {pendingOperation === "answer" ? "Checking…" : "Retry same answer safely"}
                            </button>
                            <button type="button" onClick={() => { setReconcileOutcome("remains_uncertain"); setReconcileFormOpen(true); }} className="rounded-lg border border-gray-700 px-3 py-2 text-xs text-gray-200">Reconcile delivery</button>
                          </div>
                        </div>
                      )}
                      {pendingAnswerAttempt?.state === "conflict" && (
                        <p role="status" className="mt-3 text-sm text-amber-200">This version cannot be submitted again. Review the refreshed question and answer only if it remains open.</p>
                      )}
                      <button
                        type="submit"
                        disabled={formDisabled || !buildCurrentResponse()}
                        className="mt-4 inline-flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        {pendingOperation === "answer" ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
                        {pendingOperation === "answer" ? "Submitting…" : "Submit answer"}
                      </button>
                    </form>
                  )}

                  {!acceptedReceipt && currentDetail.lifecycle === "open" && !currentDetail.can_answer && !manualOnly && (
                    <p role="status" className="mt-4 rounded-lg bg-gray-900 px-3 py-2 text-sm text-gray-400">This question is visible as metadata, but your current authority does not permit an answer.</p>
                  )}

                  {!acceptedReceipt && (
                    <p className="mt-3 text-xs text-gray-400">Delivery: {deliveryDescription(currentDetail.delivery_state)}</p>
                  )}

                  {!acceptedReceipt && (currentDetail.delivery_state === "uncertain" || currentDetail.delivery_state === "claimed" || manualOnly || pendingAnswerAttempt?.state === "uncertain") && (
                    <div className="mt-4 rounded-xl border border-gray-800 bg-gray-900/40 p-4">
                      <p className="text-sm font-medium text-gray-200">Reconcile what happened</p>
                      <p className="mt-1 text-xs text-gray-500">Record only evidence you observed. Reconciliation does not send or resend an answer.</p>
                      <button type="button" onClick={() => { setReconcileOutcome(manualOnly ? "answered_outside_cao" : "remains_uncertain"); setReconcileFormOpen((value) => !value); }} className="mt-3 rounded-lg border border-gray-700 px-3 py-2 text-xs text-gray-200 hover:bg-gray-800">
                        {reconcileFormOpen ? "Close reconciliation" : "Reconcile delivery"}
                      </button>
                      {reconcileFormOpen && (
                        <form className="mt-3 grid gap-3" onSubmit={(event) => { event.preventDefault(); void submitReconciliation(); }}>
                          <label className="text-xs text-gray-400">
                            Reconciliation outcome
                            <select
                              aria-label="Reconciliation outcome"
                              value={reconcileOutcome}
                              onChange={(event) => setReconcileOutcome(event.target.value as InteractionReconcileOutcome)}
                              className="mt-1 w-full rounded-lg border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-100"
                            >
                              <option value="answered_outside_cao">Answered in the provider terminal</option>
                              <option value="dialog_still_open">Dialog is still open</option>
                              <option value="remains_uncertain">Outcome remains uncertain</option>
                              {!manualOnly && <option value="no_effect_proven">No effect is proven</option>}
                            </select>
                          </label>
                          <label className="text-xs text-gray-400">
                            Evidence reference
                            <input aria-label="Evidence reference" maxLength={128} value={evidenceCode} onChange={(event) => setEvidenceCode(event.target.value)} className="mt-1 w-full rounded-lg border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-100" />
                          </label>
                          <label className="text-xs text-gray-400">
                            Note (optional)
                            <textarea maxLength={512} value={evidenceNote} onChange={(event) => setEvidenceNote(event.target.value)} className="mt-1 min-h-16 w-full rounded-lg border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-100" />
                          </label>
                          <button type="submit" disabled={!evidenceCode.trim() || pendingOperation === "reconcile"} className="justify-self-start rounded-lg bg-gray-700 px-3 py-2 text-xs font-medium text-white hover:bg-gray-600 disabled:opacity-40">
                            {pendingOperation === "reconcile" ? "Recording…" : "Record reconciliation"}
                          </button>
                        </form>
                      )}
                    </div>
                  )}

                  {operationMessage && <p role="status" className="mt-3 text-sm text-gray-300">{operationMessage}</p>}

                  {(currentDetail.lifecycle === "open" || currentDetail.lifecycle === "manual_only") && (
                    <div className="mt-4 border-t border-gray-800 pt-4">
                      {!cancelFormOpen ? (
                        <button type="button" onClick={() => setCancelFormOpen(true)} className="rounded-lg border border-gray-700 px-3 py-2 text-xs text-gray-300 hover:bg-gray-800">
                          Cancel question
                        </button>
                      ) : (
                        <form onSubmit={(event) => { event.preventDefault(); void confirmCancel(); }} className="rounded-xl border border-gray-800 bg-gray-900/40 p-4">
                          <p className="text-sm text-gray-200">Cancel this question?</p>
                          {currentDetail.required && <p className="mt-1 text-xs text-amber-200">This question is required. Other unanswered required questions in its group may still keep dependent work waiting.</p>}
                          <label className="mt-3 block text-xs text-gray-400">
                            Cancellation reason
                            <textarea aria-label="Cancellation reason" maxLength={512} value={cancelReason} onChange={(event) => setCancelReason(event.target.value)} className="mt-1 min-h-16 w-full rounded-lg border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-100" />
                          </label>
                          <div className="mt-3 flex gap-2">
                            <button type="submit" disabled={!cancelReason.trim() || pendingOperation === "cancel"} className="rounded-lg bg-red-700 px-3 py-2 text-xs font-medium text-white hover:bg-red-600 disabled:opacity-40">
                              {pendingOperation === "cancel" ? "Cancelling…" : "Confirm cancellation"}
                            </button>
                            <button type="button" onClick={() => { setCancelFormOpen(false); setCancelReason(""); }} className="rounded-lg border border-gray-700 px-3 py-2 text-xs text-gray-300">Keep question open</button>
                          </div>
                        </form>
                      )}
                    </div>
                  )}

                  {currentDetail.group_id && currentDetail.group_progress && (
                    <p className="mt-3 text-xs text-gray-500">This response belongs only to question {currentDetail.question_id}; group progress is tracked per question version.</p>
                  )}
                  <HistorySection key={currentDetail.question_id} questionId={currentDetail.question_id} />
                </>
              )}
            </>
          )}
        </section>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <WaitGraphSection
          graph={waitGraph}
          loading={waitGraphLoading}
          error={waitGraphError}
          onRefresh={() => void refreshWaitGraph()}
          onSelectGroup={chooseGroup}
        />
        <CapabilitySection
          capabilities={capabilities}
          loading={capabilitiesLoading}
          error={capabilitiesError}
        />
      </div>
    </section>
  );
}

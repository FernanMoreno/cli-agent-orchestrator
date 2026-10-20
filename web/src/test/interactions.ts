import type {
  AnswerReceipt,
  InteractionDetail,
  InteractionMetadata,
  InteractionWaitGraph,
  ProviderInteractionCapability,
} from "../api";

const timestamp = "2026-10-10T10:00:00Z";

export function interactionMetadata(
  overrides: Partial<InteractionMetadata> = {},
): InteractionMetadata {
  return {
    question_id: "question-1",
    version: 1,
    group_id: "group-1",
    lifecycle: "open",
    delivery_state: null,
    source: {
      terminal_id: "terminal-1",
      provider: "codex",
      provider_version: "1.2.3",
      execution_mode: "local",
      incarnation_id: "incarnation-1",
    },
    task: {
      work_item_id: "work-1",
      attempt_id: "attempt-1",
      session_name: "session-1",
    },
    kind: "text",
    required: true,
    recipient: {
      kind: "human",
      label: "Operator",
      reason: "This decision needs the operator.",
    },
    group_progress: {
      required_answered: 0,
      required_total: 1,
      total: 2,
    },
    origin: "explicit",
    can_answer: true,
    manual_reason: null,
    created_at: timestamp,
    expires_at: "2026-10-10T10:30:00Z",
    ...overrides,
  };
}

export function interactionDetail(
  overrides: Partial<InteractionDetail> = {},
): InteractionDetail {
  return {
    ...interactionMetadata(),
    prompt: "Which direction should I take?",
    options: [],
    allow_free_text: false,
    answer_receipt: null,
    delegation_candidate: null,
    delegation: null,
    ...overrides,
  };
}

export function answerReceipt(
  overrides: Partial<AnswerReceipt> = {},
): AnswerReceipt {
  return {
    question_id: "question-1",
    version: 1,
    answer_id: "answer-1",
    lifecycle: "answered",
    delivery_state: "returned_to_mcp",
    accepted_at: timestamp,
    replayed: false,
    ...overrides,
  };
}

export function waitGraph(): InteractionWaitGraph {
  return {
    nodes: [
      { id: "task-1", kind: "task", state: "waiting", label: "work-1" },
      {
        id: "group-1",
        kind: "question_group",
        state: "cycle_blocked",
        label: "Questions from codex",
      },
    ],
    edges: [{ from: "task-1", to: "group-1", required: true, cycle: true }],
  };
}

export function interactionCapabilities(): ProviderInteractionCapability[] {
  return [
    {
      provider: "codex",
      provider_version: "1.2.3",
      execution_mode: "local",
      emits_explicit: true,
      detects_native: true,
      responds_native: [],
      manual_reason: "Native dialogs require inspection in the terminal.",
      required_mcp_tools: ["cao_ask_user"],
    },
  ];
}

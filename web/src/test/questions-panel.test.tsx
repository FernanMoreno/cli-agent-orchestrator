import {
  cleanup,
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, getNodeEpoch, setActiveNode } from "../api";
import { QuestionsPanel } from "../components/QuestionsPanel";
import { useStore } from "../store";
import {
  answerReceipt,
  interactionCapabilities,
  interactionDetail,
  interactionMetadata,
  waitGraph,
} from "./interactions";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  setActiveNode(null);
  useStore.setState({ activeNode: null, nodeEpoch: getNodeEpoch() });
});

describe("QuestionsPanel", () => {
  beforeEach(() => {
    vi.spyOn(api, "listInteractions").mockResolvedValue({
      items: [interactionMetadata()],
      next_cursor: null,
    });
    vi.spyOn(api, "getInteractionDetail").mockResolvedValue(
      interactionDetail(),
    );
    vi.spyOn(api, "answerInteraction").mockResolvedValue(answerReceipt());
    vi.spyOn(api, "cancelInteraction").mockResolvedValue(undefined);
    vi.spyOn(api, "reconcileInteraction").mockResolvedValue({
      question_id: "question-1",
      version: 1,
      lifecycle: "reconciled_external",
      delivery_state: "reconciled",
    });
    vi.spyOn(api, "getInteractionHistory").mockResolvedValue({ events: [] });
    vi.spyOn(api, "getInteractionWaitGraph").mockResolvedValue(waitGraph());
    vi.spyOn(api, "getInteractionCapabilities").mockResolvedValue({
      items: interactionCapabilities(),
    });
    vi.spyOn(api, "claimInteractionContentOwner").mockResolvedValue(undefined);
    vi.spyOn(api, "delegateInteraction").mockResolvedValue(interactionDetail({
      version: 2,
      delegation: {
        terminal_id: "reviewer-terminal",
        assignment_id: "assignment-7",
        expires_at: "2026-10-10T10:01:00Z",
        state: "active",
      },
    }));
    vi.spyOn(api, "revokeInteractionDelegation").mockResolvedValue(interactionDetail({
      version: 3,
      delegation: {
        terminal_id: "reviewer-terminal",
        assignment_id: "assignment-7",
        expires_at: "2026-10-10T10:01:00Z",
        state: "revoked",
      },
    }));
  });

  async function openQuestion() {
    render(<QuestionsPanel onOpenManualTerminal={vi.fn()} />);
    const question = await screen.findByRole("button", {
      name: /codex.*terminal-1/i,
    });
    fireEvent.click(question);
    await screen.findByRole("heading", { name: /question detail/i });
  }

  it("submits safe text for the selected version and distinguishes delivery from agent acceptance", async () => {
    vi.mocked(api.getInteractionDetail).mockResolvedValue(
      interactionDetail({
        prompt: '<script>alert("x")</script> Where should I start?',
      }),
    );
    await openQuestion();

    expect(
      await screen.findByText('<script>alert("x")</script> Where should I start?'),
    ).toBeInTheDocument();
    expect(screen.getByText(/No answer \/ no delivery yet/i)).toBeInTheDocument();
    expect(screen.getByText(/Do not enter passwords or authentication codes; use the provider terminal/i)).toBeInTheDocument();
    expect(document.querySelector("script")).toBeNull();
    fireEvent.change(screen.getByRole("textbox", { name: /your answer/i }), {
      target: { value: "Start with the tests." },
    });
    fireEvent.click(screen.getByRole("button", { name: /submit answer/i }));

    await waitFor(() =>
      expect(api.answerInteraction).toHaveBeenCalledWith(
        "question-1",
        1,
        expect.any(String),
        { kind: "text", text: "Start with the tests." },
      ),
    );
    expect(await screen.findByText(/returned to agent tool/i)).toBeInTheDocument();
    expect(screen.getByText(/agent acceptance has not been observed/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /submit answer/i })).toBeNull();
  });

  it("renders only declared available single-choice options", async () => {
    vi.mocked(api.listInteractions).mockResolvedValue({
      items: [interactionMetadata({ kind: "single_choice" })],
      next_cursor: null,
    });
    vi.mocked(api.getInteractionDetail).mockResolvedValue(
      interactionDetail({
        kind: "single_choice",
        prompt: "Choose an approach.",
        options: [
          { id: "safe", label: "Safe route", available: true },
          { id: "retired", label: "Retired route", available: false },
        ],
      }),
    );
    await openQuestion();

    const safe = screen.getByRole("radio", { name: "Safe route" });
    const retired = screen.getByRole("radio", { name: /Retired route \(unavailable\)/ });
    expect(retired).toBeDisabled();
    fireEvent.click(safe);
    fireEvent.click(screen.getByRole("button", { name: /submit answer/i }));

    await waitFor(() =>
      expect(api.answerInteraction).toHaveBeenCalledWith(
        "question-1",
        1,
        expect.any(String),
        { kind: "options", option_ids: ["safe"] },
      ),
    );
  });

  it("supports multi-select and choice-or-free-text response shapes", async () => {
    vi.mocked(api.listInteractions).mockResolvedValue({
      items: [interactionMetadata({ kind: "multi_choice" })],
      next_cursor: null,
    });
    vi.mocked(api.getInteractionDetail).mockResolvedValue(
      interactionDetail({
        kind: "multi_choice",
        prompt: "Pick the allowed areas.",
        options: [
          { id: "docs", label: "Documentation", available: true },
          { id: "tests", label: "Tests", available: true },
        ],
      }),
    );
    await openQuestion();
    fireEvent.click(screen.getByRole("checkbox", { name: "Documentation" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Tests" }));
    fireEvent.click(screen.getByRole("button", { name: /submit answer/i }));
    await waitFor(() =>
      expect(api.answerInteraction).toHaveBeenCalledWith(
        "question-1",
        1,
        expect.any(String),
        { kind: "options", option_ids: ["docs", "tests"] },
      ),
    );

    cleanup();
    vi.mocked(api.answerInteraction).mockClear();
    vi.mocked(api.listInteractions).mockResolvedValue({
      items: [interactionMetadata({ kind: "choice_or_text" })],
      next_cursor: null,
    });
    vi.mocked(api.getInteractionDetail).mockResolvedValue(
      interactionDetail({
        kind: "choice_or_text",
        allow_free_text: true,
        prompt: "Pick one or suggest another.",
        options: [{ id: "one", label: "Declared one", available: true }],
      }),
    );
    await openQuestion();
    fireEvent.click(screen.getByRole("radio", { name: /write another answer/i }));
    fireEvent.change(screen.getByRole("textbox", { name: /your answer/i }), {
      target: { value: "A different route" },
    });
    fireEvent.click(screen.getByRole("button", { name: /submit answer/i }));
    await waitFor(() =>
      expect(api.answerInteraction).toHaveBeenCalledWith(
        "question-1",
        1,
        expect.any(String),
        { kind: "free_text", text: "A different route" },
      ),
    );
  });

  it("renders approval as declared options and says it grants no Work authorization", async () => {
    vi.mocked(api.listInteractions).mockResolvedValue({
      items: [interactionMetadata({ kind: "approval" })],
      next_cursor: null,
    });
    vi.mocked(api.getInteractionDetail).mockResolvedValue(
      interactionDetail({
        kind: "approval",
        prompt: "Review this exact work decision.",
        delegation_candidate: {
          terminal_id: "reviewer-terminal",
          assignment_id: "assignment-7",
        },
        options: [
          { id: "allow", label: "Allow this proposal", available: true },
          { id: "decline", label: "Decline", available: true },
        ],
      }),
    );
    await openQuestion();

    expect(screen.getByText(/this response does not grant work authorization/i)).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Allow this proposal" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Decline" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /review and approve temporary delegation/i })).toBeNull();
  });

  it("lets the current owner review a short exact-question delegation candidate", async () => {
    vi.mocked(api.getInteractionDetail).mockResolvedValue(
      interactionDetail({
        prompt: "Which patch should I inspect?",
        delegation_candidate: {
          terminal_id: "reviewer-terminal",
          assignment_id: "assignment-7",
        },
      }),
    );
    await openQuestion();

    fireEvent.click(screen.getByRole("button", { name: /review and approve temporary delegation/i }));
    expect(screen.getByText(/question question-1, version 1.*reviewer-terminal/i)).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: /reason for approving delegation/i }), {
      target: { value: "Current owner approved a temporary second review." },
    });
    fireEvent.click(screen.getByRole("button", { name: /approve this delegation/i }));

    await waitFor(() =>
      expect(api.delegateInteraction).toHaveBeenCalledWith(
        "question-1",
        1,
        "assignment-7",
        "Current owner approved a temporary second review.",
        60,
      ),
    );
  });

  it("shows an active delegation as active and lets the owner revoke it", async () => {
    vi.mocked(api.getInteractionDetail).mockResolvedValue(
      interactionDetail({
        delegation: {
          terminal_id: "reviewer-terminal",
          assignment_id: "assignment-7",
          expires_at: "2026-10-10T10:01:00Z",
          state: "active",
        },
      }),
    );
    await openQuestion();

    expect(await screen.findByText(/Reviewer terminal reviewer-terminal · active/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /review and approve temporary delegation/i })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /revoke delegation/i }));
    fireEvent.change(screen.getByRole("textbox", { name: /reason for revoking delegation/i }), {
      target: { value: "The reviewed assignment has ended." },
    });
    fireEvent.click(screen.getByRole("button", { name: /confirm revocation/i }));

    await waitFor(() =>
      expect(api.revokeInteractionDelegation).toHaveBeenCalledWith(
        "question-1",
        1,
        "The reviewed assignment has ended.",
      ),
    );
  });

  it("shows group required/optional progress and filters metadata by task, recipient and status", async () => {
    vi.mocked(api.listInteractions).mockResolvedValue({
      items: [
        interactionMetadata(),
        interactionMetadata({
          question_id: "question-2",
          version: 2,
          kind: "single_choice",
          required: false,
          recipient: { kind: "supervisor", label: "Supervisor", reason: "Within delegation." },
          task: { work_item_id: "work-2", attempt_id: null, session_name: "session-2" },
        }),
      ],
      next_cursor: null,
    });
    render(<QuestionsPanel onOpenManualTerminal={vi.fn()} />);

    expect(await screen.findByRole("button", { name: /question-1/i })).toHaveTextContent(/0 of 1 required answers complete.*2 questions in group/i);
    expect(screen.getAllByText(/optional/i).length).toBeGreaterThan(0);
    fireEvent.change(screen.getByRole("searchbox", { name: /filter by task or session/i }), {
      target: { value: "work-2" },
    });
    expect(screen.queryByRole("button", { name: /terminal-1.*question-1/i })).toBeNull();
    expect(screen.getByRole("button", { name: /terminal-1.*question-2/i })).toBeInTheDocument();

    fireEvent.change(screen.getByRole("combobox", { name: /filter by lifecycle status/i }), {
      target: { value: "expired" },
    });
    await waitFor(() =>
      expect(api.listInteractions).toHaveBeenLastCalledWith(
        { status: ["expired"], limit: 100 },
        expect.any(AbortSignal),
      ),
    );
  });

  it("keeps native unknown dialogs manual-only and opens the exact source terminal", async () => {
    const onOpenManualTerminal = vi.fn();
    vi.mocked(api.listInteractions).mockResolvedValue({
      items: [
        interactionMetadata({
          kind: "native_unknown",
          origin: "native",
          lifecycle: "manual_only",
          can_answer: false,
          manual_reason: "native_dialog_manual_action",
        }),
      ],
      next_cursor: null,
    });
    vi.mocked(api.getInteractionDetail).mockResolvedValue(
      interactionDetail({
        kind: "native_unknown",
        origin: "native",
        lifecycle: "manual_only",
        can_answer: false,
        prompt: null,
        manual_reason: "native_dialog_manual_action",
        delegation_candidate: {
          terminal_id: "reviewer-terminal",
          assignment_id: "assignment-7",
        },
      }),
    );
    render(<QuestionsPanel onOpenManualTerminal={onOpenManualTerminal} />);
    expect(screen.getByRole("combobox", { name: /filter by lifecycle status/i })).toHaveValue("attention");
    await waitFor(() =>
      expect(api.listInteractions).toHaveBeenCalledWith(
        { status: ["open", "manual_only"], limit: 100 },
        expect.any(AbortSignal),
      ),
    );
    fireEvent.click(await screen.findByRole("button", { name: /codex.*terminal-1/i }));

    expect(await screen.findByText(/CAO does not collect passwords or access codes/i)).toBeInTheDocument();
    expect(screen.getByText("Native provider dialogs require manual action in the exact terminal.")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /your answer/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /review and approve temporary delegation/i })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /open source terminal for manual handling/i }));
    expect(onOpenManualTerminal).toHaveBeenCalledWith("terminal-1", "incarnation-1");
  });

  it("keeps credential prompts manual-only with no answer controls or delegated access", async () => {
    const onOpenManualTerminal = vi.fn();
    vi.mocked(api.listInteractions).mockResolvedValue({
      items: [
        interactionMetadata({
          kind: "credential",
          origin: "native",
          lifecycle: "manual_only",
          can_answer: false,
          manual_reason: "Authentication must be completed in the provider terminal.",
        }),
      ],
      next_cursor: null,
    });
    vi.mocked(api.getInteractionDetail).mockResolvedValue(
      interactionDetail({
        kind: "credential",
        origin: "native",
        lifecycle: "manual_only",
        can_answer: false,
        prompt: "What is your password?",
        options: [{ id: "password", label: "Password", available: true }],
        manual_reason: "Authentication must be completed in the provider terminal.",
        delegation_candidate: {
          terminal_id: "reviewer-terminal",
          assignment_id: "assignment-7",
        },
      }),
    );
    render(<QuestionsPanel onOpenManualTerminal={onOpenManualTerminal} />);
    const question = await screen.findByRole("button", { name: /codex.*terminal-1/i });
    expect(question).toHaveTextContent(/credential prompt/i);
    fireEvent.click(question);

    expect(await screen.findByText(/Credential prompt hidden for privacy/i)).toBeInTheDocument();
    expect(screen.queryByText("What is your password?")).toBeNull();
    expect(screen.getByText(/CAO does not collect passwords or access codes/i)).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /your answer/i })).toBeNull();
    expect(screen.queryByRole("radio")).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByRole("button", { name: /submit answer/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /review and approve temporary delegation/i })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /open source terminal for manual handling/i }));
    expect(onOpenManualTerminal).toHaveBeenCalledWith("terminal-1", "incarnation-1");
  });

  it("keeps explicit credential requests inspect-only and lets the owner cancel them", async () => {
    vi.mocked(api.listInteractions).mockResolvedValue({
      items: [
        interactionMetadata({
          kind: "credential",
          origin: "explicit",
          lifecycle: "manual_only",
          can_answer: false,
          manual_reason: "Credential responses are not supported through the model channel.",
        }),
      ],
      next_cursor: null,
    });
    vi.mocked(api.getInteractionDetail).mockResolvedValue(
      interactionDetail({
        kind: "credential",
        origin: "explicit",
        lifecycle: "manual_only",
        can_answer: false,
        prompt: "What is your password?",
        options: [{ id: "password", label: "Password", available: true }],
        manual_reason: "Credential responses are not supported through the model channel.",
      }),
    );
    render(<QuestionsPanel onOpenManualTerminal={vi.fn()} />);
    const question = await screen.findByRole("button", { name: /codex.*terminal-1/i });
    fireEvent.click(question);

    expect(await screen.findByText(/Credential prompt hidden for privacy/i)).toBeInTheDocument();
    expect(screen.getByText(/explicit credential request is inspect-only in CAO/i)).toBeInTheDocument();
    expect(screen.queryByText("What is your password?")).toBeNull();
    expect(screen.queryByRole("textbox", { name: /your answer/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /open source terminal/i })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /cancel question/i }));
    fireEvent.change(screen.getByRole("textbox", { name: /cancellation reason/i }), {
      target: { value: "The private authentication route will be used instead." },
    });
    fireEvent.click(screen.getByRole("button", { name: /confirm cancellation/i }));
    await waitFor(() =>
      expect(api.cancelInteraction).toHaveBeenCalledWith(
        "question-1",
        1,
        "The private authentication route will be used instead.",
      ),
    );
  });

  it("explains the empty provider matrix without claiming unsupported interaction support", async () => {
    vi.mocked(api.getInteractionCapabilities).mockResolvedValue({ items: [] });
    render(<QuestionsPanel onOpenManualTerminal={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /provider support/i }));

    expect(await screen.findByText(/Explicit questions require a terminal with the source-bound CAO MCP channel configured/i)).toBeInTheDocument();
    expect(screen.getByText(/no automatic native-dialog response/i)).toBeInTheDocument();
    expect(screen.getByText(/handle provider dialogs in the agent terminal/i)).toBeInTheDocument();
  });

  it("maps content ACL denial to a precise redaction message without exposing prompt data", async () => {
    vi.mocked(api.getInteractionDetail).mockRejectedValue(
      Object.assign(new Error("403 Forbidden"), {
        status: 403,
        detail: "Private prompt should never be echoed.",
      }),
    );
    render(<QuestionsPanel onOpenManualTerminal={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /codex.*terminal-1/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/metadata is visible, but question content is restricted/i);
    expect(screen.queryByText("Private prompt should never be echoed.")).toBeNull();
    expect(screen.queryByRole("textbox", { name: /your answer/i })).toBeNull();
    fireEvent.change(screen.getByRole("textbox", { name: /reason for content access/i }), {
      target: { value: "I own this local terminal." },
    });
    fireEvent.click(screen.getByRole("button", { name: /claim this source \(admin only\)/i }));
    await waitFor(() =>
      expect(api.claimInteractionContentOwner).toHaveBeenCalledWith(
        "terminal-1",
        "incarnation-1",
        "I own this local terminal.",
      ),
    );
  });

  it("refreshes on a version conflict and never resubmits automatically", async () => {
    vi.mocked(api.answerInteraction).mockRejectedValue(
      Object.assign(new Error("409 Conflict"), { status: 409, detail: "stale text" }),
    );
    await openQuestion();
    fireEvent.change(screen.getByRole("textbox", { name: /your answer/i }), {
      target: { value: "One answer" },
    });
    fireEvent.click(screen.getByRole("button", { name: /submit answer/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/question changed while you were reviewing/i);
    expect(api.answerInteraction).toHaveBeenCalledTimes(1);
    expect(api.getInteractionDetail).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: /submit answer/i })).toBeDisabled();
  });

  it("does not auto-resend a response after an uncertain POST and exposes explicit reconcile", async () => {
    vi.mocked(api.answerInteraction).mockRejectedValue(new TypeError("network lost"));
    await openQuestion();
    fireEvent.change(screen.getByRole("textbox", { name: /your answer/i }), {
      target: { value: "One answer" },
    });
    fireEvent.click(screen.getByRole("button", { name: /submit answer/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/result could not be confirmed/i);
    expect(api.answerInteraction).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: /refresh current status/i })).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: /reconcile delivery/i })[0]);
    expect(screen.getByLabelText(/reconciliation outcome/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/evidence reference/i)).toBeInTheDocument();
  });

  it("retries an uncertain answer only by reusing the same idempotency key", async () => {
    vi.mocked(api.answerInteraction)
      .mockRejectedValueOnce(new TypeError("network lost"))
      .mockResolvedValueOnce(answerReceipt());
    await openQuestion();
    fireEvent.change(screen.getByRole("textbox", { name: /your answer/i }), {
      target: { value: "The same answer" },
    });
    fireEvent.click(screen.getByRole("button", { name: /submit answer/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/result could not be confirmed/i);
    const firstKey = vi.mocked(api.answerInteraction).mock.calls[0][2];
    fireEvent.click(screen.getByRole("button", { name: /retry same answer safely/i }));

    await waitFor(() => expect(api.answerInteraction).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.answerInteraction).mock.calls[1][2]).toBe(firstKey);
    expect(vi.mocked(api.answerInteraction).mock.calls[1][3]).toEqual({
      kind: "text",
      text: "The same answer",
    });
  });

  it("cancels with a reason, renders safe history and marks wait cycles", async () => {
    await openQuestion();
    fireEvent.click(screen.getByRole("button", { name: /cancel question/i }));
    fireEvent.change(screen.getByRole("textbox", { name: /cancellation reason/i }), {
      target: { value: "Operator no longer needs this prompt." },
    });
    fireEvent.click(screen.getByRole("button", { name: /confirm cancellation/i }));
    await waitFor(() =>
      expect(api.cancelInteraction).toHaveBeenCalledWith(
        "question-1",
        1,
        "Operator no longer needs this prompt.",
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: /show interaction history/i }));
    expect(api.getInteractionHistory).toHaveBeenCalledWith("question-1");
    fireEvent.click(screen.getByRole("button", { name: /show wait graph/i }));
    expect((await screen.findAllByText(/cycle detected/i)).length).toBeGreaterThan(0);
    expect(screen.getByText(/work-1.*questions from codex/i)).toBeInTheDocument();
  });

  it("shows provider support separately for explicit questions and native response", async () => {
    render(<QuestionsPanel onOpenManualTerminal={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /provider support/i }));

    const capability = await screen.findByRole("article", { name: /codex.*local/i });
    expect(within(capability).getByText(/explicit questions: supported/i)).toBeInTheDocument();
    expect(within(capability).getByText(/native response: manual only/i)).toBeInTheDocument();
    expect(within(capability).getByText(/native dialog detection: supported/i)).toBeInTheDocument();
  });

  it("does not render the previous node's selected detail after switching nodes", async () => {
    await openQuestion();
    expect(await screen.findByText("Which direction should I take?")).toBeInTheDocument();

    actSelectNode("node-b");
    expect(screen.queryByText("Which direction should I take?")).toBeNull();
    await waitFor(() => expect(api.listInteractions).toHaveBeenCalled());
  });

  it("discards a protected detail response that arrives after a node switch", async () => {
    let resolveStaleDetail: ((value: ReturnType<typeof interactionDetail>) => void) | undefined;
    vi.mocked(api.listInteractions)
      .mockResolvedValueOnce({ items: [interactionMetadata()], next_cursor: null })
      .mockResolvedValue({ items: [], next_cursor: null });
    vi.mocked(api.getInteractionDetail).mockImplementationOnce(
      () => new Promise((resolve) => { resolveStaleDetail = resolve; }),
    );
    render(<QuestionsPanel onOpenManualTerminal={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /codex.*terminal-1/i }));
    await waitFor(() => expect(api.getInteractionDetail).toHaveBeenCalled());

    actSelectNode("node-b");
    await act(async () => {
      resolveStaleDetail?.(interactionDetail({ prompt: "Content from the previous node" }));
    });

    expect(screen.queryByText("Content from the previous node")).toBeNull();
  });
});

function actSelectNode(name: string) {
  act(() => useStore.getState().selectNode(name));
}

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, fetchJSON, setActiveNode } from "../api";

describe("Agent interaction API", () => {
  const mockFetch = vi.fn();

  beforeEach(() => {
    mockFetch.mockReset();
    setActiveNode(null);
    vi.stubGlobal("fetch", mockFetch);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  function mockResponse(data: unknown, status = 200) {
    mockFetch.mockResolvedValueOnce({
      ok: status >= 200 && status < 300,
      status,
      statusText: status === 201 ? "Created" : "OK",
      text: () => Promise.resolve(JSON.stringify(data)),
    });
  }

  it("lists metadata with repeated lifecycle filters and an opaque cursor", async () => {
    const response = { items: [], next_cursor: "page / 2" };
    mockResponse(response);

    await expect(
      api.listInteractions({
        status: ["open", "expired"],
        terminal_id: "term/one",
        work_item_id: "work 1",
        recipient: "human",
        cursor: "page / 1",
        limit: 25,
      }),
    ).resolves.toEqual(response);
    expect(mockFetch).toHaveBeenCalledWith(
      "/interactions?status=open&status=expired&terminal_id=term%2Fone&work_item_id=work+1&recipient=human&cursor=page+%2F+1&limit=25",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("reads top-level Problem.code only when the interaction contract opts in", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 403,
      statusText: "Forbidden",
      json: () => Promise.resolve({ code: "content_denied", detail: "Restricted" }),
    });
    await expect(fetchJSON("/ordinary-endpoint")).rejects.toMatchObject({ kind: undefined });

    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 403,
      statusText: "Forbidden",
      json: () => Promise.resolve({ code: "content_denied", detail: "Restricted" }),
    });
    await expect(
      fetchJSON("/interactions/q1", { captureProblemCode: true }),
    ).rejects.toMatchObject({ kind: "content_denied" });
  });

  it("reads protected detail for the exact version", async () => {
    mockResponse({ question_id: "q/one", version: 3 });
    await api.getInteractionDetail("q/one", 3);
    expect(mockFetch).toHaveBeenCalledWith(
      "/interactions/q%2Fone?expected_version=3",
      expect.any(Object),
    );
  });

  it("answers with If-Match, a stable idempotency key and a typed response", async () => {
    mockResponse({ answer_id: "a1" }, 201);
    await api.answerInteraction("q/1", 7, "intent-1234567890123456", {
      kind: "options",
      option_ids: ["declared-id"],
    });

    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toBe("/interactions/q%2F1/answers");
    expect(options.method).toBe("POST");
    expect(options.body).toBe(
      JSON.stringify({
        expected_version: 7,
        response: { kind: "options", option_ids: ["declared-id"] },
      }),
    );
    expect(new Headers(options.headers).get("If-Match")).toBe('"7"');
    expect(new Headers(options.headers).get("Idempotency-Key")).toBe(
      "intent-1234567890123456",
    );
  });

  it("cancels only the exact version and records the operator reason", async () => {
    mockResponse({ question_id: "q1", lifecycle: "cancelled" });
    await api.cancelInteraction("q1", 4, "No longer needed");
    expect(mockFetch).toHaveBeenCalledWith(
      "/interactions/q1/cancel",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "If-Match": '"4"' }),
        body: JSON.stringify({ reason: "No longer needed" }),
      }),
    );
  });

  it("reconciles with an explicit outcome and bounded evidence", async () => {
    mockResponse({ question_id: "q1", delivery_state: "reconciled" });
    await api.reconcileInteraction("q1", 2, {
      outcome: "answered_outside_cao",
      evidence_code: "operator_observed_dialog_close",
      note: "Confirmed in the provider terminal.",
    });
    expect(mockFetch).toHaveBeenCalledWith(
      "/interactions/q1/reconcile",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "If-Match": '"2"' }),
        body: JSON.stringify({
          outcome: "answered_outside_cao",
          evidence_code: "operator_observed_dialog_close",
          note: "Confirmed in the provider terminal.",
        }),
      }),
    );
  });

  it("delegates to the server-derived assignment candidate for the reviewed version", async () => {
    mockResponse({ question_id: "q1", version: 5, delegation: { state: "active" } });
    await api.delegateInteraction("q1", 5, "assignment-7", "Owner approved temporary clarification", 60);
    expect(mockFetch).toHaveBeenCalledWith(
      "/interactions/q1/delegation",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "If-Match": '"5"' }),
        body: JSON.stringify({
          expected_assignment_id: "assignment-7",
          reason: "Owner approved temporary clarification",
          expires_in_seconds: 60,
        }),
      }),
    );
  });

  it("revokes a delegation using the reviewed version and reason", async () => {
    mockResponse({ question_id: "q1", version: 6, delegation: { state: "revoked" } });
    await api.revokeInteractionDelegation("q1", 6, "Owner withdrew the delegation");
    expect(mockFetch).toHaveBeenCalledWith(
      "/interactions/q1/delegation",
      expect.objectContaining({
        method: "DELETE",
        headers: expect.objectContaining({ "If-Match": '"6"' }),
        body: JSON.stringify({ reason: "Owner withdrew the delegation" }),
      }),
    );
  });

  it("loads history, wait graph and provider capabilities from their human routes", async () => {
    mockResponse({ events: [] });
    mockResponse({ nodes: [], edges: [] });
    mockResponse({ items: [] });

    await api.getInteractionHistory("q/1");
    await api.getInteractionWaitGraph();
    await api.getInteractionCapabilities();

    expect(mockFetch.mock.calls.map(([url]) => url)).toEqual([
      "/interactions/q%2F1/history",
      "/interaction-wait-graph",
      "/interaction-capabilities",
    ]);
  });

  it("self-claims content only for the exact terminal incarnation and reason", async () => {
    mockResponse({ claimed: true });
    await api.claimInteractionContentOwner(
      "term/one",
      "incarnation-1",
      "I am the operator for this local terminal.",
    );
    expect(mockFetch).toHaveBeenCalledWith(
      "/interactions/sources/term%2Fone/content-owner",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({
          expected_incarnation_id: "incarnation-1",
          reason: "I am the operator for this local terminal.",
        }),
      }),
    );
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchJSON, setActiveNode } from "../api";
import {
  admissionPolicyApi,
  AdmissionPolicyHttpError,
  AdmissionPolicyProtocolError,
  AdmissionPolicyTransportError,
  AdmissionPolicyUnsupportedError,
  type AdmissionPolicyValues,
} from "../admissionPolicyApi";

const HUGE = "9007199254740993";
const MAX_I64 = "9223372036854775807";
const PROFILE = "3d1c66dd-9f43-4f8e-b5ab-c517159d1d62";
const PRINCIPAL = `principal-${"a".repeat(64)}`;

const policy: AdmissionPolicyValues = {
  memory_per_delivery_bytes: 67_108_864n,
  cpu_per_delivery_millicores: 250n,
  memory_reserve_percent: 20n,
  cpu_reserve_percent: 20n,
  max_sample_age_s: 3,
  postgres_reserve_percent: 20n,
  postgres_statement_timeout_ms: 500n,
  sample_interval_s: 1,
  healthy_samples_before_growth: 3n,
};

function recordJson(revision = "1", memoryBytes = "67108864"): string {
  return (
    `{"profile_id":"${PROFILE}","revision":${revision},"active":true,` +
    `"policy":{"memory_per_delivery_bytes":${memoryBytes},` +
    '"cpu_per_delivery_millicores":250,"memory_reserve_percent":20,' +
    '"cpu_reserve_percent":20,"max_sample_age_s":3,' +
    '"postgres_reserve_percent":20,"postgres_statement_timeout_ms":500,' +
    '"sample_interval_s":1,"healthy_samples_before_growth":3},' +
    `"actor_principal_id":"${PRINCIPAL}",` +
    '"recorded_at":"2026-11-04T10:00:00+00:00"}'
  );
}

function stateJson(record = recordJson(), applied = "1"): string {
  return `{"persisted":${record},"applied_revision":${applied}}`;
}

describe("admission policy API codec and routes", () => {
  const mockFetch = vi.fn();

  beforeEach(() => {
    mockFetch.mockReset();
    sessionStorage.clear();
    setActiveNode(null);
    vi.stubGlobal("fetch", mockFetch);
  });

  afterEach(() => {
    setActiveNode(null);
    sessionStorage.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  function respond(text: string, status = 200): void {
    mockFetch.mockResolvedValueOnce({
      ok: status >= 200 && status < 300,
      status,
      statusText: status === 409 ? "Conflict" : "OK",
      text: () => Promise.resolve(text),
      json: () => Promise.resolve({ detail: "untrusted response text" }),
    });
  }

  it("preserves large signed-64 response integers without changing ordinary JSON decoding", async () => {
    respond(stateJson(recordJson(MAX_I64, HUGE), MAX_I64));

    const result = await admissionPolicyApi.get();

    expect(result.persisted?.revision).toBe(BigInt(MAX_I64));
    expect(result.persisted?.policy.memory_per_delivery_bytes).toBe(
      BigInt(HUGE),
    );
    expect(result.applied_revision).toBe(BigInt(MAX_I64));
    expect(mockFetch.mock.calls[0][0]).toBe("/admission/policy");
    expect(mockFetch.mock.calls[0][1]).toMatchObject({
      method: "GET",
      cache: "no-store",
      redirect: "error",
      credentials: "same-origin",
    });

    respond('{"quantity":9007199254740993}');
    await expect(fetchJSON("/ordinary")).resolves.toEqual({
      quantity: 9007199254740992,
    });
  });

  it("uses local direct routing and preserves central bearer authentication", async () => {
    setActiveNode("worker-remote");
    sessionStorage.setItem("cao.browser.bearer", "operator-token");
    respond(stateJson("null", "null"));

    await admissionPolicyApi.get();

    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toBe("/admission/policy");
    expect(new Headers(options.headers).get("Authorization")).toBe(
      "Bearer operator-token",
    );
  });

  it("does not publish a local response after the selected-node generation changes", async () => {
    let resolveResponse: (response: unknown) => void = () => undefined;
    mockFetch.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveResponse = resolve;
      }),
    );

    const pending = admissionPolicyApi.get();
    setActiveNode("worker-remote");
    resolveResponse({
      ok: true,
      status: 200,
      statusText: "OK",
      text: () => Promise.resolve(stateJson("null", "null")),
    });

    await expect(pending).rejects.toThrow("Node selection changed");
  });

  it("serializes bigint update fields as exact JSON integers and does not retry a conflict", async () => {
    respond(recordJson("2", HUGE));

    await admissionPolicyApi.update({
      expected_revision: BigInt(MAX_I64) - 1n,
      policy: { ...policy, memory_per_delivery_bytes: BigInt(HUGE) },
    });

    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toBe("/admission/policy");
    expect(options).toMatchObject({ method: "PUT", cache: "no-store" });
    expect(options.body).toContain(
      `"expected_revision":${BigInt(MAX_I64) - 1n}`,
    );
    expect(options.body).toContain(`"memory_per_delivery_bytes":${HUGE}`);

    respond("{}", 409);
    await expect(
      admissionPolicyApi.revoke({ expected_revision: 1n }),
    ).rejects.toMatchObject({
      status: 409,
      message: "Admission policy revision conflict.",
    });
    expect(mockFetch).toHaveBeenCalledTimes(2);
    expect(mockFetch.mock.calls[1][0]).toBe("/admission/policy/revoke");
    expect(mockFetch.mock.calls[1][1].method).toBe("POST");
  });

  it("builds a bounded keyset history URL from a bigint cursor", async () => {
    respond(`{"entries":[],"next_before_revision":${HUGE}}`);

    await expect(
      admissionPolicyApi.history({ limit: 25, before_revision: BigInt(HUGE) }),
    ).resolves.toEqual({
      entries: [],
      next_before_revision: BigInt(HUGE),
    });

    expect(mockFetch.mock.calls[0][0]).toBe(
      `/admission/policy/history?limit=25&before_revision=${HUGE}`,
    );
    expect(mockFetch.mock.calls[0][1]).toMatchObject({
      method: "GET",
      cache: "no-store",
    });
  });

  it.each([0, 101, 1.5, Number.NaN])(
    "rejects history page size %s before issuing a request",
    async (limit) => {
      await expect(
        admissionPolicyApi.history({ limit }),
      ).rejects.toBeInstanceOf(AdmissionPolicyProtocolError);
      expect(mockFetch).not.toHaveBeenCalled();
    },
  );

  it("rejects out-of-range revisions and incomplete policy values before mutation", async () => {
    await expect(
      admissionPolicyApi.update({ expected_revision: BigInt(MAX_I64), policy }),
    ).rejects.toBeInstanceOf(AdmissionPolicyProtocolError);
    await expect(
      admissionPolicyApi.update({
        expected_revision: 0n,
        policy: { ...policy, memory_reserve_percent: 100n },
      }),
    ).rejects.toBeInstanceOf(AdmissionPolicyProtocolError);
    await expect(
      admissionPolicyApi.update({
        expected_revision: 0n,
        policy: { ...policy, cpu_per_delivery_millicores: 1 },
      } as unknown as Parameters<typeof admissionPolicyApi.update>[0]),
    ).rejects.toBeInstanceOf(AdmissionPolicyProtocolError);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("keeps in-flight mutation cancellation uncertain and never replays it", async () => {
    const notStarted = new AbortController();
    notStarted.abort();
    await expect(
      admissionPolicyApi.update(
        { expected_revision: 0n, policy },
        notStarted.signal,
      ),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(mockFetch).not.toHaveBeenCalled();

    const pendingAbort = new AbortController();
    mockFetch.mockImplementationOnce(
      (_url: string, options: RequestInit) =>
        new Promise((_resolve, reject) => {
          options.signal?.addEventListener(
            "abort",
            () => reject(new DOMException("aborted", "AbortError")),
            { once: true },
          );
        }),
    );
    const pending = admissionPolicyApi.update(
      { expected_revision: 0n, policy },
      pendingAbort.signal,
    );
    pendingAbort.abort();

    await expect(pending).rejects.toBeInstanceOf(AdmissionPolicyTransportError);
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });

  it("rejects malformed, noncanonical, or out-of-range integer response shapes", async () => {
    respond(stateJson(recordJson("1.0")));
    await expect(admissionPolicyApi.get()).rejects.toBeInstanceOf(
      AdmissionPolicyProtocolError,
    );

    respond(stateJson(recordJson(MAX_I64 + "0")));
    await expect(admissionPolicyApi.get()).rejects.toBeInstanceOf(
      AdmissionPolicyProtocolError,
    );

    respond(stateJson(recordJson("1", "9007199254740993.0")));
    await expect(admissionPolicyApi.get()).rejects.toBeInstanceOf(
      AdmissionPolicyProtocolError,
    );

    respond('{"persisted":null,"applied_revision":null,"extra":true}');
    await expect(admissionPolicyApi.get()).rejects.toBeInstanceOf(
      AdmissionPolicyProtocolError,
    );
    expect(mockFetch).toHaveBeenCalledTimes(4);
  });

  it("rejects invalid duration semantics and history record shapes", async () => {
    const badDuration = recordJson().replace(
      '"sample_interval_s":1',
      '"sample_interval_s":3',
    );
    respond(stateJson(badDuration));
    await expect(admissionPolicyApi.get()).rejects.toBeInstanceOf(
      AdmissionPolicyProtocolError,
    );

    respond('{"entries":[{"revision":1}],"next_before_revision":null}');
    await expect(admissionPolicyApi.history()).rejects.toBeInstanceOf(
      AdmissionPolicyProtocolError,
    );

    const overflowDuration = recordJson().replace(
      '"sample_interval_s":1',
      '"sample_interval_s":1e309',
    );
    respond(stateJson(overflowDuration));
    await expect(admissionPolicyApi.get()).rejects.toBeInstanceOf(
      AdmissionPolicyProtocolError,
    );
  });

  it("fails before fetching when either native exact-JSON capability is absent", async () => {
    const originalRawJSON = Object.getOwnPropertyDescriptor(JSON, "rawJSON");
    Object.defineProperty(JSON, "rawJSON", {
      configurable: true,
      value: undefined,
    });
    try {
      await expect(admissionPolicyApi.get()).rejects.toBeInstanceOf(
        AdmissionPolicyUnsupportedError,
      );
      expect(mockFetch).not.toHaveBeenCalled();
    } finally {
      if (originalRawJSON)
        Object.defineProperty(JSON, "rawJSON", originalRawJSON);
      else Reflect.deleteProperty(JSON, "rawJSON");
    }

    const originalParse = JSON.parse;
    Object.defineProperty(JSON, "parse", {
      configurable: true,
      writable: true,
      value: (
        text: string,
        reviver?: (key: string, value: unknown) => unknown,
      ) =>
        originalParse(text, function (key, value) {
          return reviver?.(key, value);
        }),
    });
    try {
      await expect(admissionPolicyApi.get()).rejects.toBeInstanceOf(
        AdmissionPolicyUnsupportedError,
      );
      expect(mockFetch).not.toHaveBeenCalled();
    } finally {
      Object.defineProperty(JSON, "parse", {
        configurable: true,
        writable: true,
        value: originalParse,
      });
    }
  });

  it("uses the optional success decoder without forwarding it to fetch", async () => {
    respond("ready");

    await expect(
      fetchJSON<string>("/ordinary", {
        decodeSuccess: (text) => `decoded:${text}`,
      }),
    ).resolves.toBe("decoded:ready");

    expect(mockFetch.mock.calls[0][1]).not.toHaveProperty("decodeSuccess");
  });
});

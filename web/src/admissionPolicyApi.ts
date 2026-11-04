import { getNodeEpoch, fetchJSON, nodeSignal } from "./api";
import { BrowserAuthError } from "./auth";

const MAX_I64 = 9_223_372_036_854_775_807n;
const MAX_EXPECTED_REVISION = MAX_I64 - 1n;
const POLICY_PATH = "/admission/policy";

const POLICY_INTEGER_KEYS = new Set([
  "memory_per_delivery_bytes",
  "cpu_per_delivery_millicores",
  "memory_reserve_percent",
  "cpu_reserve_percent",
  "postgres_reserve_percent",
  "postgres_statement_timeout_ms",
  "healthy_samples_before_growth",
]);
const RESPONSE_INTEGER_KEYS = new Set([
  ...POLICY_INTEGER_KEYS,
  "revision",
  "applied_revision",
  "next_before_revision",
  "expected_revision",
]);

interface RawJsonCapable {
  rawJSON?: (source: string) => unknown;
}

interface JsonReviverContext {
  source?: string;
}

type SourceAwareReviver = (
  this: unknown,
  key: string,
  value: unknown,
  context?: JsonReviverContext,
) => unknown;

type SourceAwareParse = (text: string, reviver: SourceAwareReviver) => unknown;

export interface AdmissionPolicyValues {
  readonly memory_per_delivery_bytes: bigint;
  readonly cpu_per_delivery_millicores: bigint;
  readonly memory_reserve_percent: bigint;
  readonly cpu_reserve_percent: bigint;
  readonly max_sample_age_s: number;
  readonly postgres_reserve_percent: bigint;
  readonly postgres_statement_timeout_ms: bigint;
  readonly sample_interval_s: number;
  readonly healthy_samples_before_growth: bigint;
}

export interface AdmissionPolicyRecord {
  readonly profile_id: string;
  readonly revision: bigint;
  readonly active: boolean;
  readonly policy: AdmissionPolicyValues;
  readonly actor_principal_id: string;
  readonly recorded_at: string;
}

export interface AdmissionPolicyState {
  readonly persisted: AdmissionPolicyRecord | null;
  readonly applied_revision: bigint | null;
}

export interface AdmissionPolicyHistory {
  readonly entries: readonly AdmissionPolicyRecord[];
  readonly next_before_revision: bigint | null;
}

export interface AdmissionPolicyUpdate {
  readonly expected_revision: bigint;
  readonly policy: AdmissionPolicyValues;
}

export interface AdmissionPolicyRevision {
  readonly expected_revision: bigint;
}

export interface AdmissionPolicyHistoryOptions {
  readonly limit?: number;
  readonly before_revision?: bigint;
  readonly signal?: AbortSignal;
}

export class AdmissionPolicyUnsupportedError extends Error {
  constructor() {
    super(
      "This browser cannot safely exchange exact admission policy integers.",
    );
    this.name = "AdmissionPolicyUnsupportedError";
  }
}

export class AdmissionPolicyProtocolError extends Error {
  constructor() {
    super("Admission policy data does not match the required contract.");
    this.name = "AdmissionPolicyProtocolError";
  }
}

export type AdmissionPolicyHttpKind =
  | "authorization"
  | "conflict"
  | "not-found"
  | "invalid-request"
  | "unavailable"
  | "http";

const HTTP_MESSAGES: Record<AdmissionPolicyHttpKind, string> = {
  authorization: "Admission policy access is not authorized.",
  conflict: "Admission policy revision conflict.",
  "not-found": "Admission policy was not found.",
  "invalid-request": "Admission policy request was rejected.",
  unavailable: "Admission policy service is unavailable.",
  http: "Admission policy request failed.",
};

export class AdmissionPolicyHttpError extends Error {
  readonly status: number;
  readonly kind: AdmissionPolicyHttpKind;

  constructor(status: number) {
    const kind: AdmissionPolicyHttpKind =
      status === 401 || status === 403
        ? "authorization"
        : status === 404
          ? "not-found"
          : status === 409
            ? "conflict"
            : status === 422
              ? "invalid-request"
              : status === 503
                ? "unavailable"
                : "http";
    super(HTTP_MESSAGES[kind]);
    this.name = "AdmissionPolicyHttpError";
    this.status = status;
    this.kind = kind;
  }
}

export class AdmissionPolicyTransportError extends Error {
  constructor() {
    super(
      "Admission policy request failed; inspect current state before retrying.",
    );
    this.name = "AdmissionPolicyTransportError";
  }
}

function rawJsonFactory(): ((source: string) => unknown) | undefined {
  const candidate = (JSON as unknown as RawJsonCapable).rawJSON;
  return typeof candidate === "function" ? candidate : undefined;
}

function sourceAwareParser(): SourceAwareParse {
  return JSON.parse as unknown as SourceAwareParse;
}

function supportsExactJson(): boolean {
  const rawJSON = rawJsonFactory();
  if (!rawJSON) return false;

  let observedSource: string | undefined;
  try {
    sourceAwareParser()("9007199254740993", (_key, value, context) => {
      if (value === 9007199254740992) observedSource = context?.source;
      return value;
    });
    if (observedSource !== "9007199254740993") return false;
    return JSON.stringify(rawJSON("9007199254740993")) === "9007199254740993";
  } catch {
    return false;
  }
}

function requireExactJson(): void {
  if (!supportsExactJson()) throw new AdmissionPolicyUnsupportedError();
}

function parseExactJson(text: string): unknown {
  try {
    return sourceAwareParser()(text, (key, value, context) => {
      if (!RESPONSE_INTEGER_KEYS.has(key) || typeof value !== "number")
        return value;
      const source = context?.source;
      if (typeof source !== "string") throw new AdmissionPolicyProtocolError();
      const integer = BigInt(source);
      if (integer.toString() !== source)
        throw new AdmissionPolicyProtocolError();
      return integer;
    });
  } catch (error) {
    if (error instanceof AdmissionPolicyProtocolError) throw error;
    throw new AdmissionPolicyProtocolError();
  }
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(
  value: unknown,
  keys: readonly string[],
): value is Record<string, unknown> {
  if (!isPlainRecord(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return (
    actual.length === expected.length &&
    actual.every((key, index) => key === expected[index])
  );
}

function isBigIntBetween(
  value: unknown,
  min: bigint,
  max: bigint,
): value is bigint {
  return typeof value === "bigint" && value >= min && value <= max;
}

function isFinitePositiveNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

function validatePolicy(value: unknown): AdmissionPolicyValues {
  const keys = [
    "memory_per_delivery_bytes",
    "cpu_per_delivery_millicores",
    "memory_reserve_percent",
    "cpu_reserve_percent",
    "max_sample_age_s",
    "postgres_reserve_percent",
    "postgres_statement_timeout_ms",
    "sample_interval_s",
    "healthy_samples_before_growth",
  ] as const;
  if (!hasExactKeys(value, keys)) throw new AdmissionPolicyProtocolError();

  const memory = value.memory_per_delivery_bytes;
  const cpu = value.cpu_per_delivery_millicores;
  const memoryReserve = value.memory_reserve_percent;
  const cpuReserve = value.cpu_reserve_percent;
  const postgresReserve = value.postgres_reserve_percent;
  const statementTimeout = value.postgres_statement_timeout_ms;
  const healthySamples = value.healthy_samples_before_growth;
  const maxAge = value.max_sample_age_s;
  const interval = value.sample_interval_s;
  if (
    !isBigIntBetween(memory, 1n, MAX_I64) ||
    !isBigIntBetween(cpu, 1n, MAX_I64) ||
    !isBigIntBetween(memoryReserve, 0n, 99n) ||
    !isBigIntBetween(cpuReserve, 0n, 99n) ||
    !isBigIntBetween(postgresReserve, 0n, 99n) ||
    !isBigIntBetween(statementTimeout, 1n, MAX_I64) ||
    !isBigIntBetween(healthySamples, 1n, MAX_I64) ||
    !isFinitePositiveNumber(maxAge) ||
    !isFinitePositiveNumber(interval) ||
    interval >= maxAge
  )
    throw new AdmissionPolicyProtocolError();

  return value as unknown as AdmissionPolicyValues;
}

function decodeRecord(value: unknown): AdmissionPolicyRecord {
  if (
    !hasExactKeys(value, [
      "profile_id",
      "revision",
      "active",
      "policy",
      "actor_principal_id",
      "recorded_at",
    ])
  )
    throw new AdmissionPolicyProtocolError();

  const timestamp = value.recorded_at;
  if (
    typeof value.profile_id !== "string" ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(
      value.profile_id,
    ) ||
    !isBigIntBetween(value.revision, 1n, MAX_I64) ||
    typeof value.active !== "boolean" ||
    typeof value.actor_principal_id !== "string" ||
    !/^principal-[0-9a-f]{64}$/.test(value.actor_principal_id) ||
    typeof timestamp !== "string" ||
    !/(?:Z|[+-]\d{2}:\d{2})$/i.test(timestamp) ||
    !Number.isFinite(Date.parse(timestamp))
  )
    throw new AdmissionPolicyProtocolError();

  const policyValue = validatePolicy(value.policy);
  return { ...value, policy: policyValue } as unknown as AdmissionPolicyRecord;
}

function decodeState(text: string): AdmissionPolicyState {
  const value = parseExactJson(text);
  if (!hasExactKeys(value, ["persisted", "applied_revision"]))
    throw new AdmissionPolicyProtocolError();
  const persisted =
    value.persisted === null ? null : decodeRecord(value.persisted);
  const appliedRevision = value.applied_revision;
  if (
    appliedRevision !== null &&
    !isBigIntBetween(appliedRevision, 1n, MAX_I64)
  )
    throw new AdmissionPolicyProtocolError();
  return {
    persisted,
    applied_revision: appliedRevision,
  };
}

function decodeHistory(text: string): AdmissionPolicyHistory {
  const value = parseExactJson(text);
  if (!hasExactKeys(value, ["entries", "next_before_revision"]))
    throw new AdmissionPolicyProtocolError();
  const entries = value.entries;
  const cursor = value.next_before_revision;
  if (
    !Array.isArray(entries) ||
    entries.length > 100 ||
    (cursor !== null && !isBigIntBetween(cursor, 1n, MAX_I64))
  )
    throw new AdmissionPolicyProtocolError();
  return {
    entries: entries.map(decodeRecord),
    next_before_revision: cursor,
  };
}

function encodeRequestBody(
  value: AdmissionPolicyUpdate | AdmissionPolicyRevision,
): string {
  const rawJSON = rawJsonFactory();
  if (!rawJSON) throw new AdmissionPolicyUnsupportedError();
  try {
    const serialized = JSON.stringify(value, (_key, item: unknown) =>
      typeof item === "bigint" ? rawJSON(item.toString()) : item,
    );
    if (typeof serialized !== "string")
      throw new AdmissionPolicyProtocolError();
    return serialized;
  } catch (error) {
    if (error instanceof AdmissionPolicyProtocolError) throw error;
    throw new AdmissionPolicyProtocolError();
  }
}

function validateUpdate(value: AdmissionPolicyUpdate): void {
  if (
    !hasExactKeys(value, ["expected_revision", "policy"]) ||
    !isBigIntBetween(value.expected_revision, 0n, MAX_EXPECTED_REVISION)
  )
    throw new AdmissionPolicyProtocolError();
  validatePolicy(value.policy);
}

function validateRevision(value: AdmissionPolicyRevision): void {
  if (
    !hasExactKeys(value, ["expected_revision"]) ||
    !isBigIntBetween(value.expected_revision, 0n, MAX_EXPECTED_REVISION)
  )
    throw new AdmissionPolicyProtocolError();
}

function requestSignal(signal?: AbortSignal): AbortSignal {
  const currentNodeSignal = nodeSignal();
  return signal
    ? AbortSignal.any([currentNodeSignal, signal])
    : currentNodeSignal;
}

function httpStatus(error: unknown): number | undefined {
  if (error instanceof BrowserAuthError) return error.status;
  if (typeof error !== "object" || error === null) return undefined;
  const status = (error as { status?: unknown }).status;
  return typeof status === "number" && Number.isInteger(status)
    ? status
    : undefined;
}

async function request<T>(
  path: string,
  method: "GET" | "PUT" | "POST",
  decode: (text: string) => T,
  body?: () => string,
  signal?: AbortSignal,
): Promise<T> {
  requireExactJson();
  const epoch = getNodeEpoch();
  const bodyText = body?.();
  const signalForRequest = requestSignal(signal);
  if (signalForRequest.aborted)
    throw new DOMException("The operation was aborted.", "AbortError");
  const headers =
    bodyText === undefined
      ? undefined
      : { "Content-Type": "application/json", Accept: "application/json" };
  try {
    const result = await fetchJSON<T>(path, {
      method,
      ...(headers ? { headers } : {}),
      ...(bodyText === undefined ? {} : { body: bodyText }),
      cache: "no-store",
      direct: true,
      signal: signalForRequest,
      decodeSuccess: decode,
    });
    if (epoch !== getNodeEpoch()) throw new Error("Node selection changed");
    return result;
  } catch (error) {
    if (epoch !== getNodeEpoch()) {
      if (method !== "GET") throw new AdmissionPolicyTransportError();
      throw new Error("Node selection changed");
    }
    if (
      error instanceof AdmissionPolicyUnsupportedError ||
      error instanceof AdmissionPolicyProtocolError ||
      error instanceof AdmissionPolicyHttpError ||
      (method === "GET" &&
        error instanceof Error &&
        error.name === "AbortError") ||
      (error instanceof Error && error.message === "Node selection changed")
    )
      throw error;
    if (error instanceof Error && error.name === "AbortError")
      throw new AdmissionPolicyTransportError();
    const status = httpStatus(error);
    if (status !== undefined) throw new AdmissionPolicyHttpError(status);
    throw new AdmissionPolicyTransportError();
  }
}

function encodeHistoryPath(options: AdmissionPolicyHistoryOptions): string {
  const limit = options.limit ?? 50;
  const cursor = options.before_revision;
  if (
    typeof limit !== "number" ||
    !Number.isInteger(limit) ||
    limit < 1 ||
    limit > 100
  )
    throw new AdmissionPolicyProtocolError();
  if (cursor !== undefined && !isBigIntBetween(cursor, 1n, MAX_I64))
    throw new AdmissionPolicyProtocolError();
  const query = new URLSearchParams({ limit: String(limit) });
  if (cursor !== undefined) query.set("before_revision", cursor.toString());
  return `${POLICY_PATH}/history?${query.toString()}`;
}

export const admissionPolicyApi = Object.freeze({
  get(signal?: AbortSignal): Promise<AdmissionPolicyState> {
    return request(POLICY_PATH, "GET", decodeState, undefined, signal);
  },

  update(
    body: AdmissionPolicyUpdate,
    signal?: AbortSignal,
  ): Promise<AdmissionPolicyRecord> {
    return request(
      POLICY_PATH,
      "PUT",
      (text) => decodeRecord(parseExactJson(text)),
      () => {
        validateUpdate(body);
        return encodeRequestBody(body);
      },
      signal,
    );
  },

  revoke(
    body: AdmissionPolicyRevision,
    signal?: AbortSignal,
  ): Promise<AdmissionPolicyRecord> {
    return request(
      `${POLICY_PATH}/revoke`,
      "POST",
      (text) => decodeRecord(parseExactJson(text)),
      () => {
        validateRevision(body);
        return encodeRequestBody(body);
      },
      signal,
    );
  },

  async history(
    options: AdmissionPolicyHistoryOptions = {},
  ): Promise<AdmissionPolicyHistory> {
    const path = encodeHistoryPath(options);
    return request(path, "GET", decodeHistory, undefined, options.signal);
  },
});

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AdmissionPolicyProtocolError,
  AdmissionPolicyUnsupportedError,
  admissionPolicyApi,
  type AdmissionPolicyHistory,
  type AdmissionPolicyRecord,
  type AdmissionPolicyState,
  type AdmissionPolicyUpdate,
  type AdmissionPolicyValues,
} from "../admissionPolicyApi";
import { getNodeEpoch } from "../api";
import { useStore } from "../store";

type PolicyField = keyof AdmissionPolicyValues;
type PolicyDraft = Record<PolicyField, string>;
type MutablePartial<T> = { -readonly [Key in keyof T]?: T[Key] };
type InspectionReason = "conflict" | "uncertain" | "context";

type RetainedLocalUi = {
  draft: PolicyDraft | null;
  inspection: InspectionReason | null;
  notice: string | null;
};

// The Settings panel is remounted when the authenticated session or selected
// node changes. Keep only local form state in memory so an in-flight context
// change cannot silently discard a draft or an unconfirmed write.
function emptyRetainedLocalUi(): RetainedLocalUi {
  return {
    draft: null,
    inspection: null,
    notice: null,
  };
}

let retainedSessionKey: string | null = null;
let retainedLocalUi = emptyRetainedLocalUi();

function retainedForSession(sessionKey: string): RetainedLocalUi {
  if (retainedSessionKey !== sessionKey) {
    retainedSessionKey = sessionKey;
    retainedLocalUi = emptyRetainedLocalUi();
  }
  return retainedLocalUi;
}

type FieldDefinition = {
  key: PolicyField;
  label: string;
  unit: string;
  kind: "integer" | "duration";
  minimum?: bigint;
  maximum?: bigint;
};

const POLICY_FIELDS: readonly FieldDefinition[] = [
  {
    key: "memory_per_delivery_bytes",
    label: "Memoria estimada por entrega",
    unit: "bytes",
    kind: "integer",
    minimum: 1n,
  },
  {
    key: "cpu_per_delivery_millicores",
    label: "CPU estimada por entrega",
    unit: "millicores",
    kind: "integer",
    minimum: 1n,
  },
  {
    key: "memory_reserve_percent",
    label: "Reserva de memoria",
    unit: "%",
    kind: "integer",
    minimum: 0n,
    maximum: 99n,
  },
  {
    key: "cpu_reserve_percent",
    label: "Reserva de CPU",
    unit: "%",
    kind: "integer",
    minimum: 0n,
    maximum: 99n,
  },
  {
    key: "max_sample_age_s",
    label: "Antigüedad máxima de una muestra",
    unit: "segundos",
    kind: "duration",
  },
  {
    key: "postgres_reserve_percent",
    label: "Reserva de conexiones PostgreSQL",
    unit: "%",
    kind: "integer",
    minimum: 0n,
    maximum: 99n,
  },
  {
    key: "postgres_statement_timeout_ms",
    label: "Tiempo límite de sentencia PostgreSQL",
    unit: "milisegundos",
    kind: "integer",
    minimum: 1n,
  },
  {
    key: "sample_interval_s",
    label: "Intervalo de muestreo",
    unit: "segundos",
    kind: "duration",
  },
  {
    key: "healthy_samples_before_growth",
    label: "Muestras sanas antes de aumentar capacidad",
    unit: "muestras",
    kind: "integer",
    minimum: 1n,
  },
];

const MIN_SIGNED_64 = -(1n << 63n);
const MAX_SIGNED_64 = (1n << 63n) - 1n;
const HISTORY_PAGE_SIZE = 20;

type FieldErrors = Partial<Record<PolicyField, string>>;

type ContextOwner = {
  node: string | null;
  nodeEpoch: number;
  controller: AbortController;
};

function blankDraft(): PolicyDraft {
  return Object.fromEntries(
    POLICY_FIELDS.map(({ key }) => [key, ""]),
  ) as PolicyDraft;
}

function draftFromPolicy(policy: AdmissionPolicyValues): PolicyDraft {
  return Object.fromEntries(
    POLICY_FIELDS.map(({ key }) => [key, String(policy[key])]),
  ) as PolicyDraft;
}

function parseDraft(draft: PolicyDraft): {
  values: AdmissionPolicyValues | null;
  errors: FieldErrors;
} {
  const values: MutablePartial<AdmissionPolicyValues> = {};
  const errors: FieldErrors = {};

  for (const field of POLICY_FIELDS) {
    const raw = draft[field.key].trim();
    if (raw === "") {
      errors[field.key] = "Este valor es obligatorio.";
      continue;
    }

    if (field.kind === "integer") {
      if (!/^-?\d+$/.test(raw)) {
        errors[field.key] = "Escribe un entero decimal.";
        continue;
      }
      let value: bigint;
      try {
        value = BigInt(raw);
      } catch {
        errors[field.key] = "Escribe un entero decimal válido.";
        continue;
      }
      if (value < MIN_SIGNED_64 || value > MAX_SIGNED_64) {
        errors[field.key] =
          "El entero debe caber en un entero con signo de 64 bits.";
        continue;
      }
      if (field.minimum !== undefined && value < field.minimum) {
        errors[field.key] = `El valor mínimo es ${field.minimum.toString()}.`;
        continue;
      }
      if (field.maximum !== undefined && value > field.maximum) {
        errors[field.key] = `El valor máximo es ${field.maximum.toString()}.`;
        continue;
      }
      values[field.key] = value as never;
      continue;
    }

    if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(raw)) {
      errors[field.key] = "Escribe una duración decimal finita.";
      continue;
    }
    const value = Number(raw);
    if (!Number.isFinite(value) || value <= 0) {
      errors[field.key] = "Escribe una duración finita mayor que cero.";
      continue;
    }
    values[field.key] = value as never;
  }

  const freshness = values.max_sample_age_s;
  const interval = values.sample_interval_s;
  if (
    typeof freshness === "number" &&
    typeof interval === "number" &&
    interval >= freshness
  ) {
    errors.sample_interval_s =
      "Debe ser menor que la antigüedad máxima de una muestra.";
  }

  if (Object.keys(errors).length > 0) return { values: null, errors };
  return { values: values as AdmissionPolicyValues, errors };
}

function httpStatus(error: unknown): number | undefined {
  if (typeof error !== "object" || error === null || !("status" in error))
    return undefined;
  const status = (error as { status?: unknown }).status;
  return typeof status === "number" ? status : undefined;
}

function isKnownRejectedWrite(error: unknown): boolean {
  return [400, 401, 403, 404, 409, 422].includes(httpStatus(error) ?? 0);
}

function formatRevision(revision: bigint | null): string {
  return revision === null ? "Desconocida" : revision.toString();
}

function formatPolicyValue(value: bigint | number): string {
  return typeof value === "bigint" ? value.toString() : String(value);
}

function recordLabel(record: AdmissionPolicyRecord): string {
  return `perfil local ${record.profile_id}, revisión ${record.revision.toString()}`;
}

function validationFor(error: unknown): string {
  if (error instanceof AdmissionPolicyUnsupportedError) {
    return "Este navegador no puede conservar exactamente los enteros de la política. No se ha enviado ninguna solicitud.";
  }
  if (error instanceof AdmissionPolicyProtocolError) {
    return "La respuesta de política no cumple el formato esperado. No se ha aplicado a esta vista.";
  }
  if (httpStatus(error) === 403) {
    return "Tu sesión no tiene permiso de administrador para esta política.";
  }
  if (httpStatus(error) === 409) {
    return "La política cambió desde la revisión mostrada. Tu borrador se conserva; carga el estado actual antes de revisarlo de nuevo.";
  }
  if (httpStatus(error) === 422 || httpStatus(error) === 400) {
    return "El servidor rechazó los valores. Revisa los campos y vuelve a preparar una revisión.";
  }
  return "No se pudo leer la política. Comprueba la conexión y vuelve a intentarlo.";
}

export function AdmissionPolicySettings({
  sessionKey = "default",
}: {
  sessionKey?: string;
} = {}) {
  const retained = retainedForSession(sessionKey);
  const activeNode = useStore((state) => state.activeNode);
  const nodeEpoch = useStore((state) => state.nodeEpoch);
  const navLocked = useStore((state) => state.navLockCount > 0);
  const [policyState, setPolicyState] = useState<AdmissionPolicyState | null>(
    null,
  );
  const [draft, setDraft] = useState<PolicyDraft>(() =>
    retained.draft ? { ...retained.draft } : blankDraft(),
  );
  const [draftDirty, setDraftDirty] = useState(retained.draft !== null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [history, setHistory] = useState<AdmissionPolicyHistory | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [showValidation, setShowValidation] = useState(false);
  const [review, setReview] = useState<AdmissionPolicyUpdate | null>(null);
  const [confirmRevoke, setConfirmRevoke] = useState(false);
  const [needsInspection, setNeedsInspection] = useState(
    retained.inspection !== null,
  );
  const [allowExitWhileInspectionPending, setAllowExitWhileInspectionPending] =
    useState(false);
  const [inspectionReason, setInspectionReason] =
    useState<InspectionReason | null>(retained.inspection);
  const [unsupported, setUnsupported] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(
    retained.notice,
  );
  const [statusWarning, setStatusWarning] = useState<string | null>(null);
  const contextOwner = useRef<ContextOwner | null>(null);
  const mutationPending = useRef(false);
  const activeReadsRef = useRef(0);
  const loadingRef = useRef(false);
  const busyRef = useRef(busy);
  const reviewRef = useRef(review);
  const revokeConfirmationRef = useRef(confirmRevoke);
  const draftDirtyRef = useRef(draftDirty);
  busyRef.current = busy;
  reviewRef.current = review;
  revokeConfirmationRef.current = confirmRevoke;
  draftDirtyRef.current = draftDirty;

  const setDirty = (next: boolean) => {
    draftDirtyRef.current = next;
    setDraftDirty(next);
  };
  const setReviewState = (next: AdmissionPolicyUpdate | null) => {
    reviewRef.current = next;
    setReview(next);
  };
  const setRevokeConfirmation = (next: boolean) => {
    revokeConfirmationRef.current = next;
    setConfirmRevoke(next);
  };
  const setBusyState = (next: boolean) => {
    busyRef.current = next;
    setBusy(next);
  };
  const invalidateAppliedRevision = () => {
    setPolicyState((current) =>
      current ? { ...current, applied_revision: null } : current,
    );
  };

  const parsed = useMemo(() => parseDraft(draft), [draft]);

  useEffect(() => {
    const owner: ContextOwner = {
      node: activeNode,
      nodeEpoch,
      controller: new AbortController(),
    };
    contextOwner.current = owner;
    return () => {
      if (mutationPending.current) {
        retained.inspection = "context";
        retained.notice =
          "Una escritura estaba en curso al cambiar la sesión o el contexto. Consulta el estado local antes de otro cambio.";
      }
      owner.controller.abort();
      if (contextOwner.current === owner) contextOwner.current = null;
    };
  }, [activeNode, nodeEpoch]);

  const isCurrent = (owner: ContextOwner): boolean =>
    contextOwner.current === owner &&
    owner.node === null &&
    useStore.getState().activeNode === null &&
    useStore.getState().nodeEpoch === owner.nodeEpoch &&
    getNodeEpoch() === owner.nodeEpoch &&
    !owner.controller.signal.aborted;

  const markInspection = (reason: InspectionReason, message: string) => {
    retained.inspection = reason;
    retained.notice = message;
    setInspectionReason(reason);
    setNeedsInspection(true);
    setAllowExitWhileInspectionPending(false);
    setSuccessMessage(message);
    setReviewState(null);
    setRevokeConfirmation(false);
  };

  const acceptStatus = (
    fresh: AdmissionPolicyState,
    options: { preserveDraft: boolean },
  ) => {
    setPolicyState(fresh);
    setLoadError(null);
    setStatusWarning(null);
    if (!options.preserveDraft && !draftDirtyRef.current) {
      const next = fresh.persisted
        ? draftFromPolicy(fresh.persisted.policy)
        : blankDraft();
      setDraft(next);
      setDirty(false);
      retained.draft = null;
    }
  };

  const readCurrent = async (
    owner: ContextOwner,
    options: {
      purpose: "initial" | "inspection" | "after-write";
      acknowledgedRecord?: AdmissionPolicyRecord;
    },
  ) => {
    const { purpose, acknowledgedRecord } = options;
    if (purpose === "initial" && retained.inspection !== null) return;
    activeReadsRef.current += 1;
    loadingRef.current = true;
    setLoading(true);
    try {
      const fresh = await admissionPolicyApi.get(owner.controller.signal);
      if (!isCurrent(owner)) return;
      if (
        acknowledgedRecord &&
        (!fresh.persisted ||
          fresh.persisted.revision < acknowledgedRecord.revision)
      ) {
        setStatusWarning(
          `El guardado de la revisión ${acknowledgedRecord.revision.toString()} está confirmado, pero la lectura posterior devolvió una revisión anterior.`,
        );
        return;
      }
      acceptStatus(fresh, {
        preserveDraft: purpose === "inspection" && draftDirtyRef.current,
      });
      if (purpose === "inspection") {
        retained.inspection = null;
        retained.notice = null;
        setInspectionReason(null);
        setNeedsInspection(false);
        setAllowExitWhileInspectionPending(false);
        setSuccessMessage(null);
        setReviewState(null);
        setRevokeConfirmation(false);
      }
    } catch (error) {
      if (!isCurrent(owner)) {
        if (purpose !== "initial") {
          retained.inspection = "context";
          retained.notice =
            "La vista cambió durante la consulta. Inspecciona el estado local antes de otro cambio.";
        }
        return;
      }
      if (error instanceof AdmissionPolicyUnsupportedError) {
        setUnsupported(true);
        setLoadError(null);
      } else if (purpose === "initial") {
        setLoadError(validationFor(error));
      } else if (purpose === "inspection") {
        setActionError(validationFor(error));
      } else if (acknowledgedRecord) {
        setStatusWarning(
          `Guardado confirmado: ${recordLabel(acknowledgedRecord)}. No se pudo actualizar la lectura de aplicación; vuelve a consultar el estado cuando esté disponible.`,
        );
      }
    } finally {
      activeReadsRef.current = Math.max(0, activeReadsRef.current - 1);
      loadingRef.current = activeReadsRef.current > 0;
      if (isCurrent(owner)) setLoading(loadingRef.current);
    }
  };

  useEffect(() => {
    if (activeNode !== null) {
      setPolicyState(null);
      setHistory(null);
      setReviewState(null);
      setRevokeConfirmation(false);
      return;
    }
    const owner = contextOwner.current;
    if (!owner) return;
    if (retained.inspection !== null) {
      setLoading(loadingRef.current);
      return;
    }
    void readCurrent(owner, { purpose: "initial" });
    return () => {
      owner.controller.abort();
    };
  }, [activeNode, nodeEpoch]);

  useEffect(() => {
    if (activeNode !== null) setAllowExitWhileInspectionPending(false);
  }, [activeNode]);

  const protectsLocalWork =
    activeNode === null &&
    (busy ||
      (!allowExitWhileInspectionPending &&
        (draftDirty || review !== null || confirmRevoke || needsInspection)));
  useEffect(() => {
    if (!protectsLocalWork) return;
    useStore.getState().acquireNavLock();
    return () => useStore.getState().releaseNavLock();
  }, [protectsLocalWork]);

  const updateDraft = (field: PolicyField, value: string) => {
    const next = { ...draft, [field]: value };
    retained.draft = next;
    setDraft(next);
    setDirty(true);
    setAllowExitWhileInspectionPending(false);
    setReviewState(null);
    setRevokeConfirmation(false);
    setActionError(null);
    setFieldErrors({});
    setShowValidation(false);
    setSuccessMessage(null);
    retained.notice = null;
  };

  const discardDraft = () => {
    const next = policyState?.persisted
      ? draftFromPolicy(policyState.persisted.policy)
      : blankDraft();
    retained.draft = null;
    setDraft(next);
    setDirty(false);
    setReviewState(null);
    setRevokeConfirmation(false);
    setFieldErrors({});
    setShowValidation(false);
    setActionError(null);
    setSuccessMessage(null);
  };

  const beginReview = () => {
    setRevokeConfirmation(false);
    setReviewState(null);
    setShowValidation(true);
    setFieldErrors(parsed.errors);
    setActionError(null);
    if (
      !parsed.values ||
      !policyState ||
      needsInspection ||
      unsupported ||
      loadingRef.current ||
      busyRef.current
    )
      return;
    setReviewState({
      expected_revision: policyState.persisted?.revision ?? 0n,
      policy: parsed.values,
    });
  };

  const inspectCurrent = async () => {
    const owner = contextOwner.current;
    if (!owner || !isCurrent(owner) || busyRef.current || loadingRef.current)
      return;
    setActionError(null);
    await readCurrent(owner, { purpose: "inspection" });
  };

  const commitUpdate = async () => {
    const owner = contextOwner.current;
    const reviewedUpdate = reviewRef.current;
    if (
      !owner ||
      !reviewedUpdate ||
      !isCurrent(owner) ||
      needsInspection ||
      busyRef.current ||
      loadingRef.current ||
      revokeConfirmationRef.current
    )
      return;
    setBusyState(true);
    invalidateAppliedRevision();
    mutationPending.current = true;
    setActionError(null);
    try {
      const saved = await admissionPolicyApi.update(
        reviewedUpdate,
        owner.controller.signal,
      );
      if (!isCurrent(owner)) {
        retained.inspection = "context";
        retained.notice = `Una escritura local terminó después de cambiar la vista (${recordLabel(saved)}). Consulta el estado antes de otro cambio.`;
        retained.draft = draftFromPolicy(saved.policy);
        return;
      }
      mutationPending.current = false;
      const nextState: AdmissionPolicyState = {
        persisted: saved,
        applied_revision: null,
      };
      setPolicyState(nextState);
      setSuccessMessage(`Guardado confirmado: ${recordLabel(saved)}.`);
      retained.draft = null;
      retained.inspection = null;
      retained.notice = null;
      setDraft(draftFromPolicy(saved.policy));
      setDirty(false);
      setReviewState(null);
      setRevokeConfirmation(false);
      await readCurrent(owner, {
        purpose: "after-write",
        acknowledgedRecord: saved,
      });
    } catch (error) {
      if (!isCurrent(owner)) {
        retained.inspection = "context";
        retained.notice =
          "El resultado de una escritura local quedó incierto al cambiar la sesión o el contexto. Consulta el estado antes de otro cambio.";
        retained.draft = { ...draft };
        return;
      }
      if (httpStatus(error) === 409) {
        markInspection("conflict", validationFor(error));
        setActionError(validationFor(error));
      } else if (error instanceof AdmissionPolicyUnsupportedError) {
        setUnsupported(true);
        setActionError(validationFor(error));
        setReviewState(null);
      } else if (isKnownRejectedWrite(error)) {
        setActionError(validationFor(error));
        setReviewState(null);
      } else {
        markInspection(
          "uncertain",
          "No se puede confirmar el resultado del guardado. Inspecciona el estado local antes de otro cambio.",
        );
        setActionError(
          "La escritura no está confirmada. No se repetirá automáticamente; consulta el estado actual.",
        );
      }
    } finally {
      mutationPending.current = false;
      setBusyState(false);
    }
  };

  const commitRevoke = async () => {
    const owner = contextOwner.current;
    const current = policyState?.persisted;
    if (
      !owner ||
      !current ||
      !isCurrent(owner) ||
      !revokeConfirmationRef.current ||
      draftDirtyRef.current ||
      reviewRef.current !== null ||
      needsInspection ||
      busyRef.current ||
      loadingRef.current
    )
      return;
    setBusyState(true);
    invalidateAppliedRevision();
    setRevokeConfirmation(false);
    mutationPending.current = true;
    setActionError(null);
    try {
      const revoked = await admissionPolicyApi.revoke(
        { expected_revision: current.revision },
        owner.controller.signal,
      );
      if (!isCurrent(owner)) {
        retained.inspection = "context";
        retained.notice = `Una revocación local terminó después de cambiar la vista (${recordLabel(revoked)}). Consulta el estado antes de otro cambio.`;
        return;
      }
      mutationPending.current = false;
      setPolicyState({
        persisted: revoked,
        applied_revision: null,
      });
      setSuccessMessage(
        `Revocación confirmada: ${recordLabel(revoked)}. El historial se conserva.`,
      );
      setRevokeConfirmation(false);
      retained.draft = null;
      retained.inspection = null;
      retained.notice = null;
      setDraft(draftFromPolicy(revoked.policy));
      setDirty(false);
      await readCurrent(owner, {
        purpose: "after-write",
        acknowledgedRecord: revoked,
      });
    } catch (error) {
      if (!isCurrent(owner)) {
        retained.inspection = "context";
        retained.notice =
          "El resultado de una revocación local quedó incierto al cambiar la sesión o el contexto. Consulta el estado antes de otro cambio.";
        return;
      }
      if (httpStatus(error) === 409) {
        markInspection("conflict", validationFor(error));
        setActionError(validationFor(error));
      } else if (error instanceof AdmissionPolicyUnsupportedError) {
        setUnsupported(true);
        setActionError(validationFor(error));
        setRevokeConfirmation(false);
      } else if (isKnownRejectedWrite(error)) {
        setActionError(validationFor(error));
        setRevokeConfirmation(false);
      } else {
        markInspection(
          "uncertain",
          "No se puede confirmar el resultado de la revocación. Inspecciona el estado local antes de otro cambio.",
        );
        setActionError(
          "La revocación no está confirmada. No se repetirá automáticamente; consulta el estado actual.",
        );
      }
    } finally {
      mutationPending.current = false;
      setBusyState(false);
    }
  };

  const loadHistory = async (beforeRevision?: bigint) => {
    const owner = contextOwner.current;
    if (!owner || !isCurrent(owner) || historyLoading) return;
    setHistoryLoading(true);
    setActionError(null);
    try {
      const page = await admissionPolicyApi.history(
        beforeRevision === undefined
          ? { limit: HISTORY_PAGE_SIZE, signal: owner.controller.signal }
          : {
              limit: HISTORY_PAGE_SIZE,
              before_revision: beforeRevision,
              signal: owner.controller.signal,
            },
      );
      if (!isCurrent(owner)) return;
      setHistory((previous) =>
        beforeRevision === undefined || previous === null
          ? page
          : {
              entries: [...previous.entries, ...page.entries],
              next_before_revision: page.next_before_revision,
            },
      );
    } catch (error) {
      if (!isCurrent(owner)) return;
      if (error instanceof AdmissionPolicyUnsupportedError)
        setUnsupported(true);
      setActionError(validationFor(error));
    } finally {
      if (isCurrent(owner)) setHistoryLoading(false);
    }
  };

  if (activeNode !== null) {
    return (
      <section
        aria-labelledby="admission-policy-heading"
        className="mt-6 rounded-lg border border-gray-700 bg-gray-900/60 p-5"
      >
        <h2
          id="admission-policy-heading"
          className="text-lg font-semibold text-white"
        >
          Política de admisión local
        </h2>
        <p className="mt-2 text-sm text-amber-200">
          Esta sección administra la política del servidor local y del perfil
          configurado allí. El nodo remoto seleccionado no recibe esta
          configuración.
        </p>
        {retained.draft && (
          <p className="mt-2 text-sm text-gray-300">
            Hay un borrador local conservado. Volver al servidor local para
            revisarlo.
          </p>
        )}
        <button
          type="button"
          disabled={navLocked}
          onClick={() => useStore.getState().selectNode(null)}
          className="mt-4 rounded-md border border-gray-600 px-3 py-2 text-sm text-gray-100 hover:bg-gray-800 disabled:opacity-50"
        >
          Seleccionar servidor local
        </button>
      </section>
    );
  }

  const canReview =
    policyState !== null &&
    !needsInspection &&
    !unsupported &&
    !busy &&
    !loading &&
    !confirmRevoke;
  const inspectionLabel =
    inspectionReason === "conflict"
      ? "Cargar política actual"
      : "Inspeccionar estado actual";

  return (
    <section
      aria-labelledby="admission-policy-heading"
      className="mt-6 rounded-lg border border-gray-700 bg-gray-900/60 p-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2
            id="admission-policy-heading"
            className="text-lg font-semibold text-white"
          >
            Política de admisión
          </h2>
          <p className="mt-1 text-sm text-gray-400">
            Ámbito: servidor local · perfil configurado por el servidor. No
            representa una capacidad disponible.
          </p>
        </div>
        <button
          type="button"
          onClick={() => {
            const owner = contextOwner.current;
            if (
              owner &&
              !loadingRef.current &&
              !busyRef.current &&
              !reviewRef.current &&
              !revokeConfirmationRef.current
            )
              void readCurrent(owner, { purpose: "inspection" });
          }}
          disabled={
            loading ||
            busy ||
            needsInspection ||
            unsupported ||
            review !== null ||
            confirmRevoke
          }
          className="rounded-md border border-gray-600 px-3 py-2 text-sm text-gray-200 hover:bg-gray-800 disabled:opacity-50"
        >
          Actualizar estado
        </button>
      </div>

      {loading && !policyState && (
        <p role="status" className="mt-4 text-sm text-gray-400">
          Cargando estado de la política…
        </p>
      )}
      {loadError && (
        <p role="alert" className="mt-4 text-sm text-red-300">
          {loadError}
        </p>
      )}
      {unsupported && (
        <p role="alert" className="mt-4 text-sm text-amber-200">
          Este navegador no es compatible con la representación exacta de esta
          política.
        </p>
      )}

      {policyState && (
        <dl className="mt-4 grid gap-3 rounded-md border border-gray-700/70 bg-gray-950/50 p-4 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-gray-400">Perfil local</dt>
            <dd className="mt-1 break-all font-mono text-gray-100">
              {policyState.persisted?.profile_id ??
                "Aún no existe una política persistida"}
            </dd>
          </div>
          <div>
            <dt className="text-gray-400">Revisión persistida</dt>
            <dd className="mt-1 text-gray-100">
              {formatRevision(policyState.persisted?.revision ?? null)}
              {policyState.persisted && !policyState.persisted.active
                ? " · revocada"
                : ""}
            </dd>
          </div>
          <div>
            <dt className="text-gray-400">Revisión aplicada</dt>
            <dd className="mt-1 text-gray-100">
              {formatRevision(policyState.applied_revision)}
            </dd>
          </div>
          <div className="sm:col-span-2">
            <dd className="text-xs text-gray-500">
              Una revisión aplicada desconocida no acredita capacidad ni permite
              inferir recursos.
            </dd>
          </div>
        </dl>
      )}

      {successMessage && (
        <p
          role="status"
          aria-live="polite"
          className="mt-4 text-sm text-emerald-300"
        >
          {successMessage}
        </p>
      )}
      {statusWarning && (
        <p
          role="status"
          aria-live="polite"
          className="mt-3 text-sm text-amber-200"
        >
          {statusWarning}
        </p>
      )}
      {actionError && (
        <p role="alert" className="mt-3 text-sm text-red-300">
          {actionError}
        </p>
      )}

      {needsInspection && (
        <div className="mt-4 rounded-md border border-amber-700/70 bg-amber-950/30 p-4">
          <p role="status" className="text-sm text-amber-100">
            {retained.notice ??
              "Hace falta leer el estado actual antes de preparar otra modificación."}
          </p>
          {loading && (
            <p role="status" className="mt-2 text-sm text-amber-100">
              Cargando el estado actual…
            </p>
          )}
          <button
            type="button"
            onClick={() => void inspectCurrent()}
            disabled={loading || busy || unsupported}
            className="mt-3 rounded-md bg-amber-700 px-3 py-2 text-sm font-medium text-white hover:bg-amber-600 disabled:opacity-50"
          >
            {inspectionLabel}
          </button>
          {needsInspection && (
            <div className="mt-3">
              <p className="text-sm text-amber-100">
                Puedes salir conservando este estado pendiente. El borrador y la
                necesidad de inspección se mantendrán; no podrás guardar hasta
                completar una lectura actual.
              </p>
              <button
                type="button"
                onClick={() => {
                  if (
                    needsInspection &&
                    !busyRef.current &&
                    !loadingRef.current
                  )
                    setAllowExitWhileInspectionPending(true);
                }}
                disabled={busy || loading}
                className="mt-3 rounded-md border border-amber-600 px-3 py-2 text-sm text-amber-100 hover:bg-amber-900/50 disabled:opacity-50"
              >
                Permitir salir conservando el estado
              </button>
            </div>
          )}
        </div>
      )}

      {policyState && !unsupported && (
        <>
          <div className="mt-5 border-t border-gray-700 pt-5">
            <h3 className="text-base font-medium text-white">
              Valores completos
            </h3>
            {!policyState.persisted && (
              <p className="mt-2 text-sm text-amber-200">
                No hay valores guardados. Completa los nueve campos; no se
                aplicarán valores predeterminados ocultos.
              </p>
            )}
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              {POLICY_FIELDS.map((field) => {
                const error = showValidation
                  ? fieldErrors[field.key]
                  : undefined;
                const inputId = `admission-policy-${field.key}`;
                const errorId = `${inputId}-error`;
                return (
                  <div key={field.key}>
                    <label
                      htmlFor={inputId}
                      className="block text-sm font-medium text-gray-200"
                    >
                      {field.label}{" "}
                      <span className="text-gray-400">({field.unit})</span>
                    </label>
                    <input
                      id={inputId}
                      type="text"
                      inputMode={
                        field.kind === "integer" ? "numeric" : "decimal"
                      }
                      autoComplete="off"
                      required
                      value={draft[field.key]}
                      onChange={(event) =>
                        updateDraft(field.key, event.target.value)
                      }
                      aria-invalid={Boolean(error)}
                      aria-describedby={error ? errorId : undefined}
                      className="mt-1 min-h-10 w-full rounded-md border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-100 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500 disabled:opacity-50"
                      disabled={busy || needsInspection || confirmRevoke}
                    />
                    {error && (
                      <p id={errorId} className="mt-1 text-xs text-red-300">
                        {error}
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
            <div className="mt-4 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={beginReview}
                disabled={!canReview}
                className="rounded-md bg-emerald-700 px-3 py-2 text-sm font-medium text-white hover:bg-emerald-600 disabled:opacity-50"
              >
                Revisar valores
              </button>
              {draftDirty && (
                <button
                  type="button"
                  onClick={discardDraft}
                  disabled={busy || needsInspection || confirmRevoke}
                  className="rounded-md border border-gray-600 px-3 py-2 text-sm text-gray-200 hover:bg-gray-800 disabled:opacity-50"
                >
                  Descartar borrador
                </button>
              )}
              {policyState.persisted?.active && !needsInspection && (
                <button
                  type="button"
                  onClick={() => {
                    if (
                      busyRef.current ||
                      loadingRef.current ||
                      draftDirtyRef.current ||
                      reviewRef.current !== null
                    )
                      return;
                    setActionError(null);
                    setRevokeConfirmation(true);
                  }}
                  disabled={busy || loading || draftDirty || review !== null}
                  className="rounded-md border border-red-700 px-3 py-2 text-sm text-red-200 hover:bg-red-950/60 disabled:opacity-50"
                >
                  Revocar política
                </button>
              )}
            </div>
            {showValidation && Object.keys(fieldErrors).length > 0 && (
              <p role="alert" className="mt-3 text-sm text-red-300">
                Corrige los campos indicados antes de revisar.
              </p>
            )}
          </div>

          {review && (
            <section
              aria-labelledby="admission-policy-review-heading"
              className="mt-5 rounded-md border border-emerald-700/70 bg-emerald-950/20 p-4"
            >
              <h3
                id="admission-policy-review-heading"
                className="text-base font-semibold text-emerald-100"
              >
                Revisión antes de guardar
              </h3>
              <p className="mt-2 text-sm text-gray-300">
                Servidor local · perfil{" "}
                {policyState.persisted?.profile_id ?? "nuevo"} · revisión
                esperada {review.expected_revision.toString()}.
              </p>
              <dl className="mt-3 grid gap-2 sm:grid-cols-2">
                {POLICY_FIELDS.map((field) => (
                  <div key={field.key} className="rounded bg-gray-950/60 p-2">
                    <dt className="text-xs text-gray-400">{field.label}</dt>
                    <dd className="mt-1 break-all font-mono text-sm text-gray-100">
                      {formatPolicyValue(review.policy[field.key])} {field.unit}
                    </dd>
                  </div>
                ))}
              </dl>
              <div className="mt-4 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => setReviewState(null)}
                  disabled={busy}
                  className="rounded-md border border-gray-600 px-3 py-2 text-sm text-gray-200 hover:bg-gray-800 disabled:opacity-50"
                >
                  Volver a editar
                </button>
                <button
                  type="button"
                  onClick={() => void commitUpdate()}
                  disabled={busy || needsInspection}
                  className="rounded-md bg-emerald-700 px-3 py-2 text-sm font-medium text-white hover:bg-emerald-600 disabled:opacity-50"
                >
                  {busy ? "Guardando…" : "Confirmar y guardar"}
                </button>
              </div>
            </section>
          )}

          {confirmRevoke && policyState.persisted?.active && (
            <section
              role="group"
              aria-labelledby="admission-policy-revoke-heading"
              className="mt-5 rounded-md border border-red-700/70 bg-red-950/20 p-4"
            >
              <h3
                id="admission-policy-revoke-heading"
                className="font-semibold text-red-100"
              >
                Confirmar revocación
              </h3>
              <p className="mt-2 text-sm text-gray-300">
                Se revocará la política del perfil{" "}
                {policyState.persisted.profile_id}, revisión{" "}
                {policyState.persisted.revision.toString()}. El historial se
                conserva.
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => setRevokeConfirmation(false)}
                  disabled={busy}
                  className="rounded-md border border-gray-600 px-3 py-2 text-sm text-gray-200 hover:bg-gray-800 disabled:opacity-50"
                >
                  Cancelar
                </button>
                <button
                  type="button"
                  onClick={() => void commitRevoke()}
                  disabled={busy || needsInspection}
                  className="rounded-md bg-red-800 px-3 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
                >
                  {busy ? "Revocando…" : "Confirmar revocación"}
                </button>
              </div>
            </section>
          )}

          <section
            aria-labelledby="admission-policy-history-heading"
            className="mt-6 border-t border-gray-700 pt-5"
          >
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3
                  id="admission-policy-history-heading"
                  className="text-base font-medium text-white"
                >
                  Historial de cambios
                </h3>
                <p className="mt-1 text-xs text-gray-500">
                  Páginas de hasta {HISTORY_PAGE_SIZE} revisiones, ordenadas por
                  revisión.
                </p>
              </div>
              <button
                type="button"
                onClick={() => void loadHistory()}
                disabled={historyLoading || busy || needsInspection}
                className="rounded-md border border-gray-600 px-3 py-2 text-sm text-gray-200 hover:bg-gray-800 disabled:opacity-50"
              >
                {historyLoading && !history
                  ? "Cargando historial…"
                  : "Cargar historial"}
              </button>
            </div>
            {history && (
              <>
                <ol className="mt-3 space-y-2">
                  {history.entries.map((entry) => (
                    <li
                      key={`${entry.profile_id}:${entry.revision.toString()}`}
                      className="rounded-md border border-gray-700/70 bg-gray-950/40 p-3"
                    >
                      <div className="flex flex-wrap justify-between gap-2 text-sm">
                        <span className="font-medium text-gray-100">
                          Revisión {entry.revision.toString()} ·{" "}
                          {entry.active ? "activa" : "revocada"}
                        </span>
                        <time
                          className="text-gray-400"
                          dateTime={entry.recorded_at}
                        >
                          {entry.recorded_at}
                        </time>
                      </div>
                      <p className="mt-1 break-all text-xs text-gray-500">
                        Perfil {entry.profile_id} · actor{" "}
                        {entry.actor_principal_id}
                      </p>
                    </li>
                  ))}
                </ol>
                {history.entries.length === 0 && (
                  <p className="mt-3 text-sm text-gray-400">
                    No hay cambios registrados.
                  </p>
                )}
                {history.next_before_revision !== null && (
                  <button
                    type="button"
                    onClick={() =>
                      void loadHistory(
                        history.next_before_revision ?? undefined,
                      )
                    }
                    disabled={historyLoading || busy || needsInspection}
                    className="mt-3 rounded-md border border-gray-600 px-3 py-2 text-sm text-gray-200 hover:bg-gray-800 disabled:opacity-50"
                  >
                    {historyLoading
                      ? "Cargando…"
                      : "Cargar revisiones anteriores"}
                  </button>
                )}
              </>
            )}
          </section>
        </>
      )}
    </section>
  );
}

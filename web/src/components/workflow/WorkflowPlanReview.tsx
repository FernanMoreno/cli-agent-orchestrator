import { useEffect, useRef, useState } from "react";
import {
  api,
  getNodeEpoch,
  type PreparedWorkflowPlan,
  type WorkflowPlanPreparation,
} from "../../api";
import { useStore } from "../../store";

function object(text: string, label: string): Record<string, unknown> {
  const value: unknown = JSON.parse(text);
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error(`${label} must be a JSON object`);
  return value as Record<string, unknown>;
}

export function WorkflowPlanReview() {
  const [name, setName] = useState("");
  const [inputs, setInputs] = useState("{}");
  const [targets, setTargets] = useState("{}");
  const [bindings, setBindings] = useState("{}");
  const [scope, setScope] = useState("");
  const [savedId, setSavedId] = useState("");
  const [review, setReview] = useState<PreparedWorkflowPlan | null>(null);
  const material = useRef<WorkflowPlanPreparation | null>(null);
  const [approved, setApproved] = useState(false);
  const [expired, setExpired] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [run, setRun] = useState<string | null>(null);
  const alive = useRef(true);
  const pending = useRef(false);
  const release = useRef<(() => void) | null>(null);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      release.current?.();
      release.current = null;
    };
  }, []);
  useEffect(() => {
    setExpired(false);
    if (!review) return;
    const remaining = review.expires_at * 1000 - Date.now();
    if (remaining <= 0) {
      setExpired(true);
      setApproved(false);
      return;
    }
    const timer = setTimeout(() => {
      setExpired(true);
      setApproved(false);
    }, remaining);
    return () => clearTimeout(timer);
  }, [review]);
  const requireUnexpired = () => {
    if (!review || expired || Date.now() / 1000 >= review.expires_at) {
      setExpired(true);
      setApproved(false);
      throw new Error("Prepared plan expired. Prepare and review a new plan.");
    }
  };
  const invalidate = () => {
    setReview(null);
    material.current = null;
    setApproved(false);
    setRun(null);
    setError(null);
  };
  const act = async (operation: () => Promise<void>) => {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError(null);
    useStore.getState().acquireNavLock();
    let held = true;
    release.current = () => {
      if (held) {
        held = false;
        useStore.getState().releaseNavLock();
      }
    };
    const epoch = getNodeEpoch();
    try {
      await operation();
    } catch (cause) {
      if (alive.current && epoch === getNodeEpoch()) {
        const value = cause as {
          detail?: string;
          message?: string;
          kind?: string;
        };
        if (value.kind?.includes("expired")) {
          setExpired(true);
          setApproved(false);
          setError("Prepared plan expired. Prepare and review a new plan.");
        } else {
          if (
            value.kind &&
            /changed|drift|not_approved|refused|mismatch/.test(value.kind)
          )
            setApproved(false);
          setError(
            value.detail ??
              (value.kind
                ? `Plan refused: ${value.kind}. Review its scope and prepare again.`
                : value.message) ??
              "Plan operation unavailable",
          );
        }
      }
    } finally {
      release.current?.();
      release.current = null;
      pending.current = false;
      if (alive.current) setBusy(false);
    }
  };
  const prepare = () =>
    act(async () => {
      const request: WorkflowPlanPreparation = {
        name_or_path: name,
        inputs: object(inputs, "Inputs"),
        target_mappings: object(targets, "Target directories"),
        binding_selections: object(bindings, "Work bindings"),
        ...(scope.trim() ? { scope_source: scope } : {}),
      };
      const epoch = getNodeEpoch();
      const value = await api.prepareWorkflowPlan(request);
      if (!alive.current || epoch !== getNodeEpoch()) return;
      material.current = request;
      setReview(value);
      setApproved(false);
      setRun(null);
    });
  const load = () =>
    act(async () => {
      const epoch = getNodeEpoch();
      const value = await api.reviewWorkflowPlan(savedId.trim());
      if (!alive.current || epoch !== getNodeEpoch()) return;
      // A saved public review contains no launch inputs. It may be approved, but
      // starting requires preparation through this form with its exact inputs.
      material.current = null;
      setReview(value);
      setApproved(false);
      setRun(null);
    });
  const approve = () =>
    act(async () => {
      if (!review) return;
      requireUnexpired();
      const epoch = getNodeEpoch();
      const identity = review.plan_id;
      const value = await api.approveWorkflowPlan(identity);
      if (
        alive.current &&
        epoch === getNodeEpoch() &&
        value.plan_id === identity &&
        value.approved === true
      )
        setApproved(true);
    });
  const start = () =>
    act(async () => {
      if (!review || !approved || !material.current || run) return;
      requireUnexpired();
      const epoch = getNodeEpoch();
      const runId = `run_${review.prepared_id}`;
      const value = await api.submitPreparedWorkflow({
        name_or_path: material.current.name_or_path,
        inputs: material.current.inputs,
        prepared_id: review.prepared_id,
        expected_plan_id: review.plan_id,
        run_id: runId,
      });
      if (alive.current && epoch === getNodeEpoch()) setRun(value.run_id);
    });
  const edit =
    (setter: (value: string) => void) =>
    (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      setter(event.target.value);
      invalidate();
    };
  const field =
    "block w-full mt-1 rounded bg-gray-950 border border-gray-700 p-2 text-sm";
  const button = "rounded bg-gray-700 px-3 py-2 text-sm disabled:opacity-40";
  return (
    <details className="rounded-xl border border-gray-800 bg-gray-900/40 p-3 mb-4">
      <summary className="cursor-pointer text-sm font-medium">
        Prepare and review a workflow plan
      </summary>
      <div className="grid gap-3 mt-3">
        <p className="text-sm text-gray-400">
          Review the declared scope and exact plan before administrator approval
          and execution.
        </p>
        <label>
          Workflow
          <input
            className={field}
            value={name}
            disabled={busy}
            onChange={edit(setName)}
          />
        </label>
        <label>
          Inputs (JSON)
          <textarea
            className={field}
            value={inputs}
            disabled={busy}
            onChange={edit(setInputs)}
          />
        </label>
        <label>
          Target directories (JSON)
          <textarea
            className={field}
            value={targets}
            disabled={busy}
            onChange={edit(setTargets)}
          />
        </label>
        <label>
          Existing Work bindings (JSON)
          <textarea
            className={field}
            value={bindings}
            disabled={busy}
            onChange={edit(setBindings)}
          />
        </label>
        <label>
          Declared scope (YAML, optional)
          <textarea
            className={field}
            value={scope}
            disabled={busy}
            onChange={edit(setScope)}
          />
        </label>
        <button
          type="button"
          className={button}
          disabled={busy || !name.trim()}
          onClick={() => {
            void prepare();
          }}
        >
          Prepare plan
        </button>
        <div className="flex gap-2 items-end">
          <label className="grow">
            Prepared plan ID
            <input
              className={field}
              value={savedId}
              disabled={busy}
              onChange={edit(setSavedId)}
            />
          </label>
          <button
            type="button"
            className={button}
            disabled={busy || !savedId.trim()}
            onClick={() => {
              void load();
            }}
          >
            Review saved plan
          </button>
        </div>
        {review && (
          <section
            aria-label="Prepared plan review"
            className="grid gap-2 text-sm"
          >
            <p className="break-all font-mono">{review.plan_id}</p>
            <p>
              Expires: {new Date(review.expires_at * 1000).toLocaleString()}
            </p>
            <pre className="whitespace-pre-wrap break-all rounded bg-gray-950 p-3">
              {JSON.stringify(review.public_plan, null, 2)}
            </pre>
            <button
              type="button"
              className={button}
              disabled={
                busy ||
                approved ||
                expired ||
                Date.now() / 1000 >= review.expires_at
              }
              onClick={() => {
                void approve();
              }}
            >
              {approved ? "Plan approved" : "Approve this plan"}
            </button>
            <button
              type="button"
              className={button}
              disabled={
                busy ||
                !approved ||
                expired ||
                !material.current ||
                !!run ||
                Date.now() / 1000 >= review.expires_at
              }
              onClick={() => {
                void start();
              }}
            >
              Start approved run
            </button>
            {run && <p role="status">Run submitted: {run}</p>}
          </section>
        )}
        {error && (
          <p role="alert" className="text-red-300">
            {error}
          </p>
        )}
      </div>
    </details>
  );
}

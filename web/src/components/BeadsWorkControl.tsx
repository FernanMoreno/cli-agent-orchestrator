import { useEffect, useRef, useState } from "react";
import {
  api,
  getNodeEpoch,
  type Bead,
  type BeadsAssignment,
  type PreparedWorkflowPlan,
} from "../api";
import { useStore } from "../store";

export function BeadsWorkControl({
  workspace,
  task,
}: {
  workspace: string;
  task: Bead;
}) {
  const [workflow, setWorkflow] = useState("");
  const [criteria, setCriteria] = useState(
    '[{"kind":"output_equals","path":["status"],"value":"completed"}]',
  );
  const [bindings, setBindings] = useState("{}");
  const [iterations, setIterations] = useState(8);
  const [assignment, setAssignment] = useState<BeadsAssignment | null>(null);
  const [review, setReview] = useState<PreparedWorkflowPlan | null>(null);
  const [approved, setApproved] = useState(false);
  const [expired, setExpired] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [seeds, setSeeds] = useState<unknown>(null);
  const [closeReceipt, setCloseReceipt] = useState<unknown>(null);
  const closeKey = useRef("");
  const owner = useRef({
    alive: true,
    busy: false,
    sequence: 0,
    key: "",
    material: "",
    run: "",
    release: () => {},
  });
  useEffect(() => {
    owner.current.alive = true;
    return () => {
      owner.current.alive = false;
      owner.current.sequence++;
      owner.current.release();
    };
  }, []);
  useEffect(() => {
    owner.current.sequence++;
    setAssignment(null);
    setReview(null);
    setApproved(false);
    setUncertain(false);
    owner.current.key = "";
    owner.current.material = "";
    owner.current.run = "";
  }, [workspace, task.id, task.material_hash]);
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
  function requireUnexpired() {
    if (!review || Date.now() / 1000 >= review.expires_at) {
      setExpired(true);
      setApproved(false);
      throw {
        status: 409,
        message: "Task plan expired. Prepare and review a new plan.",
      };
    }
  }
  async function act(
    job: () => Promise<unknown>,
    publish: (value: any) => void,
    write = false,
  ) {
    if (owner.current.busy) return;
    owner.current.busy = true;
    setBusy(true);
    setError("");
    useStore.getState().acquireNavLock();
    let held = true;
    owner.current.release = () => {
      if (held) {
        held = false;
        useStore.getState().releaseNavLock();
      }
    };
    const epoch = getNodeEpoch(),
      sequence = owner.current.sequence;
    try {
      const value = await job();
      if (
        owner.current.alive &&
        epoch === getNodeEpoch() &&
        sequence === owner.current.sequence
      ) {
        setUncertain(false);
        publish(value);
      }
    } catch (e: any) {
      if (
        owner.current.alive &&
        epoch === getNodeEpoch() &&
        sequence === owner.current.sequence
      ) {
        setError(
          typeof e.detail === "string"
            ? e.detail
            : (e.message ??
                "Response unavailable; inspect the same assignment before retrying."),
        );
        if (write) {
          if ([400, 401, 403, 409, 422, 429].includes(e.status)) {
            setUncertain(false);
            if (
              e.status === 403 ||
              e.status === 409 ||
              /expired|changed|mismatch|not_approved|refused/.test(e.kind ?? "")
            )
              setApproved(false);
            if (/expired/.test(e.kind ?? "")) {
              setExpired(true);
              setReview(null);
              setAssignment(null);
              owner.current.key = "";
              owner.current.material = "";
            }
          } else setUncertain(true);
        }
      }
    } finally {
      owner.current.release();
      owner.current.busy = false;
      if (owner.current.alive) setBusy(false);
    }
  }
  function prepare() {
    let body: Record<string, unknown>;
    try {
      const parsedCriteria = JSON.parse(criteria),
        parsedBindings = JSON.parse(bindings);
      if (
        !Array.isArray(parsedCriteria) ||
        parsedCriteria.length < 1 ||
        parsedCriteria.length > 32 ||
        !parsedBindings ||
        typeof parsedBindings !== "object" ||
        Array.isArray(parsedBindings)
      )
        throw new Error(
          "Criteria must be a nonempty JSON list; bindings must be a JSON object.",
        );
      body = {
        workflow_name: workflow,
        expected_hash: task.material_hash,
        criteria: parsedCriteria,
        binding_selections: parsedBindings,
        max_iterations: iterations,
      };
    } catch (e: any) {
      setError(e.message ?? "Invalid task plan JSON.");
      return;
    }
    void act(
      () => {
        const material = JSON.stringify([workspace, task.id, body]);
        if (material !== owner.current.material) {
          owner.current.material = material;
          owner.current.key = crypto.randomUUID();
        }
        return api.prepareBeadAssignment(workspace, task.id, {
          ...body,
          operation_key: owner.current.key,
        });
      },
      (value) => {
        setAssignment(value);
        setReview(null);
        setApproved(false);
        owner.current.run = `bead_${value.binding_id}`;
      },
      true,
    );
  }
  function inspect() {
    if (assignment)
      void act(
        () => api.inspectBeadAssignment(assignment.binding_id),
        setAssignment,
      );
  }
  const frozen = busy || uncertain || !!assignment?.run_id;
  return (
    <details className="border rounded p-3">
      <summary>Approved Work assignment</summary>
      <p>
        Choose a published coordinator workflow and existing authorized
        bindings. Review its exact plan before approval.
      </p>
      <label>
        Coordinator workflow
        <input
          aria-label="Coordinator workflow"
          value={workflow}
          disabled={frozen}
          onChange={(e) => {
            setWorkflow(e.target.value);
            setAssignment(null);
            setReview(null);
            setApproved(false);
          }}
        />
      </label>
      <button
        disabled={busy || !workflow.trim()}
        onClick={() => void act(() => api.workflowSeeds(workflow), setSeeds)}
      >
        Inspect authorized bindings
      </button>
      {seeds !== null && <pre>{JSON.stringify(seeds, null, 2)}</pre>}
      <label>
        Completion criteria
        <textarea
          aria-label="Completion criteria"
          value={criteria}
          disabled={frozen}
          onChange={(e) => {
            setCriteria(e.target.value);
            setAssignment(null);
            setReview(null);
            setApproved(false);
          }}
        />
      </label>
      <label>
        Authorized bindings
        <textarea
          aria-label="Authorized bindings"
          value={bindings}
          disabled={frozen}
          onChange={(e) => {
            setBindings(e.target.value);
            setAssignment(null);
            setReview(null);
            setApproved(false);
          }}
        />
      </label>
      <label>
        Maximum iterations
        <input
          aria-label="Maximum iterations"
          type="number"
          min={1}
          max={64}
          value={iterations}
          disabled={frozen}
          onChange={(e) => {
            setIterations(Number(e.target.value));
            setAssignment(null);
            setReview(null);
            setApproved(false);
          }}
        />
      </label>
      <button
        disabled={busy || !!assignment?.run_id || !workflow.trim()}
        onClick={prepare}
      >
        {uncertain && !assignment
          ? "Retry same preparation"
          : "Prepare task plan"}
      </button>
      <label>
        Saved assignment
        <input
          aria-label="Saved assignment"
          value={saved}
          disabled={busy || uncertain}
          onChange={(e) => setSaved(e.target.value)}
        />
      </label>
      <button
        disabled={busy || !saved.trim()}
        onClick={() =>
          void act(
            () => api.inspectBeadAssignment(saved.trim()),
            (value) => {
              if (
                value.workspace_id !== workspace ||
                value.task_id !== task.id
              ) {
                setError("Assignment belongs to another task or workspace.");
                return;
              }
              setAssignment(value);
              owner.current.run = `bead_${value.binding_id}`;
              setReview(null);
              setApproved(false);
            },
          )
        }
      >
        Inspect saved assignment
      </button>
      {assignment && (
        <section>
          <p>Assignment: {assignment.binding_id}</p>
          <p>
            State: {assignment.state}. Completion:{" "}
            {assignment.work_verified_completed ? "verified" : "unverified"}.
          </p>
          <button disabled={busy} onClick={inspect}>
            Inspect assignment
          </button>
          <button
            disabled={busy || !!assignment.run_id}
            onClick={() =>
              void act(
                () => api.reviewWorkflowPlan(assignment.prepared_id),
                setReview,
              )
            }
          >
            Review task plan
          </button>
          {review && (
            <>
              <pre>{JSON.stringify(review.public_plan, null, 2)}</pre>
              <p>
                Expires: {new Date(review.expires_at * 1000).toLocaleString()}
              </p>
              <button
                disabled={
                  busy ||
                  approved ||
                  expired ||
                  Date.now() / 1000 >= review.expires_at ||
                  review.plan_id !== assignment.plan_id
                }
                onClick={() =>
                  void act(
                    () => {
                      requireUnexpired();
                      return api.approveWorkflowPlan(review.plan_id);
                    },
                    (value) =>
                      setApproved(
                        value.approved === true &&
                          value.plan_id === review.plan_id,
                      ),
                  )
                }
              >
                Approve task plan
              </button>
              <button
                disabled={
                  busy ||
                  !approved ||
                  expired ||
                  Date.now() / 1000 >= review.expires_at ||
                  !!assignment.run_id
                }
                onClick={() =>
                  void act(
                    () => {
                      requireUnexpired();
                      return api.startBeadAssignment(
                        assignment.binding_id,
                        review.plan_id,
                        owner.current.run,
                      );
                    },
                    setAssignment,
                    true,
                  )
                }
              >
                {uncertain ? "Retry same task run" : "Start approved task"}
              </button>
            </>
          )}
          {assignment.run_id && (
            <button
              disabled={
                busy ||
                assignment.state === "stopped" ||
                assignment.state === "completed"
              }
              onClick={() =>
                void act(
                  () => api.unassignBead(assignment.binding_id),
                  setAssignment,
                  true,
                )
              }
            >
              Unassign and stop
            </button>
          )}
          {assignment.work_verified_completed && (
            <button
              disabled={busy}
              onClick={() => {
                if (!closeKey.current) closeKey.current = crypto.randomUUID();
                void act(
                  () =>
                    api.closeVerifiedBead(assignment.binding_id, {
                      operation_key: closeKey.current,
                      expected_hash: task.material_hash,
                    }),
                  setCloseReceipt,
                  true,
                );
              }}
            >
              Close verified external task
            </button>
          )}
          {closeReceipt !== null && (
            <pre>{JSON.stringify(closeReceipt, null, 2)}</pre>
          )}
          {assignment.coordinator && (
            <pre>{JSON.stringify(assignment.coordinator, null, 2)}</pre>
          )}
        </section>
      )}
      {error && <p role="alert">{error}</p>}
    </details>
  );
}

import { useEffect, useRef, useState } from "react";
import { api, getNodeEpoch } from "../api";
import { ralphApi, type RalphPrepared, type RalphStatus } from "../ralphApi";
import { useStore } from "../store";

export function RalphPanel() {
  const node = useStore((s) => s.activeNode);
  const [identity, setIdentity] = useState(""),
    [status, setStatus] = useState<RalphStatus | null>(null);
  const [prepared, setPrepared] = useState<RalphPrepared | null>(null),
    [approved, setApproved] = useState(false);
  const [provider, setProvider] = useState(""),
    [agent, setAgent] = useState(""),
    [workflow, setWorkflow] = useState("");
  const [request, setRequest] = useState(
    '{"workflow_name":"","task":{"description":""},"criteria":[],"target_mappings":{},"binding_selections":{},"max_iterations":8}',
  );
  const [runId, setRunId] = useState(""),
    [feedback, setFeedback] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const owner = useRef({ alive: true, sequence: 0, busy: false });
  useEffect(
    () => () => {
      owner.current.alive = false;
      owner.current.sequence++;
      if (owner.current.busy) {
        owner.current.busy = false;
        useStore.getState().releaseNavLock();
      }
    },
    [],
  );
  useEffect(() => {
    owner.current.sequence++;
    setStatus(null);
    setPrepared(null);
    setApproved(false);
    setIdentity("");
    setError("");
  }, [node]);
  async function perform<T>(
    job: () => Promise<T>,
    publish: (value: T) => void,
  ) {
    if (owner.current.busy) return;
    owner.current.busy = true;
    setBusy(true);
    setError("");
    useStore.getState().acquireNavLock();
    const sequence = ++owner.current.sequence,
      epoch = getNodeEpoch();
    try {
      const value = await job();
      if (
        owner.current.alive &&
        sequence === owner.current.sequence &&
        epoch === getNodeEpoch()
      )
        publish(value);
    } catch (e: any) {
      if (owner.current.alive && sequence === owner.current.sequence)
        setError(
          e.detail ??
            e.message ??
            "Request unavailable; inspect the run before retrying.",
        );
    } finally {
      if (owner.current.busy) {
        owner.current.busy = false;
        useStore.getState().releaseNavLock();
      }
      if (owner.current.alive) setBusy(false);
    }
  }
  const box = "rounded bg-gray-800 border border-gray-600 p-2";
  return (
    <section className="p-4 space-y-4" aria-label="Ralph bounded continuation">
      <h2 className="text-lg">Ralph</h2>
      <p>
        Runs use a reviewed workflow, existing Work bindings and finite
        iteration limits. Completion requires an accepted result that satisfies
        the reviewed criteria.
      </p>
      <div className="flex gap-2 flex-wrap">
        <input
          className={box}
          aria-label="Provider"
          placeholder="Provider"
          value={provider}
          onChange={(e) => setProvider(e.target.value)}
        />
        <input
          className={box}
          aria-label="Agent profile"
          placeholder="Agent profile"
          value={agent}
          onChange={(e) => setAgent(e.target.value)}
        />
        <button
          disabled={busy || !provider || !agent}
          onClick={() =>
            perform(
              () => ralphApi.template(provider, agent, "exact-snapshot"),
              (v) => setWorkflow(v.workflow_name),
            )
          }
        >
          Create workflow template
        </button>
      </div>
      {workflow && (
        <p>
          Workflow: {workflow}. Use its source hash and the workflow binding
          controls to provision an existing authorized seed before preparing.
        </p>
      )}
      <label className="block">
        Preparation request JSON
        <textarea
          className={box + " block w-full h-32"}
          aria-label="Preparation request JSON"
          value={request}
          onChange={(e) => {
            setRequest(e.target.value);
            setPrepared(null);
            setApproved(false);
          }}
        />
      </label>
      <button
        disabled={busy}
        onClick={() =>
          perform(
            async () => ralphApi.prepare(JSON.parse(request)),
            (v) => {
              setPrepared(v);
              setApproved(false);
            },
          )
        }
      >
        Prepare review
      </button>
      {prepared && (
        <div className="space-y-2">
          <pre className="overflow-auto">
            {JSON.stringify(prepared, null, 2)}
          </pre>
          <button
            disabled={busy || approved}
            onClick={() =>
              perform(
                () => api.approveWorkflowPlan(prepared.plan_id),
                () => setApproved(true),
              )
            }
          >
            Approve reviewed plan
          </button>
          <input
            className={box}
            aria-label="New run ID"
            value={runId}
            placeholder="Unique run ID"
            onChange={(e) => setRunId(e.target.value)}
          />
          <button
            disabled={busy || !approved || !runId}
            onClick={() =>
              perform(
                () =>
                  ralphApi.start(prepared.prepared_id, prepared.plan_id, runId),
                (v) => setIdentity(v.coordinator_id),
              )
            }
          >
            Start approved run
          </button>
        </div>
      )}
      <div className="flex gap-2">
        <input
          className={box}
          aria-label="Run or coordinator ID"
          value={identity}
          onChange={(e) => {
            setIdentity(e.target.value);
            setStatus(null);
          }}
        />
        <button
          disabled={busy || !identity}
          onClick={() => perform(() => ralphApi.status(identity), setStatus)}
        >
          Refresh status
        </button>
      </div>
      {status && (
        <div>
          <p>{status.state}</p>
          <p>
            Iteration {status.iteration} of {status.max_iterations}; driver{" "}
            {status.driver_state}
          </p>
          {status.pause_reason && <p>{status.pause_reason}</p>}
          {status.escalation_reason && <p>{status.escalation_reason}</p>}
          {status.work_verified_completed && <p>Verified completed</p>}
          <pre className="overflow-auto">
            {JSON.stringify(status.events, null, 2)}
          </pre>
        </div>
      )}
      <label className="block">
        Future iteration feedback
        <textarea
          className={box + " block w-full"}
          aria-label="Future iteration feedback"
          value={feedback}
          maxLength={4096}
          onChange={(e) => setFeedback(e.target.value)}
        />
      </label>
      <button
        disabled={busy || !identity || !feedback}
        onClick={() =>
          perform(
            () => ralphApi.feedback(identity, crypto.randomUUID(), feedback),
            setStatus,
          )
        }
      >
        Queue feedback
      </button>
      <button
        disabled={busy || !identity}
        onClick={() =>
          perform(
            () => ralphApi.resume(identity),
            () => {},
          )
        }
      >
        Resume with current authority
      </button>
      <button
        disabled={busy || !identity}
        onClick={() => perform(() => ralphApi.stop(identity), setStatus)}
      >
        Stop and check cessation
      </button>
      <button
        disabled={busy || !identity}
        onClick={() => perform(() => ralphApi.complete(identity), setStatus)}
      >
        Verify completion
      </button>
      {error && <p role="alert">{error}</p>}
    </section>
  );
}

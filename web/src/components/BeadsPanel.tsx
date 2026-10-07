import { useEffect, useRef, useState } from "react";
import {
  api,
  getNodeEpoch,
  type Bead,
  type BeadsCapabilities,
  type BeadsReceipt,
} from "../api";
import { useStore } from "../store";
import { BeadsWorkControl } from "./BeadsWorkControl";
import { BeadsBulkPlanner } from "./BeadsBulkPlanner";

export function BeadsPanel() {
  const node = useStore((s) => s.activeNode);
  const [capability, setCapability] = useState<BeadsCapabilities | null>(null);
  const [workspace, setWorkspace] = useState("");
  const [tasks, setTasks] = useState<Bead[]>([]);
  const [selected, setSelected] = useState<Bead | null>(null);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState(2);
  const [action, setAction] = useState("comment");
  const [value, setValue] = useState("");
  const [receipt, setReceipt] = useState<BeadsReceipt | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState<unknown>(null);
  const owner = useRef({
    alive: true,
    sequence: 0,
    busy: false,
    key: "",
    material: "",
    creating: true,
  });
  useEffect(() => {
    owner.current.alive = true;
    return () => {
      owner.current.alive = false;
      owner.current.sequence++;
      if (owner.current.busy) {
        owner.current.busy = false;
        useStore.getState().releaseNavLock();
      }
    };
  }, []);
  useEffect(() => {
    ++owner.current.sequence;
    const epoch = getNodeEpoch();
    setCapability(null);
    setWorkspace("");
    setTasks([]);
    setSelected(null);
    setReceipt(null);
    setError("");
    api
      .beadsCapabilities()
      .then((result) => {
        if (owner.current.alive && epoch === getNodeEpoch()) {
          setCapability(result);
          setWorkspace(result.workspaces[0]?.id ?? "");
        }
      })
      .catch((e) => {
        if (owner.current.alive && epoch === getNodeEpoch())
          setError(e.detail ?? e.message);
      });
  }, [node]);
  useEffect(() => {
    const sequence = ++owner.current.sequence;
    setTasks([]);
    setSelected(null);
    setReceipt(null);
    setDetail(null);
    if (!workspace) return;
    api
      .listBeads(workspace)
      .then((result) => {
        if (owner.current.alive && sequence === owner.current.sequence)
          setTasks(result.tasks);
      })
      .catch((e) => {
        if (owner.current.alive && sequence === owner.current.sequence)
          setError(e.detail ?? e.message);
      });
  }, [workspace]);
  async function run(
    job: () => Promise<unknown>,
    publish: (result: any) => void,
  ) {
    if (owner.current.busy) return;
    owner.current.busy = true;
    setBusy(true);
    setError("");
    useStore.getState().acquireNavLock();
    const epoch = getNodeEpoch(),
      sequence = owner.current.sequence;
    try {
      const result = await job();
      if (
        owner.current.alive &&
        epoch === getNodeEpoch() &&
        sequence === owner.current.sequence
      )
        publish(result);
    } catch (e: any) {
      if (
        owner.current.alive &&
        epoch === getNodeEpoch() &&
        sequence === owner.current.sequence
      )
        setError(
          e.detail ??
            e.message ??
            "Response unavailable; inspect before retrying.",
        );
    } finally {
      if (owner.current.busy) {
        owner.current.busy = false;
        useStore.getState().releaseNavLock();
      }
      if (owner.current.alive) setBusy(false);
    }
  }
  function mutate(create: boolean) {
    owner.current.creating = create;
    let edited: Record<string, unknown> = {};
    if (!create && action === "update") {
      try {
        edited = JSON.parse(value);
        if (!edited || typeof edited !== "object" || Array.isArray(edited))
          throw new Error();
      } catch {
        setError("Update requires a JSON object.");
        return;
      }
    }
    const values = create
      ? { title, description, priority }
      : action === "update"
        ? edited
        : action === "close" || action === "delete"
          ? {}
          : {
              [action === "label_add" || action === "label_remove"
                ? "label"
                : action === "dep_add" || action === "dep_remove"
                  ? "depends_on"
                  : action]: value,
            };
    const body: Record<string, unknown> = {
      action: create ? "create" : action,
      values,
      ...(!create && selected
        ? { task_id: selected.id, expected_hash: selected.material_hash }
        : {}),
    };
    const material = JSON.stringify([workspace, body]);
    if (owner.current.material !== material) {
      owner.current.material = material;
      owner.current.key = crypto.randomUUID();
    }
    body.operation_key = owner.current.key;
    const epochAtStart = getNodeEpoch(),
      sequenceAtStart = owner.current.sequence;
    void run(
      () =>
        api.mutateBead(workspace, body).catch((e: any) => {
          if ([400, 401, 403, 409, 422, 429].includes(e.status)) throw e;
          return {
            operation_id: "",
            state: "uncertain",
            error_kind: "beads_response_unavailable",
          };
        }),
      (result) => {
        setReceipt(result);
        if (result.state === "applied")
          void api
            .listBeads(workspace)
            .then((next) => {
              if (
                owner.current.alive &&
                getNodeEpoch() === epochAtStart &&
                owner.current.sequence === sequenceAtStart
              )
                setTasks(next.tasks);
            })
            .catch(() => {});
      },
    );
  }
  const uncertain =
    receipt?.state === "uncertain" || receipt?.state === "partial";
  return (
    <section className="space-y-4">
      <h2>External tasks</h2>
      {error && <p role="alert">{error}</p>}
      {capability && !capability.available && (
        <p>{capability.error_kind ?? "Task integration unavailable"}</p>
      )}
      {capability?.available && (
        <>
          <label>
            Workspace
            <select
              aria-label="Workspace"
              disabled={busy || !!uncertain}
              value={workspace}
              onChange={(e) => setWorkspace(e.target.value)}
            >
              {capability.workspaces.map((w) => (
                <option key={w.id}>{w.id}</option>
              ))}
            </select>
          </label>
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              {tasks.map((task) => (
                <button
                  className="block"
                  key={task.id}
                  disabled={busy || !!uncertain}
                  onClick={() => {
                    setSelected(task);
                    setReceipt(null);
                    setDetail(null);
                  }}
                >
                  {task.title} · {task.external_status}
                  {task.work_verified_completed ? " · verified" : ""}
                </button>
              ))}
            </div>
            <div>
              <h3>Create task</h3>
              <label>
                Title
                <input
                  aria-label="Title"
                  disabled={busy || !!uncertain}
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </label>
              <label>
                Description
                <textarea
                  aria-label="Description"
                  disabled={busy || !!uncertain}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </label>
              <label>
                Priority
                <select
                  aria-label="Priority"
                  disabled={busy || !!uncertain}
                  value={priority}
                  onChange={(e) => setPriority(Number(e.target.value))}
                >
                  {[0, 1, 2, 3, 4].map((p) => (
                    <option key={p}>{p}</option>
                  ))}
                </select>
              </label>
              <button
                disabled={busy || !!uncertain || !title.trim()}
                onClick={() => mutate(true)}
              >
                Create task
              </button>
            </div>
          </div>
          <BeadsBulkPlanner key={workspace} workspace={workspace} />
          {selected && (
            <div>
              <h3>{selected.title}</h3>
              <p>{selected.description}</p>
              <p>
                External status: {selected.external_status}. Work completion:{" "}
                {selected.work_verified_completed ? "verified" : "unverified"}.
              </p>
              {selected.work_assignment && (
                <p>
                  Work run:{" "}
                  {selected.work_assignment.run_id ?? "Awaiting start"} ·{" "}
                  {selected.work_assignment.state}
                </p>
              )}
              {!!selected.work_attempts?.length && (
                <ul aria-label="Work attempts">
                  {selected.work_attempts.map((attempt) => (
                    <li key={attempt.id}>
                      {attempt.step_id} · {attempt.state} ·{" "}
                      {attempt.terminal_id ?? "No terminal attached"}
                    </li>
                  ))}
                </ul>
              )}
              {selected.work_attempts_truncated && (
                <p>Showing the first 64 Work attempts.</p>
              )}
              <label>
                Change
                <select
                  aria-label="Change"
                  disabled={busy || !!uncertain}
                  value={action}
                  onChange={(e) => setAction(e.target.value)}
                >
                  {[
                    "update",
                    "comment",
                    "notes",
                    "label_add",
                    "label_remove",
                    "dep_add",
                    "dep_remove",
                    "close",
                    "delete",
                  ].map((a) => (
                    <option key={a} value={a}>
                      {a.replace(/_/g, " ")}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Value
                <textarea
                  aria-label="Value"
                  disabled={busy || !!uncertain}
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                />
              </label>
              <button
                disabled={busy || !!uncertain}
                onClick={() => mutate(false)}
              >
                Apply change
              </button>
              <button
                disabled={busy}
                onClick={() =>
                  void run(
                    () => api.beadsComments(workspace, selected.id),
                    setDetail,
                  )
                }
              >
                Read comments
              </button>
              <button
                disabled={busy}
                onClick={() =>
                  void run(
                    () => api.beadsEpic(workspace, selected.id),
                    setDetail,
                  )
                }
              >
                Read epic
              </button>
              <BeadsWorkControl
                key={`${workspace}:${selected.id}`}
                workspace={workspace}
                task={selected}
              />
            </div>
          )}
          {receipt && (
            <div>
              <p>{receipt.state}</p>
              <p>{receipt.operation_id || owner.current.key}</p>
              {uncertain && (
                <button
                  disabled={busy}
                  onClick={() => mutate(owner.current.creating)}
                >
                  Retry same operation
                </button>
              )}
              <button
                disabled={busy || !receipt.operation_id}
                onClick={() =>
                  void run(
                    () => api.inspectBeadsOperation(receipt.operation_id, true),
                    setDetail,
                  )
                }
              >
                Inspect outcome
              </button>
            </div>
          )}
          {detail !== null && <pre>{JSON.stringify(detail, null, 2)}</pre>}
        </>
      )}
    </section>
  );
}

import { useEffect, useRef, useState } from "react";
import { api, getNodeEpoch } from "../api";
import { useStore } from "../store";

export function BeadsBulkPlanner({ workspace }: { workspace: string }) {
  const [text, setText] = useState(""),
    [epic, setEpic] = useState(""),
    [sequential, setSequential] = useState(false);
  const [draft, setDraft] = useState<{
    tasks: Record<string, unknown>[];
    draft_hash: string;
  } | null>(null);
  const [receipt, setReceipt] = useState<Record<string, unknown> | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const owner = useRef({
    alive: true,
    busy: false,
    key: "",
    body: null as Record<string, unknown> | null,
    release: () => {},
  });
  useEffect(() => {
    owner.current.alive = true;
    return () => {
      owner.current.alive = false;
      owner.current.release();
    };
  }, []);
  async function act(
    job: () => Promise<any>,
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
    const epoch = getNodeEpoch();
    try {
      const value = await job();
      if (owner.current.alive && epoch === getNodeEpoch()) publish(value);
    } catch (e: any) {
      if (owner.current.alive && epoch === getNodeEpoch()) {
        setError(
          typeof e.detail === "string"
            ? e.detail
            : (e.message ?? "Task operation unavailable"),
        );
        if (write && ![400, 401, 403, 409, 422, 429].includes(e.status))
          setReceipt({ state: "uncertain", operation_key: owner.current.key });
      }
    } finally {
      owner.current.release();
      owner.current.busy = false;
      if (owner.current.alive) setBusy(false);
    }
  }
  function create() {
    if (!draft) return;
    if (!owner.current.body) {
      owner.current.key = crypto.randomUUID();
      owner.current.body = {
        operation_key: owner.current.key,
        tasks: draft.tasks,
        draft_hash: draft.draft_hash,
        ...(epic.trim() ? { epic: { title: epic.trim() } } : {}),
        sequential,
      };
    }
    void act(
      () => api.bulkCreateBeads(workspace, owner.current.body!),
      setReceipt,
      true,
    );
  }
  const retained = !!owner.current.body;
  return (
    <details className="border rounded p-3">
      <summary>Plan tasks and epic</summary>
      <p>
        One task title per line. Review the complete draft before creating
        external metadata.
      </p>
      <label>
        Task draft
        <textarea
          aria-label="Task draft"
          value={text}
          disabled={busy || retained}
          onChange={(e) => {
            setText(e.target.value);
            setDraft(null);
          }}
        />
      </label>
      <label>
        Epic title
        <input
          aria-label="Epic title"
          value={epic}
          disabled={busy || retained}
          onChange={(e) => setEpic(e.target.value)}
        />
      </label>
      <label>
        <input
          type="checkbox"
          aria-label="Sequential dependencies"
          checked={sequential}
          disabled={busy || retained}
          onChange={(e) => setSequential(e.target.checked)}
        />
        Sequential dependencies
      </label>
      <button
        disabled={busy || retained || !text.trim()}
        onClick={() => void act(() => api.decomposeBeads(text), setDraft)}
      >
        Preview tasks
      </button>
      {draft && (
        <>
          <pre>{JSON.stringify(draft.tasks, null, 2)}</pre>
          <button
            disabled={busy || receipt?.state === "applied"}
            onClick={create}
          >
            {retained ? "Retry same draft operation" : "Create reviewed tasks"}
          </button>
        </>
      )}
      {receipt && (
        <>
          <p>Task creation: {String(receipt.state)}</p>
          <pre>{JSON.stringify(receipt, null, 2)}</pre>
          {typeof receipt.operation_id === "string" && (
            <button
              disabled={busy}
              onClick={() =>
                void act(
                  () =>
                    api.inspectBeadsOperation(
                      String(receipt.operation_id),
                      true,
                    ),
                  (value) => setReceipt(value),
                )
              }
            >
              Inspect draft outcome
            </button>
          )}
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </details>
  );
}

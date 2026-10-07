import { fetchJSON } from "./api";
export interface RalphStatus {
  coordinator_id: string;
  run_id: string;
  mode: "ralph" | "beads";
  state: string;
  revision: number;
  iteration: number;
  min_iterations: number;
  max_iterations: number;
  deadline: number;
  driver_state: string;
  pause_reason: string | null;
  escalation_reason: string | null;
  work_verified_completed: boolean;
  events: {
    kind: string;
    iteration: number;
    accepted_result_id: string | null;
    content_hash: string;
    public_evidence_json: string;
  }[];
}
export interface RalphPrepared {
  prepared_id: string;
  plan_id: string;
  source_hash: string;
  expires_at: number;
  public_plan: unknown;
  scope_summary: unknown;
}
const post = <T>(path: string, body: unknown = {}) =>
  fetchJSON<T>("/ralph" + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
const id = (value: string) => encodeURIComponent(value);
export const ralphApi = {
  template: (provider: string, agent: string, memory: string) =>
    post<{ workflow_name: string; source_hash: string }>("/templates", {
      provider,
      agent,
      memory,
    }),
  prepare: (body: unknown) => post<RalphPrepared>("/plans:prepare", body),
  start: (prepared_id: string, expected_plan_id: string, run_id: string) =>
    post<{ coordinator_id: string; run_id: string; state: string }>("/runs", {
      prepared_id,
      expected_plan_id,
      run_id,
    }),
  status: (identity: string) =>
    fetchJSON<RalphStatus>("/ralph/runs/" + id(identity)),
  feedback: (identity: string, request_id: string, text: string) =>
    post<RalphStatus>("/runs/" + id(identity) + "/feedback", {
      request_id,
      text,
    }),
  stop: (identity: string) =>
    post<RalphStatus>("/runs/" + id(identity) + "/stop"),
  complete: (identity: string) =>
    post<RalphStatus>("/runs/" + id(identity) + "/complete"),
  resume: (identity: string) =>
    post<{ run_id: string; state: string }>(
      "/runs/" + id(identity) + "/resume",
    ),
};

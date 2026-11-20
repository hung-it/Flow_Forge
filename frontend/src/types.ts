export type Role = "OWNER" | "DEVELOPER" | "VIEWER";
export type Project = { id: string; name: string; role: Role };
export type Member = { user_id: string; email: string; role: Role };
export type Workflow = { id: string; name: string; project_id: string; latest_version: number | null };
export type Draft = { revision: number; dag: Record<string, unknown> };
export type Dag = {
  nodes: { key: string; handler: string }[];
  edges: [string, string][];
};
export type Version = {
  version: number; checksum: string; source_draft_revision: number; created_at: string;
};
export type Published = Version & { id: string; dag: Dag };
export type RunStatus = "PENDING" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELED";
export type Run = {
  run_id: string; project_id: string; workflow_id: string; workflow_version_id: string;
  version: number; created_by: string; status: RunStatus;
  created_at: string; started_at: string | null; finished_at: string | null;
};
export type Task = {
  task_id: string; node_key: string; status: string;
  attempt_count: number; predecessor_task_ids: string[];
};
export type Attempt = {
  attempt_no: number; dispatch_generation: number; worker_id: string; status: string;
  error_code: string | null; started_at: string; finished_at: string | null;
};
export const active = (run: Run) => run.status === "PENDING" || run.status === "RUNNING";

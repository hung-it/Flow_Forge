import { useEffect, useRef, useState } from "react";
import { api, ApiError, errorText, isAbort, json } from "./api";
import { DagView } from "./DagView";
import { active } from "./types";
import type { Attempt, Project, Published, Run, Task, Workflow } from "./types";
import { Badge, Confirm, date, listPath, Notice, Pager, pageSize, PageTitle, projectPath, useLoad } from "./ui";

export function Runs({ project, initialWorkflow }: { project: Project; initialWorkflow: string }) {
  const [workflow, setWorkflow] = useState(initialWorkflow);
  const [status, setStatus] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [filters, setFilters] = useState({ workflow_id: initialWorkflow, status: "", created_from: "", created_to: "" });
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState("");
  const result = useLoad<Run[]>(listPath("/runs", offset, { project_id: project.id, ...filters }), refresh);
  const workflows = useLoad<Workflow[]>("/projects/" + project.id + "/workflows?limit=100");
  const names = new Map(workflows?.data?.map(item => [item.id, item.name]));

  return <>
    <PageTitle title="Runs" description="Browse the execution history for this project."
      action={<button className="secondary" onClick={() => setRefresh(value => value + 1)}>Refresh runs</button>} />
    <section className="panel">
      <form className="form-row" onSubmit={event => {
        event.preventDefault();
        setError("");
        const start = from ? new Date(from) : null, end = to ? new Date(to) : null;
        if (start && end && start >= end) {
          setError("Created before must be later than created from.");
          return;
        }
        setFilters({ workflow_id: workflow.trim(), status,
          created_from: start?.toISOString() ?? "", created_to: end?.toISOString() ?? "" });
        setOffset(0);
      }}>
        <label>Workflow<input value={workflow} list="run-workflows" placeholder="All workflows"
          onChange={event => setWorkflow(event.target.value)} /></label>
        <datalist id="run-workflows">{workflows?.data?.map(item =>
          <option key={item.id} value={item.id}>{item.name}</option>)}</datalist>
        <label>Status<select value={status} onChange={event => setStatus(event.target.value)}>
          <option value="">All statuses</option>
          {["PENDING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED"].map(value =>
            <option key={value} value={value}>{value}</option>)}
        </select></label>
        <label>Created from<input type="datetime-local" value={from}
          onChange={event => setFrom(event.target.value)} /></label>
        <label>Created before<input type="datetime-local" value={to}
          onChange={event => setTo(event.target.value)} /></label>
        <button>Apply filters</button>
      </form>
      <Notice error>{error || result?.error}</Notice>
      {!result && <Notice>Loading runs…</Notice>}
      {result?.data && <>
        {result.data.length ? <div className="table-scroll"><table>
          <thead><tr><th scope="col">Run</th><th scope="col">Workflow</th><th scope="col">Version</th>
            <th scope="col">Status</th><th scope="col">Created</th></tr></thead>
          <tbody>{result.data.slice(0, pageSize).map(run => <tr key={run.run_id}>
            <td><a className="mono" href={projectPath(project.id) + "/runs/" + run.run_id}>
              {run.run_id.slice(0, 8)}
            </a></td>
            <td>{names.get(run.workflow_id) ?? run.workflow_id.slice(0, 8)}</td>
            <td>v{run.version}</td><td><Badge value={run.status} /></td><td>{date(run.created_at)}</td>
          </tr>)}</tbody>
        </table></div> : <p className="empty">No runs match this page.</p>}
        <Pager offset={offset} count={result.data.length} setOffset={setOffset} />
      </>}
    </section>
  </>;
}

type RunState = {
  run?: Run; tasks?: Task[]; attemptTask?: string; attempts?: Attempt[]; error?: string;
};

function useRunDetails(project: string, id: string, selected: string | undefined, refresh: number) {
  const [state, setState] = useState<RunState>({});
  useEffect(() => {
    const controller = new AbortController();
    const signal = controller.signal;
    let timer: number | undefined;
    let running = false;
    let polling = true;

    async function tick() {
      window.clearTimeout(timer);
      if (signal.aborted || document.hidden || running || !polling) return;
      running = true;
      try {
        const run = await api<Run>("/runs/" + id, { signal });
        if (run.project_id !== project) {
          throw new ApiError(404, "NOT_FOUND", "Run not found in this project.", null, null);
        }
        const tasks = await api<Task[]>("/runs/" + id + "/tasks?limit=100", { signal });
        const attemptTask = tasks.find(task => task.task_id === selected)?.task_id;
        const attempts = attemptTask ? await api<Attempt[]>(
          "/tasks/" + attemptTask + "/attempts", { signal },
        ) : undefined;
        if (signal.aborted) return;
        setState({ run, tasks, attemptTask, attempts });
        polling = active(run);
      } catch (error) {
        if (isAbort(error) || signal.aborted) return;
        const message = errorText(error);
        if (error instanceof ApiError && (error.status === 403 || error.status === 404)) {
          setState({ error: message });
          polling = false;
        } else {
          setState(previous => ({ ...previous, error: message }));
        }
      } finally {
        running = false;
        if (polling && !document.hidden && !signal.aborted) {
          timer = window.setTimeout(() => { void tick(); }, 3000);
        }
      }
    }

    const visible = () => {
      window.clearTimeout(timer);
      if (!document.hidden) void tick();
    };
    document.addEventListener("visibilitychange", visible);
    void tick();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [project, id, selected, refresh]);
  return state;
}

function revealAttempts(heading: HTMLHeadingElement | null) {
  heading?.focus({ preventScroll: true });
  heading?.scrollIntoView({ block: "center" });
}

export function RunDetails({ id, project, userId }: { id: string; project: Project; userId: string }) {
  const [selected, setSelected] = useState<string>();
  const attemptHeading = useRef<HTMLHeadingElement>(null);
  const [refresh, setRefresh] = useState(0);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const detail = useRunDetails(project.id, id, selected, refresh);
  const run = detail.run;
  const tasks = detail.tasks ?? [];
  const task = tasks.find(item => item.task_id === selected);
  const names = new Map(tasks.map(item => [item.task_id, item.node_key]));

  useEffect(() => {
    revealAttempts(attemptHeading.current);
  }, [selected]);

  if (!run) return <Notice error={Boolean(detail.error)}>{detail.error ?? "Loading run…"}</Notice>;
  const canCancel = active(run) &&
    (project.role === "OWNER" || (project.role === "DEVELOPER" && run.created_by === userId));

  async function cancel() {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api<Run>("/runs/" + id + "/cancel", json("POST"));
      setConfirm(false);
      setNotice("Run status: " + result.status + ".");
      setRefresh(value => value + 1);
    } catch (problem) {
      setError(errorText(problem));
    } finally {
      setBusy(false);
    }
  }

  return <>
    <PageTitle title={"Run " + run.run_id.slice(0, 8)} description={"Workflow version " + run.version}
      action={canCancel && <button className="secondary" onClick={() => setConfirm(true)}>Cancel run</button>} />
    <section className="panel">
      <div className="run-meta">
        <div><span className="field-label">Run status</span><span role="status"><Badge value={run.status} /></span></div>
        <div><span className="field-label">Created</span>{date(run.created_at)}</div>
        <div><span className="field-label">Started</span>{date(run.started_at)}</div>
        <div><span className="field-label">Finished</span>{date(run.finished_at)}</div>
      </div>
      {confirm && canCancel && <Confirm question="Cancel this run and its unfinished tasks?"
        label="Confirm cancellation" confirm={() => void cancel()} onClose={() => setConfirm(false)} busy={busy} />}
      <Notice error>{error}</Notice><Notice>{notice}</Notice>
      {detail.error && <Notice error>{detail.error + " Showing the last successful response."}</Notice>}
    </section>
    <section className="panel">
      <h2>Execution graph</h2>
      <RunGraph run={run} tasks={tasks} />
    </section>
    <section className="panel">
      <h2>Tasks</h2>
      <div className="table-scroll"><table>
        <thead><tr><th scope="col">Node</th><th scope="col">Status</th>
          <th scope="col">Prerequisites</th><th scope="col">Attempts</th></tr></thead>
        <tbody>{tasks.map(item => <tr key={item.task_id}>
          <td>{item.node_key}</td><td><Badge value={item.status} /></td>
          <td>{item.predecessor_task_ids.map(value => names.get(value) ?? value).join(", ") || "—"}</td>
          <td><button className="text-button" aria-label={"Attempts for " + item.node_key}
            aria-pressed={selected === item.task_id}
            aria-controls={selected === item.task_id ? "attempt-history" : undefined}
            onClick={() => {
              if (selected === item.task_id) revealAttempts(attemptHeading.current);
              else setSelected(item.task_id);
            }}>
            {item.attempt_count} {item.attempt_count === 1 ? "attempt" : "attempts"}
          </button></td>
        </tr>)}</tbody>
      </table></div>
    </section>
    {task && <section className="panel" id="attempt-history" aria-labelledby="attempt-history-heading">
      <h2 id="attempt-history-heading" ref={attemptHeading} tabIndex={-1}>Attempts · {task.node_key}</h2>
      {detail.attemptTask === selected && detail.attempts ?
        <Attempts attempts={detail.attempts} /> : <Notice>Loading attempts…</Notice>}
    </section>}
  </>;
}

function RunGraph({ run, tasks }: { run: Run; tasks: Task[] }) {
  const result = useLoad<Published>("/workflows/" + run.workflow_id + "/versions/" + run.version);
  if (!result?.data) return <Notice error={Boolean(result?.error)}>{result?.error ?? "Loading graph…"}</Notice>;
  if (result.data.id !== run.workflow_version_id) return <Notice error>Run version could not be verified.</Notice>;
  return <DagView dag={result.data.dag} states={new Map(tasks.map(task => [task.node_key, task.status]))} />;
}

function Attempts({ attempts }: { attempts: Attempt[] }) {
  if (!attempts.length) return <p className="empty">This task has no attempts yet.</p>;
  return <div className="table-scroll"><table>
    <thead><tr><th scope="col">Attempt</th><th scope="col">Status</th><th scope="col">Generation</th>
      <th scope="col">Worker</th><th scope="col">Error</th><th scope="col">Started</th>
      <th scope="col">Finished</th></tr></thead>
    <tbody>{attempts.map(attempt => <tr key={attempt.attempt_no}>
      <td>#{attempt.attempt_no}</td><td><Badge value={attempt.status} /></td>
      <td>{attempt.dispatch_generation}</td><td className="mono">{attempt.worker_id}</td>
      <td className="mono">{attempt.error_code ?? "—"}</td>
      <td>{date(attempt.started_at)}</td><td>{date(attempt.finished_at)}</td>
    </tr>)}</tbody>
  </table></div>;
}

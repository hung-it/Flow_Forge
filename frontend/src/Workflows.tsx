import { useState } from "react";
import type { FormEvent } from "react";
import { api, ApiError, errorText, json, jsonObject, uncertain } from "./api";
import { DagView } from "./DagView";
import { CreateName } from "./Projects";
import type { Draft, Project, Published, Version, Workflow } from "./types";
import { Badge, date, listPath, Notice, Pager, PageTitle, projectPath, useLoad } from "./ui";

export function Workflows({ project }: { project: Project }) {
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const path = "/projects/" + project.id + "/workflows";
  const href = projectPath(project.id) + "/workflows/";
  const result = useLoad<Workflow[]>(listPath(path, offset, { q: query }));

  return <>
    <PageTitle title="Workflows" description="Manage workflow drafts and immutable versions." />
    {project.role !== "VIEWER" && <section className="panel">
      <CreateName label="Workflow name" button="Create workflow" path={path}
        onCreated={id => { window.location.hash = href + id; }} />
    </section>}
    <section className="panel">
      <form className="form-row" onSubmit={event => {
        event.preventDefault();
        setQuery(search.trim());
        setOffset(0);
      }}>
        <label>Search workflows<input type="search" value={search} maxLength={100}
          onChange={event => setSearch(event.target.value)} /></label>
        <button className="secondary">Search</button>
      </form>
      <Notice error>{result?.error}</Notice>
      {!result && <Notice>Loading workflows…</Notice>}
      {result?.data && <>
        {result.data.length ? <div className="table-scroll"><table>
          <thead><tr><th scope="col">Name</th><th scope="col">Status</th><th scope="col">Latest version</th></tr></thead>
          <tbody>{result.data.map(workflow => <tr key={workflow.id}>
            <td><a href={href + workflow.id}>{workflow.name}</a></td>
            <td><Badge value={workflow.latest_version ? "Published" : "Draft"} /></td>
            <td>{workflow.latest_version ?? "—"}</td>
          </tr>)}</tbody>
        </table></div> : <p className="empty">No workflows match this page.</p>}
        <Pager offset={offset} count={result.data.length} setOffset={setOffset} />
      </>}
    </section>
  </>;
}

export function WorkflowDetails({ project, id }: { project: Project; id: string }) {
  const [history, setHistory] = useState(0);
  const [offset, setOffset] = useState(0);
  const [picked, setPicked] = useState<number>();
  const path = "/workflows/" + id;
  const original = useLoad<Workflow>(path);
  const draft = useLoad<Draft>(path + "/draft");
  const versions = useLoad<Version[]>(listPath(path + "/versions", offset), history);

  const workflow = original?.data;
  if (!workflow) return <Notice error={Boolean(original?.error)}>{original?.error ?? "Loading workflow…"}</Notice>;
  if (workflow.project_id !== project.id) return <Notice error>Workflow not found in this project.</Notice>;
  const selected = picked ?? workflow.latest_version;
  const canEdit = project.role !== "VIEWER";
  const projectHref = projectPath(project.id);

  return <>
    <PageTitle title={workflow.name} description="Workflow definition"
      action={<a className="button secondary" href={projectHref + "/runs?workflow=" + id}>View runs</a>} />
    <section className="panel">
      <h2>Draft</h2>
      <Notice error={Boolean(draft?.error)}>{draft?.error ?? (!draft ? "Loading draft…" : "")}</Notice>
      {draft?.data && <DraftEditor path={path} draft={draft.data} canEdit={canEdit}
        refreshHistory={version => {
          if (version !== undefined) setPicked(version);
          setOffset(0);
          setHistory(value => value + 1);
        }} />}
    </section>
    <section className="panel">
      <h2>Version history</h2>
      <Notice error={Boolean(versions?.error)}>{versions?.error ?? (!versions ? "Loading versions…" : "")}</Notice>
      {versions?.data && <>
        {versions.data.length ? <div className="table-scroll"><table>
          <thead><tr><th scope="col">Version</th><th scope="col">Draft revision</th>
            <th scope="col">Published</th><th scope="col">Action</th></tr></thead>
          <tbody>{versions.data.map(version => <tr key={version.version}>
            <td>v{version.version}</td><td>{version.source_draft_revision}</td>
            <td>{date(version.created_at)}</td>
            <td><button className="text-button" aria-pressed={selected === version.version}
              onClick={() => setPicked(version.version)}>View version {version.version}</button></td>
          </tr>)}</tbody>
        </table></div> : <p className="empty">No versions on this page.</p>}
        <Pager offset={offset} count={versions.data.length} setOffset={setOffset} />
      </>}
    </section>
    {selected !== null && <section className="panel">
      <PublishedPreview path={path} version={selected} />
      {canEdit && <RunForm path={path} version={selected} projectHref={projectHref} />}
    </section>}
  </>;
}

function PublishedPreview({ path, version }: { path: string; version: number }) {
  const result = useLoad<Published>(path + "/versions/" + version);
  return <>
    <h2>Published version {version}</h2>
    <Notice error={Boolean(result?.error)}>{result?.error ?? (!result ? "Loading snapshot…" : "")}</Notice>
    {result?.data && <DagView dag={result.data.dag} />}
  </>;
}

function DraftEditor({ path, draft, canEdit, refreshHistory }: {
  path: string; draft: Draft; canEdit: boolean; refreshHistory: (version?: number) => void;
}) {
  const [saved, setSaved] = useState(() => ({
    revision: draft.revision, text: JSON.stringify(draft.dag, null, 2),
  }));
  const [editor, setEditor] = useState(saved.text);
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [unknownPublish, setUnknownPublish] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const dirty = editor !== saved.text;

  async function change(action: "save" | "publish" | "reload" | "history") {
    if (busy) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      if (action === "save") {
        const next = await api<Draft>(path + "/draft", json("PUT", {
          expected_revision: saved.revision, dag: jsonObject(editor),
        }));
        setSaved({ revision: next.revision, text: editor });
        setMessage("Draft saved.");
      } else if (action === "publish") {
        const version = await api<Published>(path + "/versions", json("POST", {
          draft_revision: saved.revision,
        }));
        refreshHistory(version.version);
        setMessage("Published version " + version.version + ".");
      } else if (action === "reload") {
        const edited = await api<Draft>(path + "/draft");
        setSaved({ revision: edited.revision, text: JSON.stringify(edited.dag, null, 2) });
        setConflict(false);
        setMessage("Current revision loaded. Review the saved JSON before saving your edits.");
      } else {
        const latest = await api<Version[]>(path + "/versions?limit=1");
        refreshHistory(latest[0]?.version);
        setUnknownPublish(false);
        setMessage("Version history checked. Inspect it before publishing again.");
      }
    } catch (problem) {
      if (problem instanceof ApiError && problem.status === 409 && (action === "save" || action === "publish")) {
        setConflict(true);
        setError("The draft changed elsewhere. Load the current revision to continue.");
      } else if (action === "publish" && uncertain(problem)) {
        setUnknownPublish(true);
        setError("Publish outcome is unknown. Check version history before publishing again.");
      } else {
        setError(errorText(problem));
      }
    } finally {
      setBusy(false);
    }
  }
  return <>
    <p className="muted">Revision {saved.revision}{dirty ? " · Unsaved edits" : ""}</p>
    <label htmlFor="workflow-json">Workflow JSON</label>
    <textarea id="workflow-json" className="code" spellCheck={false} readOnly={!canEdit}
      disabled={busy} value={editor} onChange={event => setEditor(event.target.value)} rows={10} />
    {canEdit && <div className="actions">
      <button disabled={busy || conflict || !dirty}
        onClick={() => void change("save")}>Save draft</button>
      <button className="secondary" disabled={busy || conflict || dirty || unknownPublish}
        onClick={() => void change("publish")}>Publish version</button>
      {conflict && <button className="secondary" disabled={busy}
        onClick={() => void change("reload")}>Load current revision</button>}
      {unknownPublish && <button className="secondary" disabled={busy}
        onClick={() => void change("history")}>Check version history</button>}
    </div>}
    {canEdit && dirty && <details><summary>Compare with saved JSON</summary><pre>{saved.text}</pre>
      <button className="secondary" disabled={busy || conflict}
        onClick={() => setEditor(saved.text)}>Use saved JSON</button>
    </details>}
    <Notice error>{error}</Notice><Notice>{message}</Notice>
  </>;
}

type RunIntent = { key: string; body: string; version: number };

function RunForm({ path, version, projectHref }: {
  path: string; version: number; projectHref: string;
}) {
  const [input, setInput] = useState("{}");
  const [pending, setPending] = useState<RunIntent>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    let intent = pending;
    try {
      intent ??= {
        key: crypto.randomUUID(), version, body: JSON.stringify({ version, input: jsonObject(input) }),
      };
      const run = await api<{ run_id: string }>(path + "/runs", {
        method: "POST", body: intent.body, headers: { "Idempotency-Key": intent.key },
      });
      setPending(undefined);
      window.location.hash = projectHref + "/runs/" + run.run_id;
    } catch (problem) {
      if (intent && uncertain(problem)) {
        setPending(intent);
        setError("Run creation could not be confirmed. Retry the same request to recover its result.");
      } else {
        setError(errorText(problem));
      }
    } finally {
      setBusy(false);
    }
  }

  return <form className="run-form" onSubmit={submit}>
    <h3>Start run from version {pending?.version ?? version}</h3>
    <label htmlFor="run-input-json">Run input JSON</label>
    <textarea id="run-input-json" className="code" rows={4} spellCheck={false}
      value={input} disabled={busy} readOnly={Boolean(pending)}
      onChange={event => setInput(event.target.value)} />
    <div className="actions">
      <button disabled={busy}>{pending ? "Retry run request" : "Start run"}</button>
      {pending && <a href={projectHref + "/runs"}>Check run history</a>}
    </div>
    <Notice error>{error}</Notice>
  </form>;
}

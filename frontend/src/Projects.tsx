import { useState } from "react";
import type { FormEvent } from "react";
import { api, errorText, json } from "./api";
import type { Member, Project } from "./types";
import { Badge, Confirm, listPath, Notice, Pager, pageSize, PageTitle, projectPath, useLoad } from "./ui";

export function CreateName({ label, button, path, onCreated }: {
  label: string; button: string; path: string; onCreated: (id: string) => void;
}) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await api<{ id: string }>(path, json("POST", { name: name.trim() }));
      setName("");
      onCreated(result.id);
    } catch (problem) {
      setError(errorText(problem));
    } finally {
      setBusy(false);
    }
  }

  return <form className="name-form" onSubmit={submit}>
    <label>{label}<input maxLength={100} required value={name}
      onChange={event => setName(event.target.value)} /></label>
    <button disabled={busy || !name.trim()}>{button}</button>
    <Notice error>{error}</Notice>
  </form>;
}

export function Projects() {
  const [offset, setOffset] = useState(0);
  const result = useLoad<Project[]>(listPath("/projects", offset));
  return <>
    <PageTitle title="Projects" description="Choose a project to view its workflows and runs." />
    <div className="panel">
      <CreateName label="Project name" button="Create project" path="/projects"
        onCreated={id => { window.location.hash = projectPath(id); }} />
    </div>
    <Notice error>{result?.error}</Notice>
    {!result && <Notice>Loading projects…</Notice>}
    {result?.data && <>
      {result.data.length ? <ul className="project-grid">
        {result.data.slice(0, pageSize).map(project => <li key={project.id}>
          <a className="project-tile" href={projectPath(project.id)}>
            <span className="tile-icon" aria-hidden="true">{project.name.slice(0, 1).toUpperCase()}</span>
            <strong>{project.name}</strong><Badge value={project.role} />
          </a>
        </li>)}
      </ul> : <div className="panel empty">No projects on this page.</div>}
      <Pager offset={offset} count={result.data.length} setOffset={setOffset} />
    </>}
  </>;
}

export function Members({ project }: { project: Project }) {
  if (project.role !== "OWNER") {
    return <Notice error>Only the project owner can manage members.</Notice>;
  }
  return <MemberSettings project={project} />;
}

function MemberSettings({ project }: { project: Project }) {
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"DEVELOPER" | "VIEWER">("VIEWER");
  const [removing, setRemoving] = useState<Member>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const path = "/projects/" + project.id + "/members";
  const result = useLoad<Member[]>(listPath(path, offset), refresh);

  async function change(init: RequestInit, suffix: string, success: string) {
    if (busy) return false;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await api(path + suffix, init);
      setRefresh(value => value + 1);
      setMessage(success);
      return true;
    } catch (problem) {
      setError(errorText(problem));
      return false;
    } finally {
      setBusy(false);
    }
  }

  return <>
    <PageTitle title="Members" description="Manage access for existing FlowForge accounts." />
    <section className="panel">
      <h2>Add member</h2>
      <form className="form-row" onSubmit={async event => {
        event.preventDefault();
        if (await change(json("POST", { email: email.trim(), role }), "", "Member added.")) setEmail("");
      }}>
        <label>Email<input type="email" required maxLength={260} value={email}
          onChange={event => setEmail(event.target.value)} /></label>
        <label>New member role<select value={role}
          onChange={event => setRole(event.target.value as typeof role)}>
          <option value="VIEWER">Viewer</option><option value="DEVELOPER">Developer</option>
        </select></label>
        <button disabled={busy}>Add member</button>
      </form>
      <Notice error>{error}</Notice><Notice>{message}</Notice>
    </section>
    <section className="panel">
      <h2>Project access</h2>
      <Notice error>{result?.error}</Notice>
      {!result && <Notice>Loading members…</Notice>}
      {result?.data && <>
        <div className="table-scroll"><table>
          <thead><tr><th scope="col">Email</th><th scope="col">Role</th><th scope="col">Actions</th></tr></thead>
          <tbody>{result.data.slice(0, pageSize).map(member => <tr key={member.user_id}>
            <td>{member.email}</td>
            <td>{member.role === "OWNER" ? <Badge value={member.role} /> :
              <select aria-label={"Role for " + member.email} disabled={busy} value={member.role}
                onChange={event => void change(
                  json("PATCH", { role: event.target.value }), "/" + member.user_id, "Role updated.",
                )}>
                <option value="VIEWER">Viewer</option><option value="DEVELOPER">Developer</option>
              </select>}</td>
            <td>{member.role !== "OWNER" && <button className="text-button" disabled={busy}
              aria-label={"Remove " + member.email} onClick={() => setRemoving(member)}>Remove</button>}</td>
          </tr>)}</tbody>
        </table></div>
        {!result.data.length && <Notice>No members on this page.</Notice>}
        {removing && <Confirm question={"Remove " + removing.email + " from this project?"}
          label="Remove member" busy={busy} onClose={() => setRemoving(undefined)} confirm={async () => {
            if (await change(json("DELETE"), "/" + removing.user_id, "Member removed.")) {
              setRemoving(undefined);
              if (offset && result.data?.length === 1) setOffset(offset - pageSize);
            }
          }} />}
        <Pager offset={offset} count={result.data.length} setOffset={setOffset} />
      </>}
    </section>
  </>;
}

import { useState, useSyncExternalStore } from "react";
import type { FormEvent } from "react";
import { createRoot } from "react-dom/client";
import { errorText, restore, signIn, signOut, snapshot, subscribe } from "./api";
import type { User } from "./api";
import { Members, Projects } from "./Projects";
import { RunDetails, Runs } from "./Runs";
import type { Project } from "./types";
import { Badge, Notice, projectPath, useLoad } from "./ui";
import { WorkflowDetails, Workflows } from "./Workflows";
import "./style.css";

function Brand() {
  return <a className="brand" href="#/" aria-label="FlowForge projects">
    <span className="brand-symbol" aria-hidden="true"><i /><i /><i /><i /></span>
    FlowForge
  </a>;
}

function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await signIn(email.trim(), password);
    } catch (problem) {
      setError(errorText(problem));
    } finally {
      setBusy(false);
    }
  }

  return <main className="login">
    <div className="login-context">
      <Brand />
      <div>
        <h1>Workflow operations</h1>
        <p>Publish versioned workflows, start runs, and inspect every task attempt.</p>
        <div className="login-flow" aria-hidden="true">
          <span>Workflow</span><span>→</span><span>Run</span><span>→</span><span>Attempt</span>
        </div>
      </div>
    </div>
    <section className="login-form" aria-labelledby="sign-in-heading">
      <h2 id="sign-in-heading">Sign in</h2>
      <p>Use your FlowForge account to open your projects.</p>
      <form className="fields" onSubmit={submit}>
        <label>Email<input type="email" required autoComplete="username" maxLength={260}
          value={email} onChange={event => setEmail(event.target.value)} /></label>
        <label>Password<input type="password" required autoComplete="current-password" maxLength={128}
          value={password} onChange={event => setPassword(event.target.value)} /></label>
        <button disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        <Notice error>{error}</Notice>
      </form>
    </section>
  </main>;
}

function subscribeHash(listener: () => void) {
  window.addEventListener("hashchange", listener);
  return () => window.removeEventListener("hashchange", listener);
}

const readHash = () => window.location.hash.slice(1) || "/";
const uuid = /^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i;

function ProjectArea({ id, view, resource, workflow, user }: {
  id: string; view: string; resource?: string; workflow: string; user: User;
}) {
  const [refresh, setRefresh] = useState(0);
  const result = useLoad<Project>("/projects/" + id, refresh, true);
  const project = result?.data;
  if (!project) return <main id="dashboard-content" className="main-area alone">
    <Notice error={Boolean(result?.error)}>{result?.error ?? "Loading project…"}</Notice>
    {result?.error && <a href="#/">Back to projects</a>}
  </main>;

  const href = projectPath(id);
  return <div className="workspace">
    <aside className="sidebar">
      <a className="back" href="#/">← All projects</a>
      <div className="project-context"><span className="field-label">Project</span>
        <strong>{project.name}</strong><Badge value={project.role} /></div>
      <nav aria-label="Project navigation">
        <a href={href} aria-current={view === "workflows" ? "page" : undefined}>Workflows</a>
        <a href={href + "/runs"} aria-current={view === "runs" ? "page" : undefined}>Runs</a>
        {project.role === "OWNER" && <a href={href + "/members"}
          aria-current={view === "members" ? "page" : undefined}>Members</a>}
      </nav>
      <button className="text-button refresh-access" disabled={result?.refreshing}
        onClick={() => setRefresh(value => value + 1)}>Refresh access</button>
    </aside>
    <main id="dashboard-content" className="main-area">
      <p className="breadcrumb"><a href="#/">Projects</a><span>/</span>{project.name}</p>
      <Notice error>{result?.error}</Notice>
      {view === "members" ? <Members project={project} /> :
        view === "runs" ? (resource ? <RunDetails key={resource} id={resource} project={project} userId={user.id} />
          : <Runs key={workflow} project={project} initialWorkflow={workflow} />) :
        resource ? <WorkflowDetails key={resource} project={project} id={resource} userId={user.id} /> :
          <Workflows project={project} />}
    </main>
  </div>;
}

function Workspace({ user }: { user: User }) {
  const route = useSyncExternalStore(subscribeHash, readHash);
  const [path, query] = route.split("?", 2);
  const parts = path.split("/").filter(Boolean);
  const [, project, tab = "workflows", resource] = parts;
  const requestedWorkflow = new URLSearchParams(query).get("workflow") ?? "";
  const validProject = parts[0] === "projects" && !!project && uuid.test(project) &&
    ["workflows", "runs", "members"].includes(tab) && parts.length <= 4 &&
    (!resource || (tab !== "members" && uuid.test(resource)));
  return <>
    <a className="skip-link" href="#dashboard-content" onClick={event => {
      event.preventDefault();
      const main = document.getElementById("dashboard-content");
      if (main) {
        main.tabIndex = -1;
        main.focus();
      }
    }}>Skip to content</a>
    <header className="topbar"><Brand />
      <div className="account"><span>{user.email}</span>
        <button className="top-button" onClick={() => void signOut()}>Sign out</button>
      </div>
    </header>
    {!parts.length ? <main id="dashboard-content" className="main-area alone"><Projects /></main> :
      validProject ? <ProjectArea key={project} id={project} view={tab} resource={resource}
        workflow={uuid.test(requestedWorkflow) ? requestedWorkflow : ""} user={user} /> :
        <main id="dashboard-content" className="main-area alone">
          <Notice error>Page not found.</Notice><a href="#/">Back to projects</a>
        </main>}
  </>;
}

function App() {
  const session = useSyncExternalStore(subscribe, snapshot);
  if (session.phase === "ready") return <Workspace key={session.user.id} user={session.user} />;
  if (session.phase === "guest") return <Login />;
  return <main className="main-area alone">
    <h1>FlowForge</h1>
    {session.phase === "checking" ? <Notice>Checking your session…</Notice> :
      session.phase === "error" && <>
        <Notice error>{session.error}</Notice>
        <button onClick={() => void (session.retry === "logout" ? signOut() : restore())}>Try again</button>
      </>}
  </main>;
}

createRoot(document.getElementById("root")!).render(<App />);
void restore();

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { api, ApiError, errorText, isAbort } from "./api";

export const pageSize = 20;

type LoadResult<T> = {
  path: string; revision: number; data?: T; error?: string; refreshing?: boolean;
};

export function useLoad<T>(path: string, revision = 0, keepData = false): LoadResult<T> | undefined {
  const [result, setResult] = useState<LoadResult<T>>();
  useEffect(() => {
    const controller = new AbortController();
    api<T>(path, { signal: controller.signal }).then(
      data => { if (!controller.signal.aborted) setResult({ path, revision, data }); },
      error => {
        if (!isAbort(error) && !controller.signal.aborted) {
          const denied = error instanceof ApiError && [401, 403, 404].includes(error.status);
          setResult(previous => ({
            path, revision, error: errorText(error),
            data: keepData && !denied && previous?.path === path ? previous.data : undefined,
          }));
        }
      },
    );
    return () => controller.abort();
  }, [path, revision, keepData]);
  if (result?.path !== path) return;
  if (result.revision === revision) return result;
  if (keepData && result.data !== undefined) {
    return { path, revision, data: result.data, refreshing: true };
  }
}

export function PageTitle({ title, description, action }: {
  title: string; description?: string; action?: ReactNode;
}) {
  return <header className="page-title">
    <div><h1>{title}</h1>{description && <p>{description}</p>}</div>{action}
  </header>;
}

export function Notice({ children, error = false }: { children?: ReactNode; error?: boolean }) {
  if (!children) return null;
  return <p className={error ? "notice error" : "notice"} role={error ? "alert" : "status"}>{children}</p>;
}

export function Pager({ offset, count, setOffset }: {
  offset: number; count: number; setOffset: (offset: number) => void;
}) {
  return <nav className="pager" aria-label="Pagination">
    <button className="secondary" disabled={!offset} onClick={() => setOffset(offset - pageSize)}>Previous</button>
    <span>Page {offset / pageSize + 1}</span>
    <button className="secondary" disabled={count <= pageSize} onClick={() => setOffset(offset + pageSize)}>Next</button>
  </nav>;
}

export function Badge({ value }: { value: string }) {
  return <span className={"badge " + value.toLowerCase()}>{value.replaceAll("_", " ")}</span>;
}

export function Confirm({ question, confirm, onClose, busy, label }: {
  question: string; confirm: () => void; onClose: () => void; busy: boolean; label: string;
}) {
  return <div className="confirm" role="group" aria-label={question}>
    <p>{question}</p>
    <div className="actions">
      <button className="danger" disabled={busy} onClick={confirm}>{label}</button>
      <button className="secondary" disabled={busy} onClick={onClose}>Keep</button>
    </div>
  </div>;
}

export function date(value: string | null) {
  return value ? new Date(value).toLocaleString("en", { dateStyle: "medium", timeStyle: "medium" }) : "—";
}

export const projectPath = (id: string) => "#/projects/" + id;

export function listPath(path: string, offset: number, filters: Record<string, string> = {}) {
  const params = new URLSearchParams({ limit: String(pageSize + 1), offset: String(offset) });
  for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value);
  return path + "?" + params;
}

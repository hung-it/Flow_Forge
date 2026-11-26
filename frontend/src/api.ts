import { clearRunIntents } from "./runIntent";

type ApiErrorDetail = { loc: (string | number)[]; message: string };

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly requestId: string | null,
    public readonly retryAfter: string | null,
    public readonly details: ApiErrorDetail[] = [],
  ) {
    super(message);
  }
}

export type User = { id: string; email: string };
type TokenResponse = { access_token: string };
type Session =
  | { phase: "checking" | "guest" }
  | { phase: "ready"; user: User }
  | { phase: "error"; error: string; retry: "restore" | "logout" };

let session: Session = { phase: "checking" };
let token: string | null = null;
let epoch = 0;
let refresh: Promise<void> | undefined;
const subscribers = new Set<() => void>();
const channel = typeof BroadcastChannel === "function" ? new BroadcastChannel("flowforge-session") : null;

export function snapshot() {
  return session;
}

export function subscribe(listener: () => void) {
  subscribers.add(listener);
  return () => { subscribers.delete(listener); };
}

function publish(next: Session) {
  if (next.phase === "guest") clearRunIntents();
  if (next.phase === "ready") clearRunIntents(next.user.id);
  session = next;
  for (const listener of subscribers) listener();
}

function reset(next: Session) {
  epoch++;
  token = null;
  publish(next);
}

function checkEpoch(started: number, signal?: AbortSignal | null) {
  signal?.throwIfAborted();
  if (epoch !== started) throw new DOMException("Session changed", "AbortError");
}

export function isAbort(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}

export function errorText(error: unknown) {
  if (error instanceof ApiError) {
    const wait = error.status === 429 && error.retryAfter ? " Try again in " + error.retryAfter + " seconds." : "";
    const reference = error.requestId && error.status >= 500 ? " Request ID " + error.requestId + "." : "";
    return error.message + wait + reference;
  }
  return "Could not reach FlowForge. Try again.";
}

export function uncertain(error: unknown) {
  return !isAbort(error) && (!(error instanceof ApiError) || error.status >= 500);
}

async function send<T>(path: string, init: RequestInit = {}, access?: string | null): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body !== undefined && init.body !== null) headers.set("Content-Type", "application/json");
  if (access) headers.set("Authorization", "Bearer " + access);
  const timeout = AbortSignal.timeout(15000);
  const signal = init.signal ? AbortSignal.any([init.signal, timeout]) : timeout;
  const response = await fetch("/api/v1" + path, {
    ...init, headers, signal, credentials: "same-origin", cache: "no-store",
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as {
      code?: string; message?: string; details?: ApiErrorDetail[];
    } | null;
    throw new ApiError(response.status, payload?.code ?? "HTTP_ERROR",
      payload?.message ?? "Request failed.", response.headers.get("X-Request-ID"),
      response.headers.get("Retry-After"), Array.isArray(payload?.details) ? payload.details : []);
  }
  return response.status === 204 ? undefined as T : response.json() as Promise<T>;
}

function authPost<T>(action: string, body: object = {}) {
  return send<T>("/auth/" + action, {
    method: "POST", body: JSON.stringify(body), headers: { "X-FlowForge-CSRF": "1" },
  });
}

async function lock<T>(action: () => Promise<T>): Promise<T> {
  if (!navigator.locks) {
    throw new ApiError(0, "BROWSER_UNSUPPORTED",
      "Open FlowForge on localhost or HTTPS in a browser with Web Locks support.", null, null);
  }
  return await navigator.locks.request("flowforge-auth", action);
}

export function restore(): Promise<void> {
  if (refresh) return refresh;
  const started = epoch;
  const flight = lock(async () => {
    checkEpoch(started);
    const result = await authPost<TokenResponse>("refresh");
    const user = await send<User>("/auth/me", {}, result.access_token);
    checkEpoch(started);
    if (session.phase === "ready" && session.user.id !== user.id) {
      reset({ phase: "checking" });
    }
    token = result.access_token;
    publish({ phase: "ready", user });
  }).catch(error => {
    if (epoch !== started || isAbort(error)) return;
    if (error instanceof ApiError && error.status === 401) {
      reset({ phase: "guest" });
    } else {
      reset({ phase: "error", retry: "restore", error: errorText(error) });
    }
  }).finally(() => {
    if (refresh === flight) refresh = undefined;
  });
  refresh = flight;
  return flight;
}

export async function signIn(email: string, password: string) {
  const started = epoch;
  await lock(async () => {
    checkEpoch(started);
    const result = await authPost<TokenResponse>("login", { email, password });
    const user = await send<User>("/auth/me", {}, result.access_token);
    checkEpoch(started);
    token = result.access_token;
    publish({ phase: "ready", user });
    channel?.postMessage("changed");
  });
}

export async function signOut() {
  reset({ phase: "checking" });
  const started = epoch;
  try {
    await lock(async () => {
      checkEpoch(started);
      await authPost<void>("logout");
      checkEpoch(started);
      reset({ phase: "guest" });
      channel?.postMessage("ended");
    });
  } catch (error) {
    if (epoch === started && !isAbort(error)) {
      reset({ phase: "error", retry: "logout", error: "Sign out could not be confirmed. Try again." });
    }
  }
}

if (channel) {
  channel.onmessage = ({ data }) => {
    if (data !== "changed" && data !== "ended") return;
    refresh = undefined;
    reset({ phase: data === "ended" ? "guest" : "checking" });
    if (data === "changed") void restore();
  };
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const started = epoch;
  const original = token;
  try {
    const result = await send<T>(path, init, original);
    checkEpoch(started, init.signal);
    return result;
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 401) throw error;
    checkEpoch(started, init.signal);
    if (token === original) await restore();
    checkEpoch(started, init.signal);
    if (!token) throw error;
    try {
      const result = await send<T>(path, init, token);
      checkEpoch(started, init.signal);
      return result;
    } catch (retryError) {
      if (retryError instanceof ApiError && retryError.status === 401 && epoch === started) {
        reset({ phase: "guest" });
      }
      throw retryError;
    }
  }
}

export function json(method: string, body?: object): RequestInit {
  return { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) };
}

export function jsonObject(value: string): Record<string, unknown> {
  try {
    const parsed: unknown = JSON.parse(value);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    // Keep the original editor value available for correction.
  }
  throw new ApiError(422, "INVALID_JSON", "Enter a valid JSON object.", null, null);
}

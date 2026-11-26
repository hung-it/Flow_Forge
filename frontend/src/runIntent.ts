const prefix = "flowforge:pending-run:";

export type RunIntent = { key: string; body: string; version: number; input: string };

export function runIntentKey(userId: string, projectId: string, workflowId: string) {
  return prefix + [userId, projectId, workflowId].join(":");
}

export function readRunIntent(storageKey: string): RunIntent | undefined {
  try {
    const record = sessionStorage.getItem(storageKey);
    if (!record) return;
    const { key, body } = JSON.parse(record);
    if (typeof key !== "string" || typeof body !== "string") return;
    const { version, input } = JSON.parse(body);
    if (!Number.isInteger(version) || version < 1 || !input || typeof input !== "object"
        || Array.isArray(input)) return;
    return { key, body, version, input: JSON.stringify(input, null, 2) };
  } catch {
    // Storage may be unavailable, or the tab may contain an unreadable record.
    return undefined;
  }
}

export function saveRunIntent(storageKey: string, { key, body }: RunIntent) {
  try {
    sessionStorage.setItem(storageKey, JSON.stringify({ key, body }));
    return true;
  } catch {
    return false;
  }
}

export function forgetRunIntent(storageKey: string, key: string) {
  try {
    if (readRunIntent(storageKey)?.key === key) sessionStorage.removeItem(storageKey);
  } catch {
    // Keeping a replayable key is safe if storage cleanup fails.
  }
}

export function clearRunIntents(keepUserId?: string) {
  try {
    const keep = keepUserId ? prefix + keepUserId + ":" : undefined;
    for (let index = sessionStorage.length - 1; index >= 0; index--) {
      const key = sessionStorage.key(index);
      if (key?.startsWith(prefix) && (!keep || !key.startsWith(keep))) {
        sessionStorage.removeItem(key);
      }
    }
  } catch {
    // Browser storage failures must not prevent sign-in or sign-out.
  }
}

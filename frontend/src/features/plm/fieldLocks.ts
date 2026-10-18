/**
 * Field-level locks for SDS / TDS / COA / label documents (frontend-only).
 *
 * Documents are immutable versions — the backend has no lock endpoint — so
 * locks are review-time annotations kept in localStorage: a locked field is
 * marked verified/frozen, shown with a lock badge on the proofreader, and
 * surfaced in the generate-version modal so regeneration can preserve it.
 * Every lock / unlock appends an audit event (who / when / note).
 */
export interface FieldLock {
  docId: string;
  fieldKey: string;
  label: string;
  /** String snapshot of the locked value (for the regeneration notice). */
  value: string;
  lockedBy: string;
  lockedAt: string;
  note?: string;
}

export interface LockEvent {
  docId: string;
  fieldKey: string;
  action: "lock" | "unlock";
  by: string;
  at: string;
  note?: string;
}

const LOCKS_KEY = "formunex:doc-field-locks";
const HISTORY_KEY = "formunex:doc-field-lock-history";

export type LockStore = Record<string, FieldLock[]>;

export function loadLocks(): LockStore {
  try {
    const raw = JSON.parse(localStorage.getItem(LOCKS_KEY) ?? "{}") as unknown;
    if (raw && typeof raw === "object" && !Array.isArray(raw)) return raw as LockStore;
    return {};
  } catch {
    return {};
  }
}

export function loadHistory(): LockEvent[] {
  try {
    const raw = JSON.parse(localStorage.getItem(HISTORY_KEY) ?? "[]") as unknown;
    return Array.isArray(raw) ? (raw as LockEvent[]) : [];
  } catch {
    return [];
  }
}

function persist(store: LockStore, history: LockEvent[]): void {
  try {
    localStorage.setItem(LOCKS_KEY, JSON.stringify(store));
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
  } catch { /* storage full / private mode: locks stay session-only */ }
}

export function isLocked(store: LockStore, docId: string, fieldKey: string): boolean {
  return (store[docId] ?? []).some((l) => l.fieldKey === fieldKey);
}

export function locksFor(store: LockStore, docId: string): FieldLock[] {
  return store[docId] ?? [];
}

/** Lock a field; returns the new store + history (caller persists via persistLocks). */
export function lockField(store: LockStore, history: LockEvent[], docId: string,
  field: { fieldKey: string; label: string; value: string }, by: string, note?: string): { store: LockStore; history: LockEvent[] } {
  const at = new Date().toISOString();
  const entry: FieldLock = { docId, fieldKey: field.fieldKey, label: field.label, value: field.value, lockedBy: by, lockedAt: at, note: note?.trim() || undefined };
  const rest = (store[docId] ?? []).filter((l) => l.fieldKey !== field.fieldKey);
  const nextStore = { ...store, [docId]: [...rest, entry] };
  const nextHistory = [{ docId, fieldKey: field.fieldKey, action: "lock" as const, by, at, note: entry.note }, ...history];
  persist(nextStore, nextHistory);
  return { store: nextStore, history: nextHistory };
}

/** Unlock a field; returns the new store + history. */
export function unlockField(store: LockStore, history: LockEvent[], docId: string, fieldKey: string, by: string): { store: LockStore; history: LockEvent[] } {
  const at = new Date().toISOString();
  const nextStore = { ...store, [docId]: (store[docId] ?? []).filter((l) => l.fieldKey !== fieldKey) };
  const nextHistory = [{ docId, fieldKey, action: "unlock" as const, by, at }, ...history];
  persist(nextStore, nextHistory);
  return { store: nextStore, history: nextHistory };
}

/** Plain-text snapshot of a field value for the lock record. */
export function snapshotValue(v: unknown): string {
  if (v == null || v === "") return "—";
  if (Array.isArray(v)) return v.map((x) => (typeof x === "object" ? `${(x as { code?: string }).code ?? ""} ${(x as { text?: string }).text ?? ""}`.trim() : String(x))).join("；");
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

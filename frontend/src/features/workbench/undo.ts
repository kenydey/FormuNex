/** Bounded undo / redo history of unsaved ledger edits (pure; the page keeps it in state). */
export interface UndoHistory<T> { past: T[]; future: T[]; lastKey?: string; lastAt?: number }

export const UNDO_LIMIT = 50;
/** Consecutive edits of the same cell within this window are one undo step (typing "123" is one edit). */
export const COALESCE_MS = 1000;

export const emptyHistory = <T,>(): UndoHistory<T> => ({ past: [], future: [] });

/** Record `before` (the state just before an edit). `key` identifies the edited cell for coalescing. */
export function record<T>(h: UndoHistory<T>, before: T, key?: string, now = Date.now(), limit = UNDO_LIMIT): UndoHistory<T> {
  if (key && h.lastKey === key && h.lastAt != null && now - h.lastAt < COALESCE_MS && h.past.length) {
    return { ...h, future: [], lastAt: now };
  }
  return { past: [...h.past, before].slice(-limit), future: [], lastKey: key, lastAt: now };
}

/** Step back: returns the state to show and the new history, or null when there is nothing to undo. */
export function undo<T>(h: UndoHistory<T>, current: T): { state: T; history: UndoHistory<T> } | null {
  if (!h.past.length) return null;
  const state = h.past[h.past.length - 1];
  return { state, history: { past: h.past.slice(0, -1), future: [current, ...h.future] } };
}

export function redo<T>(h: UndoHistory<T>, current: T): { state: T; history: UndoHistory<T> } | null {
  if (!h.future.length) return null;
  const [state, ...rest] = h.future;
  return { state, history: { past: [...h.past, current], future: rest } };
}

/** Keyboard shortcut → action: Ctrl/⌘+Z undo, Ctrl/⌘+Shift+Z or Ctrl/⌘+Y redo. */
export function shortcut(e: Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "shiftKey" | "altKey">): "undo" | "redo" | null {
  if (!(e.ctrlKey || e.metaKey) || e.altKey) return null;
  const k = e.key.toLowerCase();
  if (k === "z") return e.shiftKey ? "redo" : "undo";
  if (k === "y" && !e.shiftKey) return "redo";
  return null;
}

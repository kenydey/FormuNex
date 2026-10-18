/**
 * OCSR correction ledger (frontend-only). The backend exposes no corrections
 * endpoint, so corrections live in localStorage: they are auto-applied when
 * the same OCSR output recurs, and can be exported as JSON for the OCSR
 * pipeline / a future server-side corrections endpoint.
 */
export interface OcsrCorrection {
  id: string;
  /** OCSR output as recognised (wrong). */
  original: string;
  /** Human-corrected SMILES. */
  corrected: string;
  note?: string;
  created_at: string;
}

const KEY = "formunex:ocsr-corrections";

export function loadCorrections(): OcsrCorrection[] {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "[]") as unknown;
    return Array.isArray(raw) ? raw.filter((x): x is OcsrCorrection =>
      typeof x?.original === "string" && typeof x?.corrected === "string") : [];
  } catch {
    return [];
  }
}

export function saveCorrections(list: OcsrCorrection[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(list));
  } catch { /* storage full / private mode: corrections stay session-only */ }
}

/** Upsert by original SMILES (same OCSR output corrected twice keeps the latest). Newest first. */
export function addCorrection(list: OcsrCorrection[], original: string, corrected: string, note?: string): OcsrCorrection[] {
  const o = original.trim();
  const c = corrected.trim();
  if (!o || !c || o === c) return list;
  const entry: OcsrCorrection = {
    id: Math.random().toString(36).slice(2), original: o, corrected: c,
    note: note?.trim() || undefined, created_at: new Date().toISOString(),
  };
  return [entry, ...list.filter((x) => x.original !== o)];
}

export function findCorrection(list: OcsrCorrection[], smiles: string): OcsrCorrection | undefined {
  const s = smiles.trim();
  return s ? list.find((x) => x.original === s) : undefined;
}

export function removeCorrection(list: OcsrCorrection[], id: string): OcsrCorrection[] {
  return list.filter((x) => x.id !== id);
}

export function exportCorrections(list: OcsrCorrection[]): string {
  return JSON.stringify(list, null, 2);
}

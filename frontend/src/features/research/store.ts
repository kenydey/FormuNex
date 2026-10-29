import { create } from "zustand";
import { DEFAULT_SOURCE_TYPES, evidenceKey, mergeEvidence } from "./lib";
import type { ChatTurn, ComprehensiveReport, Evidence, FilterReport, SourceType } from "./types";

/**
 * Research session (UI state, in memory): the gathered external sources, which are selected for grounding, the Q&A
 * transcript and the last deep-research report. Kept outside the page so switching tabs / pages does not lose it.
 */
interface ResearchState {
  query: string;
  sourceTypes: SourceType[];
  sources: Evidence[];
  selected: string[];
  usedSeedFallback: boolean;
  filterReport: FilterReport | null;
  turns: ChatTurn[];
  report: ComprehensiveReport | null;
  setQuery: (q: string) => void;
  setSourceTypes: (t: SourceType[]) => void;
  /** Merge rows (deduplicated) and select the new ones. */
  addSources: (rows: Evidence[]) => void;
  resetSources: () => void;
  removeSource: (key: string) => void;
  setSelected: (keys: string[]) => void;
  setSearchMeta: (m: { usedSeedFallback?: boolean; filterReport?: FilterReport | null }) => void;
  setTurns: (f: (turns: ChatTurn[]) => ChatTurn[]) => void;
  setReport: (r: ComprehensiveReport | null) => void;
}

export const useResearch = create<ResearchState>()((set) => ({
  query: "",
  sourceTypes: DEFAULT_SOURCE_TYPES,
  sources: [],
  selected: [],
  usedSeedFallback: false,
  filterReport: null,
  turns: [],
  report: null,
  setQuery: (query) => set({ query }),
  setSourceTypes: (sourceTypes) => set({ sourceTypes }),
  addSources: (rows) => set((s) => {
    const { merged, added } = mergeEvidence(s.sources, rows);
    return added.length ? { sources: merged, selected: [...s.selected, ...added.filter((k) => !s.selected.includes(k))] } : {};
  }),
  resetSources: () => set({ sources: [], selected: [], usedSeedFallback: false, filterReport: null }),
  removeSource: (key) => set((s) => ({ sources: s.sources.filter((e) => evidenceKey(e) !== key), selected: s.selected.filter((k) => k !== key) })),
  setSelected: (selected) => set({ selected }),
  setSearchMeta: (m) => set((s) => ({ usedSeedFallback: m.usedSeedFallback ?? s.usedSeedFallback, filterReport: m.filterReport === undefined ? s.filterReport : m.filterReport })),
  setTurns: (f) => set((s) => ({ turns: f(s.turns) })),
  setReport: (report) => set({ report }),
}));

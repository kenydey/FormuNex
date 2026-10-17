import { describe, expect, it } from "vitest";
import {
  buildManifestMarkdown, canIngestFulltext, chunk, draftConfirmPath, draftForEmbodiment, formatLocator, locatorBody, manifestCoverage,
  mergeEvidence, missingForProfile, parseSearchStreamData, probeScopeParams, propertyText, pushTrend, loadTrend, rankDelta,
  scopedManifestItems, searchableTypes, shelfFilename, sourceStatusColor, splitList, tableKind, trendDropped,
} from "./logic";
import type { EmbodimentDraft, Evidence, ManifestItem } from "./types";

const ev = (o: Partial<Evidence>): Evidence => ({ source: "patents", identifier: "X", title: "t", snippet: "", relevance: 1, ...o });

describe("lists", () => {
  it("splits on ascii and full-width separators", () => {
    expect(splitList("a, b，,c ")).toEqual(["a", "b", "c"]);
    expect(splitList("x; y；z", ";")).toEqual(["x", "y", "z"]);
  });
  it("chunks", () => expect(chunk([1, 2, 3, 4, 5], 2)).toEqual([[1, 2], [3, 4], [5]]));
});

describe("search", () => {
  it("maps availability to a badge status", () => {
    expect(sourceStatusColor(undefined)).toBe("default");
    expect(sourceStatusColor({ available: false })).toBe("error");
    expect(sourceStatusColor({ available: true, offline_fallback: true })).toBe("warning");
    expect(sourceStatusColor({ available: true })).toBe("success");
    expect(searchableTypes(["local", "patents"])).toEqual(["patents"]);
  });
  it("offers fulltext ingest for patents and OA / DOI literature only", () => {
    expect(canIngestFulltext(ev({ source: "surechembl" }))).toBe(true);
    expect(canIngestFulltext(ev({ source: "patents", is_seed_corpus: true }))).toBe(false);
    expect(canIngestFulltext(ev({ source: "literature", identifier: "10.1000/abc" }))).toBe(true);
    expect(canIngestFulltext(ev({ source: "literature", identifier: "foo" }))).toBe(false);
    expect(canIngestFulltext(ev({ source: "internet", identifier: "u" }))).toBe(false);
  });
  it("merges evidence without duplicates and parses stream frames", () => {
    const a = [ev({ identifier: "A" })];
    expect(mergeEvidence(a, [ev({ identifier: "A" }), ev({ identifier: "B" })]).map((e) => e.identifier)).toEqual(["A", "B"]);
    const p = parseSearchStreamData({ evidence: [ev({ is_seed_corpus: true })], sources_done: ["patents"] });
    expect(p.total).toBe(1);
    expect(p.usedSeedFallback).toBe(true);
    expect(p.sourcesDone).toEqual(["patents"]);
    expect(parseSearchStreamData(undefined).evidence).toEqual([]);
  });
});

describe("tables", () => {
  it("normalises kinds and formats properties", () => {
    expect(tableKind("recipe")).toBe("recipe");
    expect(tableKind("weird")).toBe("other");
    expect(propertyText({ name: "visc", name_normalized: "粘度", value: 1200, unit: "mPa·s" })).toBe("粘度: 1200 mPa·s");
    expect(propertyText({ name: "x", value: "", raw_text: "~5" })).toBe("x: ~5");
  });
});

describe("manifest", () => {
  const items: ManifestItem[] = [
    { id: "1", title: "A", doi: "10.1/a", screening: "match", snippet: "s", has_fulltext: true, locator: { page: 3, table: "2" } },
    { id: "2", title: "B|x", screening: "no_match" },
    { id: "3", enrich_status: "fetched" },
  ];
  it("counts coverage", () => {
    expect(manifestCoverage(items)).toEqual({ total: 3, doi: 1, snippet: 1, fulltext: 2, match: 1, noMatch: 1, pending: 1 });
  });
  it("scopes to the frozen subset", () => {
    expect(scopedManifestItems(items, null)).toHaveLength(3);
    expect(scopedManifestItems(items, { item_ids: ["2"] }).map((i) => i.id)).toEqual(["2"]);
    expect(scopedManifestItems(items, {})).toEqual([]);
  });
  it("formats locators and builds markdown", () => {
    expect(formatLocator(items[0])).toBe("p.3 / Tab.2");
    expect(formatLocator(items[1])).toBe("");
    expect(buildManifestMarkdown("citation", "H", items)).toContain("[1] A. DOI: 10.1/a (p.3 / Tab.2)");
    expect(buildManifestMarkdown("table", "H", items)).toContain("| 2 | B｜x |");
    expect(buildManifestMarkdown("list", "H", items)).toContain("## 3. 3");
  });
  it("validates locator pages", () => {
    expect(locatorBody({ page: "", figure: " ", table: "1" })).toEqual({ page: null, figure: null, table: "1" });
    expect(locatorBody({ page: 0 })).toBeNull();
    expect(locatorBody({ page: "2.5" })).toBeNull();
    expect(locatorBody({ page: 4 })?.page).toBe(4);
  });
  it("builds safe shelf names", () => {
    expect(shelfFilename("manifest_ab/c", "md", new Date("2026-01-02T03:04:05Z"))).toBe("manifest_ab_c_2026-01-02T03-04-05.md");
  });
});

describe("retrieval probe", () => {
  it("computes rank deltas and scope params", () => {
    expect(rankDelta({ rank: 1, title: "", snippet: "", rank_before_rerank: 3, rerank_score: 0.9 })).toBe("↑2");
    expect(rankDelta({ rank: 3, title: "", snippet: "", rank_before_rerank: 1, rerank_score: 0.9 })).toBe("↓2");
    expect(rankDelta({ rank: 1, title: "", snippet: "" })).toBe("—");
    expect(probeScopeParams(null, "project")).toEqual({ project_id: null, include_global: true });
    expect(probeScopeParams("p", "project")).toEqual({ project_id: "p", include_global: false });
    expect(probeScopeParams("p", "project_global")).toEqual({ project_id: "p", include_global: true });
  });
  it("keeps a bounded trend and detects drops", () => {
    const mem: Record<string, string> = {};
    const st = { getItem: (k: string) => mem[k] ?? null, setItem: (k: string, v: string) => { mem[k] = v; } };
    for (let i = 0; i < 14; i++) pushTrend({ at: String(i), mrr: 0.5, recall_at_k: 0.5, passed: 1, total: 2 }, st);
    expect(loadTrend(st)).toHaveLength(12);
    const t = pushTrend({ at: "x", mrr: 0.4, recall_at_k: 0.4, passed: 1, total: 2 }, st);
    expect(trendDropped(t)).toBe(true);
    expect(loadTrend({ getItem: () => "{bad" })).toEqual([]);
  });
});

describe("parse profiles and drafts", () => {
  it("lists missing requirements", () => {
    expect(missingForProfile("high", { gpu_available: false, mineru_key_present: true })).toEqual(["gpu"]);
    expect(missingForProfile("cloud", { gpu_available: false, mineru_key_present: true })).toEqual([]);
    expect(missingForProfile("mid", null)).toEqual(["mineru"]);
  });
  it("uses the picked embodiment as the formulation", () => {
    const d: EmbodimentDraft = {
      status: "draft", needs_review: true, origin: "patent_fulltext", doc_id: "D",
      formulation: { name: "f", domain: "x", ingredients: [] },
      embodiments: [{ label: "E1", ingredients: [{ name: "A", role: "", weight_pct: 60 }], amount_source: "table" }],
    };
    const r = draftForEmbodiment(d, 0);
    expect(r.formulation.ingredients).toEqual([{ name: "A", role: "additive", weight_pct: 60 }]);
    expect(r.amount_source).toBe("table");
    expect(draftForEmbodiment(d, 5)).toBe(d);
    expect(draftConfirmPath("surechembl")).toContain("/api/surechembl/");
    expect(draftConfirmPath("oa_pdf")).toContain("/api/formulations/");
  });
});

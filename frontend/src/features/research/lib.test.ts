import { describe, expect, it } from "vitest";
import {
  citationTarget, clusterEvidence, dedupKey, deepPercent, dispositionName, draftForEmbodiment, mergeEvidence, pageLabel, parseKbIngest, parseSearchData,
  sanitizeEvidence, shelfFilename, sortFindings, sourceBadge, stageIndex, toggleDiffPick, confirmPath, chatHistory, mapKbSourceIds, resolveItems, valueScore,
} from "./lib";
import type { EmbodimentDraft, Evidence, PreflightFinding } from "./types";

const ev = (id: string, extra: Partial<Evidence> = {}): Evidence => ({ source: "patents", identifier: id, title: `T ${id}`, snippet: "", relevance: 0.5, ...extra });

describe("research lib", () => {
  it("merges evidence without duplicates", () => {
    const { merged, added } = mergeEvidence([ev("a")], [ev("a"), ev("b"), { ...ev(""), title: "only-title" }]);
    expect(merged.map((e) => e.identifier || e.title)).toEqual(["a", "b", "only-title"]);
    expect(added).toEqual(["b", "only-title"]);
  });

  it("sanitizes evidence for the chat endpoint", () => {
    const s = sanitizeEvidence({ ...ev(""), title: "", source: "", relevance: 7 });
    expect(s).toMatchObject({ identifier: "source", title: "source", snippet: "source", source: "local", relevance: 1 });
    expect(sanitizeEvidence({ ...ev("x"), relevance: Number.NaN }).relevance).toBe(0.5);
  });

  it("parses search stream data", () => {
    const r = parseSearchData({ evidence: [ev("a", { is_seed_corpus: true })], sources_done: ["patents"], sources_pending: ["literature"],
      new_count: 1, kb_ingest_task_id: "k1", filter_report: { kept: 1, dropped: 2, dropped_by_reason: { junk: 2 }, dropped_examples: [] } });
    expect(r.usedSeedFallback).toBe(true);
    expect(r.kbIngestTaskId).toBe("k1");
    expect(r.progress).toMatchObject({ total: 1, newCount: 1, sourcesDone: ["patents"], sourcesPending: ["literature"] });
    expect(r.filterReport?.dropped).toBe(2);
    expect(parseSearchData(undefined).evidence).toEqual([]);
  });

  it("parses KB ingest progress", () => {
    expect(parseKbIngest({})).toBeNull();
    expect(parseKbIngest({ docs: [{ status: "indexed" }, { status: "failed" }, { status: "fetching" }] }))
      .toEqual(expect.objectContaining({ total: 3, indexed: 1, failed: 1, done: 0 }));
  });

  it("maps source availability to a badge", () => {
    expect(sourceBadge(undefined)).toBe("default");
    expect(sourceBadge({ available: false })).toBe("error");
    expect(sourceBadge({ available: true, offline_fallback: true })).toBe("warning");
    expect(sourceBadge({ available: true })).toBe("success");
  });

  it("tracks CRAG stages including regenerate", () => {
    expect(stageIndex("regenerate")).toBe(5);
    expect(stageIndex("")).toBe(0);
    expect(deepPercent("generate", undefined)).toBe(67);
    expect(deepPercent("retrieve", 0.42)).toBe(42);
    expect(deepPercent("retrieve", 55)).toBe(55);
  });

  it("formats page locators and citation targets", () => {
    expect(pageLabel({ page: 3, page_end: 5 })).toBe("pp. 3–5");
    expect(pageLabel({ page: 3, page_end: 3 })).toBe("pp. 3");
    expect(pageLabel({ page: null })).toBeNull();
    expect(citationTarget(ev("kb:abc#p2"))).toEqual({ internal: "/knowledge/sources/abc" });
    expect(citationTarget(ev("x", { url: "https://p.example/1", oa_pdf_url: "https://oa.example/1.pdf" }))).toEqual({ external: "https://oa.example/1.pdf" });
    expect(citationTarget(ev("x", { url: "javascript:alert(1)" }))).toBeNull();
  });

  it("keeps the last finished chat turns as history", () => {
    const h = chatHistory([{ role: "user", content: "q" }, { role: "assistant", content: "a" }, { role: "assistant", content: "", streaming: true }], 6);
    expect(h).toEqual([{ role: "user", content: "q", citations: undefined }, { role: "assistant", content: "a", citations: undefined }]);
  });

  it("sorts preflight findings open/blocking first", () => {
    const f = (id: string, severity: string, status = "open") => ({ id, severity, status, check: "", title: id, detail: "" }) as PreflightFinding;
    expect(sortFindings([f("a", "minor"), f("b", "blocking", "resolved"), f("c", "blocking"), f("d", "major")]).map((x) => x.id))
      .toEqual(["c", "d", "a", "b"]);
  });

  it("keeps two versions selected for a diff", () => {
    expect(toggleDiffPick([], "a")).toEqual(["a"]);
    expect(toggleDiffPick(["a", "b"], "c")).toEqual(["b", "c"]);
    expect(toggleDiffPick(["a", "b"], "a")).toEqual(["b"]);
  });

  it("names shelf files and parses Content-Disposition", () => {
    expect(shelfFilename("deep report/1", ".md", new Date("2026-10-16T08:30:00Z"))).toBe("deep_report_1_2026-10-16T08-30-00.md");
    expect(dispositionName('attachment; filename="doe_report_p1.docx"', "x")).toBe("doe_report_p1.docx");
    expect(dispositionName(null, "fallback.md")).toBe("fallback.md");
  });

  it("applies the selected embodiment to the draft", () => {
    const d: EmbodimentDraft = { status: "draft", needs_review: true, origin: "patent_fulltext", doc_id: "D", amount_source: "placeholder",
      formulation: { name: "F", domain: "degreaser", ingredients: [] },
      embodiments: [{ label: "Ex 1", amount_source: "table", ingredients: [{ name: "NaOH", role: "", weight_pct: 5 }] }] };
    const out = draftForEmbodiment(d, 0);
    expect(out.amount_source).toBe("table");
    expect(out.formulation.ingredients).toEqual([{ name: "NaOH", role: "additive", weight_pct: 5 }]);
    expect(draftForEmbodiment(d, 3)).toBe(d);
    expect(confirmPath("surechembl")).toContain("/surechembl/");
    expect(confirmPath("surechembl+fulltext")).toContain("/formulations/");
  });
});

describe("KB full-text mapping of search hits", () => {
  const ev = (identifier: string, extra: Partial<Evidence> = {}): Evidence => ({ source: "patents", identifier, title: identifier, snippet: "", relevance: 0.5, ...extra });
  it("maps hits via KB-build docs (patent aliases) and resolve matches, skipping failed docs", () => {
    const rows = [ev("CN-112345678-A"), ev("10.1000/xyz"), ev("web-1", { url: "https://e.org/a" }), ev("US1234567B2"), ev("kb:abc#c3")];
    const docs = [
      { identifier: "CN112345678A", status: "indexed", source_id: "s-pat" },
      { identifier: "US1234567B2", status: "failed", source_id: "s-bad" },
    ];
    const matches = [{ identifier: "10.1000/xyz", source_id: "s-doi" }, { identifier: "https://e.org/a", source_id: "s-web" }];
    expect(mapKbSourceIds(rows, docs, matches)).toEqual({
      "CN-112345678-A": "s-pat", "10.1000/xyz": "s-doi", "web-1": "s-web", "kb:abc#c3": "abc",
    });
  });

  it("asks the resolve endpoint only for unmapped, real, unique hits", () => {
    const rows = [ev("a", { url: "https://x" }), ev("a"), ev("b"), ev("kb:z#c0"), ev("seed", { is_seed_corpus: true })];
    expect(resolveItems(rows, { b: "s-b" })).toEqual([{ identifier: "a", url: "https://x", url_alt: null }]);
  });
});

describe("federated dedup clusters and value score", () => {
  const ev = (identifier: string, extra: Partial<Evidence> = {}): Evidence => ({ source: "patents", identifier, title: identifier, snippet: "", relevance: 0.5, ...extra });

  it("collapses identifier spellings into one cluster", () => {
    expect(dedupKey(ev("CN 112345678 A"))).toBe(dedupKey(ev("CN112345678A")));
    expect(dedupKey(ev("https://doi.org/10.1000/xyz"))).toBe(dedupKey(ev("10.1000/xyz")));
    expect(dedupKey(ev("", { title: "水性 环氧" }))).toBe(dedupKey(ev("", { title: "水性环氧" })));
    const clusters = clusterEvidence([
      ev("CN 112345678 A", { relevance: 0.6, source: "patents" }),
      ev("CN112345678A", { relevance: 0.9, source: "surechembl" }),
      ev("US999", { relevance: 0.7 }),
    ]);
    expect(clusters).toHaveLength(2);
    expect(clusters[0].items).toHaveLength(2);
    expect(clusters[0].representative.relevance).toBe(0.9);
    expect(clusters[0].representative.source).toBe("surechembl");
  });

  it("scores value from relevance, domain match, OA and KB presence", () => {
    expect(valueScore(ev("a", { relevance: 1, domain_match: "strong", is_oa: true }), true)).toBe(1);
    expect(valueScore(ev("a", { relevance: 0, domain_match: "none" }), false)).toBe(0);
    expect(valueScore(ev("a", { relevance: 0.8, domain_match: "weak" }), false)).toBe(0.5);
  });
});

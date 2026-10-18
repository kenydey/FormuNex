import { describe, expect, it } from "vitest";
import { metaList, parseWikiFrontMatter } from "./frontMatter";
import { dossierProjectId, themeKey, wikiHref, wikiPathFromHref } from "./paths";
import { wikiRows } from "./rows";
import { isWikiEvidence, relatedWikiFromCitations, wikiPathFromIdentifier, type WikiCitation } from "./wikiEvidence";
import { buildWikiLinkIndex, isKindKeyTarget, parseWikiHref, resolveWikiLink, rewriteWikiLinks } from "./wikilinks";

describe("parseWikiFrontMatter", () => {
  it("splits meta and body", () => {
    const md = `---
kind: material
title: "E-51"
source_ids: ["s1", "s2"]
flags: ["stale"]
---
# E-51

Body with $$E=mc^2$$.
`;
    const { meta, body } = parseWikiFrontMatter(md);
    expect(meta.kind).toBe("material");
    expect(meta.title).toBe("E-51");
    expect(meta.source_ids).toEqual(["s1", "s2"]);
    expect(metaList(meta, "flags")).toEqual(["stale"]);
    expect(metaList(meta, "kind")).toEqual(["material"]);
    expect(body).toContain("# E-51");
    expect(body).not.toContain("source_ids");
  });

  it("leaves markdown without front matter alone", () => {
    expect(parseWikiFrontMatter("# x").body).toBe("# x");
    expect(parseWikiFrontMatter("---\nunterminated").meta).toEqual({});
  });
});

describe("paths", () => {
  it("round-trips page paths through router hrefs", () => {
    expect(wikiHref("materials/e51.md")).toBe("/knowledge/wiki/materials/e51.md");
    const odd = "themes/环氧 (A).md";
    const href = wikiHref(odd);
    expect(href).not.toMatch(/[ ()]/);
    expect(wikiPathFromHref(href)).toBe(odd);
    expect(wikiPathFromHref("/knowledge/wiki")).toBeNull();
    expect(wikiPathFromHref("/knowledge/search")).toBeNull();
  });

  it("parses dossier paths and theme keys", () => {
    expect(dossierProjectId("themes/project-p1.md")).toBe("p1");
    expect(dossierProjectId("themes/other.md")).toBeNull();
    expect(themeKey({ norm_key: "epoxy", path: "systems/x.md" })).toBe("epoxy");
    expect(themeKey({ path: "systems/epoxy-amine.md" })).toBe("epoxy-amine");
  });
});

describe("wikilinks", () => {
  const index = buildWikiLinkIndex([
    { path: "materials/e51.md", title: "E-51", norm_key: "e51", kind: "material" },
    { path: "systems/epoxy.md", title: "环氧体系", kind: "system" },
  ]);

  it("resolves by title / path / norm_key", () => {
    expect(resolveWikiLink("E-51", index)?.path).toBe("materials/e51.md");
    expect(resolveWikiLink("materials/e51.md", index)?.path).toBe("materials/e51.md");
    expect(resolveWikiLink("materials/e51", index)?.path).toBe("materials/e51.md");
    expect(resolveWikiLink("e51", index)?.path).toBe("materials/e51.md");
    expect(resolveWikiLink("", index)).toBeNull();
  });

  it("resolves [[kind:key]] and rewrites live links to router hrefs, dead ones to anchors", () => {
    expect(resolveWikiLink("material:e51", index)?.path).toBe("materials/e51.md");
    expect(resolveWikiLink("system:epoxy", index)?.path).toBe("systems/epoxy.md");
    const out = rewriteWikiLinks("See [[material:e51|牌号]] and [[Missing (x)|缺页]] and [[system:epoxy|体系]].", index);
    expect(out).toContain("[牌号](/knowledge/wiki/materials/e51.md)");
    expect(out).toContain("[体系](/knowledge/wiki/systems/epoxy.md)");
    expect(out).toContain("[缺页](#wiki-dead:Missing%20%28x%29)");
    expect(parseWikiHref("/knowledge/wiki/materials/e51.md")).toEqual({ kind: "path", value: "materials/e51.md" });
    expect(parseWikiHref("#wiki-dead:Missing%20%28x%29")).toEqual({ kind: "dead", value: "Missing (x)" });
    expect(parseWikiHref("https://example.com")).toBeNull();
    expect(isKindKeyTarget("material:missing")).toBe(true);
    expect(isKindKeyTarget("Missing")).toBe(false);
  });
});

describe("wikiEvidence", () => {
  it("detects wiki citations and extracts paths", () => {
    const citations: WikiCitation[] = [
      { source: "wiki", identifier: "wiki:materials/e51.md", title: "[Wiki/material] E-51", snippet: "epoxy" },
      { source: "kb", identifier: "wiki:materials/e51.md", title: "dup" },
      { source: "patent", identifier: "US123", title: "Raw patent", snippet: "claim" },
    ];
    expect(isWikiEvidence(citations[0])).toBe(true);
    expect(isWikiEvidence(citations[2])).toBe(false);
    expect(wikiPathFromIdentifier("wiki:materials/e51.md")).toBe("materials/e51.md");
    const related = relatedWikiFromCitations(citations);
    expect(related).toEqual([{ path: "materials/e51.md", title: "E-51", kind: "material", snippet: "epoxy" }]);
    expect(relatedWikiFromCitations(null)).toEqual([]);
  });
});

describe("wikiRows", () => {
  const pages = [
    { id: "1", path: "materials/e51.md", kind: "material", title: "E-51", norm_key: "e51" },
    { id: "2", path: "queries/q.md", kind: "query", title: "草稿", flags: ["unreviewed"] },
  ];
  it("filters locally, hides unreviewed, and orders by search hits", () => {
    expect(wikiRows({ pages, flagsOnly: false, reviewedOnly: false, query: "e5" }).map((r) => r.id)).toEqual(["1"]);
    expect(wikiRows({ pages, flagsOnly: false, reviewedOnly: true, query: "" }).map((r) => r.id)).toEqual(["1"]);
    const hits = [
      { id: "3", path: "systems/x.md", kind: "system", title: "X", snippet: "…环氧…" },
      { id: "1", path: "materials/e51.md", kind: "material", title: "E-51", snippet: "hit" },
    ];
    const rows = wikiRows({ pages, hits, flagsOnly: false, reviewedOnly: false, query: "环氧" });
    expect(rows.map((r) => r.path)).toEqual(["systems/x.md", "materials/e51.md"]);
    expect(rows[1].snippet).toBe("hit");
  });
  it("uses flagged pages in flags mode", () => {
    const flagPages = [{ id: "9", path: "a.md", kind: "material", title: "A", flags: ["stale"], source_ids: [], actions: [{ id: "open_page", label: "打开" }] }];
    const rows = wikiRows({ pages, flagPages, hits: [], flagsOnly: true, reviewedOnly: false, query: "" });
    expect(rows).toHaveLength(1);
    expect(rows[0].actions?.[0].id).toBe("open_page");
  });
});

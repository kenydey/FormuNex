import { describe, expect, it } from "vitest";
import {
  acceptedTaskId, applyChatEvent, degradedReasons, evidenceToAnchors, historyForRequest, infraState, kbSourceId, maskCitationMarkers,
  parseFootnotes, PH_CLOSE, PH_OPEN, progressPct, restoreHistory, splitCitationMarkdown, trailingToken,
} from "./logic";
import type { ChatMessage, ChatStreamEvent } from "./types";

describe("citations", () => {
  it("splits body and footnotes", () => {
    expect(splitCitationMarkdown("plain")).toBeNull();
    expect(splitCitationMarkdown("A [^1].\n\n[^1]: src one")).toEqual({ answer: "A [^1].", footnotes: "[^1]: src one" });
  });

  it("masks markers outside code only", () => {
    const { text, ids } = maskCitationMarkers("x [^1] `a [^2]`\n```\n[^3]\n```\ny [^4]");
    expect(ids).toEqual(["1", "4"]);
    expect(text).toContain("`a [^2]`");
    expect(text).toContain("[^3]");
    expect(text).toContain(`${PH_OPEN}0${PH_CLOSE}`);
  });

  it("parses footnotes and ends one on a blank line", () => {
    const anchors = evidenceToAnchors([{ source: "kb", identifier: "kb:S1#c2", title: "Doc", snippet: "s", relevance: 1, page: 3 }]);
    const fns = parseFootnotes("[^1]: first\ncont\n\nbody\n[^2]: second", anchors);
    expect(fns.map((f) => [f.id, f.content])).toEqual([["1", "first\ncont"], ["2", "second"]]);
    expect(fns[0].anchor?.sourceId).toBe("S1");
    expect(fns[0].anchor?.page).toBe(3);
  });

  it("finds the KB source behind an evidence id", () => {
    expect(kbSourceId({ source: "patent", identifier: "kb:abc#c1" })).toBe("abc");
    expect(kbSourceId({ source: "kb_pageindex", identifier: "abc#0012" })).toBe("abc");
    expect(kbSourceId({ source: "uspto", identifier: "US123" })).toBeNull();
  });
});

describe("composer tokens", () => {
  it("detects a trailing /skill or @source token", () => {
    expect(trailingToken("hello /sum", "/")).toEqual({ active: true, query: "sum", before: "hello " });
    expect(trailingToken("a/b", "/").active).toBe(false);
    expect(trailingToken("see @专利", "@")).toEqual({ active: true, query: "专利", before: "see " });
  });
});

describe("stream reducer", () => {
  const start: ChatMessage = { id: "a", role: "assistant", content: "", streaming: true, steps: [] };
  const run = (evs: ChatStreamEvent[]) => evs.reduce(applyChatEvent, start);

  it("builds the thinking timeline and the final message", () => {
    const m = run([
      { type: "phase", phase: "retrieval" },
      { type: "meta", kb_used: 3, source_count: 5, rewritten_query: "q2" },
      { type: "tool_start", name: "pubchem", label: "PubChem" },
      { type: "tool_result", name: "pubchem", ok: false, summary: "timeout" },
      { type: "token", delta: "Hel" },
      { type: "token", delta: "lo" },
      { type: "done", answer: "Hello [^1]", citations: [], reviewer_fix: { run_id: "r1" }, notices: [{ code: "x", message: "m" }],
        mcp_permission_required: { server_id: "s", tool_name: "t" } },
    ]);
    expect(m.streaming).toBe(false);
    expect(m.content).toBe("Hello [^1]");
    expect(m.kbChunksUsed).toBe(3);
    expect(m.reviewRunId).toBe("r1");
    expect(m.mcpPermission?.tool_name).toBe("t");
    expect(m.steps?.map((s) => [s.title, s.status])).toEqual([["retrieval", "done"], ["PubChem", "error"], ["answering", "done"]]);
  });

  it("marks running steps as failed on error", () => {
    const m = run([{ type: "phase", phase: "retrieval" }, { type: "error", message: "检索失败" }]);
    expect(m.error).toBe("检索失败");
    expect(m.steps?.[0].status).toBe("error");
  });

  it("sends only finished turns as history", () => {
    const msgs: ChatMessage[] = [
      { id: "1", role: "user", content: "q" }, { id: "2", role: "assistant", content: "a", citations: [] },
      { id: "3", role: "assistant", content: "", streaming: true },
    ];
    expect(historyForRequest(msgs)).toEqual([{ role: "user", content: "q", citations: [] }, { role: "assistant", content: "a", citations: [] }]);
  });

  it("restores legacy session rows", () => {
    const r = restoreHistory([{ role: "user", content: "q" }, { role: "x", content: "?" }, null]);
    expect(r).toHaveLength(1);
    expect(r[0].id).toBeTruthy();
  });
});

describe("deep mode evidence", () => {
  it("classifies /health", () => {
    expect(infraState({ status: "ok" }).level).toBe("none");
    expect(infraState({ status: "ok", datalab: { required: false, reachable: false, ledger_mode: "local", hint: "h" } }))
      .toEqual({ level: "local", issues: [], hint: "h" });
    const d = infraState({ status: "degraded", database: { ok: false, scheme: "sqlite" }, task_broker: { required: true, reachable: false }, parsers: { pdf: false } });
    expect(d.issues.map((i) => i.code)).toEqual(["database", "broker", "pdf"]);
    expect(infraState({ status: "degraded" }).issues[0].code).toBe("generic");
  });

  it("lists offline reasons", () => {
    expect(degradedReasons({ key_set: false }, { literature: { available: false }, patents: { available: false } })).toEqual(["llm_key", "online_deps"]);
    expect(degradedReasons(undefined, { internet: { available: true } })).toEqual([]);
  });

  it("reads progress and accepted tasks", () => {
    expect(progressPct(0.42)).toBe(42);
    expect(progressPct(55)).toBe(55);
    expect(progressPct(null)).toBe(0);
    expect(acceptedTaskId({ task_id: "t1", stream_url: "/x" })).toBe("t1");
    expect(acceptedTaskId({ id: 1 })).toBeNull();
  });
});

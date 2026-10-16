import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { GateChecklist } from "./GateChecklist";
import type { Gate } from "./types";

vi.mock("@/app/session", () => ({ useSession: () => ({ can: (p: string) => p === "spec.write" }) }));
vi.mock("@/shared/SignatureModal", () => ({ useSignature: () => vi.fn() }));

const gate: Gate = {
  revision_id: "r", passed: false, evaluated_at: "", documents: [],
  checks: [
    { key: "formula_total", code: "G1", title: "配方合计 = 100%", status: "pass", waivable: false, items: [], notes: [], evidence: {} },
    { key: "objectives", code: "G3", title: "目标契约", status: "fail", waivable: true, notes: [], evidence: {},
      items: [{ key: "salt", message: "耐盐雾：无已复核的实测值", waivable: true, waived: false, waiver: null }] },
    { key: "red_lines", code: "G4", title: "合规红线", status: "fail", waivable: false, notes: [], evidence: {},
      items: [{ key: "x", message: "二甲苯超过红线", waivable: false, waived: false, waiver: null }] },
  ],
};

describe("GateChecklist", () => {
  it("expands failures and offers a waiver only where allowed", () => {
    render(<GateChecklist gate={gate} revisionId="r" onChange={() => undefined} />);
    expect(screen.getByText("耐盐雾：无已复核的实测值")).toBeInTheDocument();
    expect(screen.getByText("二甲苯超过红线")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^豁\s?免$/ })).toHaveLength(1); // red lines are never waivable
  });

  it("is read-only on a submitted package", () => {
    render(<GateChecklist gate={gate} revisionId="r" onChange={() => undefined} readOnly />);
    expect(screen.queryByRole("button", { name: /^豁\s?免$/ })).toBeNull();
  });
});

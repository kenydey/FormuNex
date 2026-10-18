import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import "@/app/i18n";

const authFetch = vi.fn();
vi.mock("@/api/auth", () => ({ authFetch: (...a: unknown[]) => authFetch(...a) }));
vi.mock("@/api/client", () => ({ call: vi.fn(), ApiError: class extends Error { constructor(public status: number, public detail: unknown) { super(String(detail)); } } }));

describe("RelationInsightsBanner", () => {
  it("renders components with substitutes and relation counts", async () => {
    const { RelationInsightsBanner } = await import("./RelationInsights");
    render(<RelationInsightsBanner insights={[
      { component: "KH-550", cas_no: "919-30-2", substitutes: ["KH-560", "KH-570"], relations: [{ type: "R", target: "x", confidence: 0.5 }] },
      { component: "Zinc phosphate", substitutes: [], relations: [] },
    ]} />);
    expect(screen.getByTestId("relation-insights-banner")).toBeInTheDocument();
    expect(screen.getByText("KH-550")).toBeInTheDocument();
    expect(screen.getByText("KH-560")).toBeInTheDocument();
    expect(screen.getByText("Zinc phosphate")).toBeInTheDocument();
  });

  it("renders nothing without insights", async () => {
    const { RelationInsightsBanner } = await import("./RelationInsights");
    const { container } = render(<RelationInsightsBanner insights={[]} />);
    expect(container.firstChild).toBeNull();
  });
});

describe("exportFormulationPdf", () => {
  it("posts the card and returns the PDF blob", async () => {
    const { exportFormulationPdf } = await import("./api");
    authFetch.mockResolvedValue(new Response(new Blob(["%PDF-1.4"], { type: "application/pdf" }), { status: 200 }));
    const form = { name: "F", domain: "degreaser", ingredients: [] } as never;
    const blob = await exportFormulationPdf(form, "en-US");
    expect(blob.size).toBeGreaterThan(0);
    const [path, init] = authFetch.mock.calls[0];
    expect(path).toBe("/api/formulations/export/pdf");
    expect(JSON.parse(init.body)).toEqual({ formulation: form, lang: "en-US" });
  });

  it("throws the server detail on error", async () => {
    const { exportFormulationPdf } = await import("./api");
    authFetch.mockResolvedValue(new Response(JSON.stringify({ detail: "bad" }), { status: 422 }));
    await expect(exportFormulationPdf({ name: "F" } as never, "zh-CN")).rejects.toThrow("bad");
  });
});

import { fireEvent, render, screen } from "@testing-library/react";
import { App as AntApp } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/app/i18n";

const call = vi.fn();
vi.mock("@/api/client", () => ({ call: (...a: unknown[]) => call(...a), download: vi.fn(), ApiError: class extends Error {} }));
let chartShouldThrow = false;
vi.mock("@/shared/Chart", () => ({ Chart: () => { if (chartShouldThrow) throw new Error("chart boom"); return <div data-testid="chart" />; } }));

function wrap(el: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><AntApp><MemoryRouter>{el}</MemoryRouter></AntApp></QueryClientProvider>);
}

vi.setConfig({ testTimeout: 60000 });

beforeEach(() => {
  call.mockReset();
  chartShouldThrow = false;
  call.mockImplementation(async (method: string, path: string) => {
    if (path === "/api/cockpit/color/materials") return { count: 0, materials: [] };
    if (method === "POST" && path === "/api/cockpit/color") return { formulations: [
      { name: "钛白", L: 95, a: -1, b: 2, delta_e_00: null, delta_e_ab: 5.1, delta_e: 5.1, delta_e_method: "cie76" },
      { name: "无色素", L: null, a: null, b: null, delta_e_00: null, delta_e_ab: null, delta_e: null, delta_e_method: null },
    ] };
    throw new Error(`unexpected ${method} ${path}`);
  });
});

describe("ColorPage P1-15", () => {
  it("无色素行显式标注而不白屏，有色行正常显示数值", async () => {
    const { default: Page } = await import("./ColorPage");
    wrap(<Page forms={[
      { name: "钛白", ingredients: [{ name: "Titanium dioxide", weight_pct: 20, role: "pigment" }] },
      { name: "无色素", ingredients: [{ name: "自由输入名", weight_pct: 100, role: "solvent" }] },
    ]} />);
    fireEvent.click(screen.getByRole("button", { name: "计算色度" }));
    expect(await screen.findByText("配方间 ΔE*ab 矩阵（CIE76）")).toBeInTheDocument();
    expect(screen.getAllByText("—（无色素）").length).toBeGreaterThan(0);
    expect(screen.getByText("95.00")).toBeInTheDocument();
  });

  it("结果区渲染异常时局部 ErrorBoundary 兜底，不整页白屏", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    chartShouldThrow = true;
    const { default: Page } = await import("./ColorPage");
    wrap(<Page forms={[{ name: "钛白", ingredients: [{ name: "Titanium dioxide", weight_pct: 20, role: "pigment" }] }]} />);
    fireEvent.click(screen.getByRole("button", { name: "计算色度" }));
    expect(await screen.findByText("界面发生错误")).toBeInTheDocument();
  });
});

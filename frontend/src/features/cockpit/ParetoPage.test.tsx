import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { App as AntApp } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/app/i18n";

const call = vi.fn();
vi.mock("@/api/client", () => ({ call: (...a: unknown[]) => call(...a), download: vi.fn(), ApiError: class extends Error {} }));
vi.mock("@/app/store", () => ({ useUi: (sel: (s: { projectId: string | null; theme: string }) => unknown) => sel({ projectId: "p1", theme: "light" }) }));
vi.mock("@/app/session", () => ({ useSession: () => ({ can: (p: string) => p === "formulation.freeze" }) }));
vi.mock("@/features/workbench/charts", () => ({ ParallelChart: () => <div data-testid="parallel" /> }));
// 散点 stub：每个点渲染一个按钮，点击即触发页面的 click 回调（与真实 echarts 事件同构）。
vi.mock("@/shared/Chart", () => ({ Chart: ({ onEvents, option }: { onEvents?: { click?: (p: unknown) => void }; option?: { series?: { data?: { name: string }[] }[] } }) => (
  <div data-testid="chart">{(option?.series ?? []).flatMap((s) => s.data ?? []).map((d) => (
    <button key={d.name} data-testid={`chart-point-${d.name}`} onClick={() => onEvents?.click?.({ name: d.name })}>{d.name}</button>
  ))}</div>
) }));

function wrap(el: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><AntApp><MemoryRouter>{el}</MemoryRouter></AntApp></QueryClientProvider>);
}

const tasks = [
  { task_no: "T1", experiment_id: 101, measured: { gloss: 80, hardness: 50 }, factors: { x: 1 } },
  { task_no: "T2", experiment_id: 102, measured: { gloss: 60, hardness: 40 }, factors: { x: 2 } },
];

vi.setConfig({ testTimeout: 60000 });

beforeEach(() => {
  call.mockReset();
  call.mockImplementation(async (method: string, path: string) => {
    if (path === "/api/projects/p1") return { workspace: { requirement: { objectives: [
      { metric: "gloss", direction: "match_target", target_value: 85 },
      { metric: "hardness", direction: "maximize", target_value: null },
    ] } } };
    if (path.startsWith("/api/lab/tasks")) return tasks;
    if (method === "POST" && path === "/api/cockpit/pareto") return { points: [
      { id: "T1", rank: 0, values: { gloss: 80, hardness: 50 } },
      { id: "T2", rank: 1, values: { gloss: 60, hardness: 40 } },
    ], frontier: ["T1"] };
    if (path === "/api/plm/products") return [];
    if (path.startsWith("/api/formulations/versions")) return [];
    throw new Error(`unexpected ${method} ${path}`);
  });
});

async function runFrontier() {
  const { default: Page } = await import("./ParetoPage");
  const utils = wrap(<Page />);
  // 等项目目标渲染成 Select 标签（而非仅请求发出），否则按钮仍是 disabled、点击无效果。
  await screen.findByText("gloss 目标值", { selector: ".ant-select-selection-item-content" }, { timeout: 10000 });
  fireEvent.click(screen.getByRole("button", { name: "计算前沿" }));
  await screen.findByTestId("chart-point-T1", {}, { timeout: 10000 });
  return utils;
}

describe("ParetoPage P1-16", () => {
  it("提供 match_target 选项，且 Select 改动后 target_value 不丢失", async () => {
    const { container } = await runFrontier();
    fireEvent.mouseDown(container.querySelector(".ant-select-selector")!);
    expect(await screen.findByText("gloss 目标值", { selector: ".ant-select-item-option-content" }, { timeout: 10000 })).toBeInTheDocument();
    fireEvent.click(await screen.findByText("hardness ↓", { selector: ".ant-select-item-option-content" }, { timeout: 10000 }));
    fireEvent.click(screen.getByRole("button", { name: "计算前沿" }));
    await waitFor(() => {
      const posts = call.mock.calls.filter((c) => c[0] === "POST" && c[1] === "/api/cockpit/pareto");
      const last = posts[posts.length - 1][2] as { objectives: { metric: string; direction: string; target_value?: number }[] };
      expect(last.objectives.find((o) => o.metric === "gloss")).toMatchObject({ direction: "match_target", target_value: 85 });
    }, { timeout: 10000 });
  });
});

describe("ParetoPage P1-17", () => {
  it("散点点击非前沿点只选中、不开定型弹窗；前沿点才开弹窗", async () => {
    await runFrontier();
    fireEvent.click(screen.getByTestId("chart-point-T2"));
    expect(await screen.findByText(/选中点 T2/)).toBeInTheDocument();
    expect(screen.queryByText("设为定型配方")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("chart-point-T1"));
    expect(await screen.findByText("设为定型配方")).toBeInTheDocument();
  });
});

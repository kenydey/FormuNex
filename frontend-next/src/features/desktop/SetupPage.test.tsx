import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { App } from "antd";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DesktopInfo } from "@/desktop/bridge";

const info: DesktopInfo = {
  config: { mode: null, remote_url: "", api_port: 8000, image: "img", version: "0.2.0", setup_complete: false, docker_path: null },
  api_base: null, data_dir: "/data", config_dir: "/cfg", app_version: "0.2.0", platform: "linux",
};
const desktop = {
  info: vi.fn(async () => ({ ...info, api_base: "https://fx.example.com", config: { ...info.config, mode: "remote", remote_url: "https://fx.example.com", setup_complete: true } })),
  saveConfig: vi.fn(async (c: unknown) => c),
  probeHealth: vi.fn(async () => ({ reachable: true, status: "ok", detail: {}, error: null })),
  dockerStatus: vi.fn(async () => ({ installed: false, path: null, client_version: null, server_version: null, compose_version: null, mem_total_bytes: null, cpus: null, message: "未找到 Docker" })),
};
vi.mock("@/desktop/bridge", () => ({ desktop, onStackLog: vi.fn(async () => () => undefined), isDesktop: () => true }));
const applied: DesktopInfo[] = [];
vi.mock("@/desktop/runtime", () => ({
  useDesktop: (sel: (s: { info: DesktopInfo }) => unknown) => sel({ info }),
  applyDesktopInfo: (i: DesktopInfo) => applied.push(i),
}));

const { default: SetupPage } = await import("./SetupPage");

function Where() {
  return <div data-testid="where">{useLocation().pathname}</div>;
}

function renderPage() {
  return render(
    <App>
      <MemoryRouter initialEntries={["/setup"]}>
        <Routes>
          <Route path="/setup" element={<SetupPage />} />
          <Route path="/login" element={<Where />} />
        </Routes>
      </MemoryRouter>
    </App>,
  );
}

beforeEach(() => vi.clearAllMocks());

describe("SetupPage", () => {
  it("connects to a server: probes /health, saves the remote mode, goes to sign-in", async () => {
    renderPage();
    fireEvent.click(screen.getByText("连接服务器"));
    fireEvent.change(screen.getByPlaceholderText("https://formunex.example.com"), { target: { value: "https://fx.example.com" } });
    fireEvent.click(screen.getByRole("button", { name: /测试并连接/ }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/login"));
    expect(desktop.probeHealth).toHaveBeenCalledWith("https://fx.example.com");
    expect(desktop.saveConfig).toHaveBeenCalledWith(expect.objectContaining({ mode: "remote", remote_url: "https://fx.example.com", setup_complete: true }));
    expect(applied.at(-1)?.api_base).toBe("https://fx.example.com");
  });

  it("does not save an unreachable server", async () => {
    desktop.probeHealth.mockResolvedValueOnce({ reachable: false, status: "unreachable", detail: null, error: "连接超时" } as never);
    renderPage();
    fireEvent.click(screen.getByText("连接服务器"));
    fireEvent.change(screen.getByPlaceholderText("https://formunex.example.com"), { target: { value: "https://down.example.com" } });
    fireEvent.click(screen.getByRole("button", { name: /测试并连接/ }));
    expect(await screen.findByText(/无法连接服务器/)).toBeInTheDocument();
    expect(desktop.saveConfig).not.toHaveBeenCalled();
  });

  it("local mode checks Docker and guides installation when it is missing", async () => {
    renderPage();
    fireEvent.click(screen.getByText("本机单机版"));
    expect(await screen.findByText("未检测到 Docker Desktop")).toBeInTheDocument();
    expect(desktop.saveConfig).toHaveBeenCalledWith(expect.objectContaining({ mode: "local" }));
    expect(screen.getByRole("button", { name: /下一步/ })).toBeDisabled();
  });
});

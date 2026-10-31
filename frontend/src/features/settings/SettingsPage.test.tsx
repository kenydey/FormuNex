import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "antd";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const call = vi.fn();
vi.mock("@/api/client", () => ({ call: (...a: unknown[]) => call(...a), ApiError: class extends Error {} }));
let perms: string[] = ["admin.llm"];
let me: unknown = { id: "u1", username: "u1", permissions: [], project_permissions: {} };
vi.mock("@/app/session", () => ({ useSession: () => ({ me, can: (p: string) => perms.includes(p) }) }));

import SettingsPage from "./SettingsPage";
import McpApprovalsPage from "./McpApprovalsPage";

const LLM = {
  provider: "deepseek", model: "deepseek-chat", key_set: true, base_url: null,
  providers: [{ id: "deepseek", label: "DeepSeek", base_url: "https://api.deepseek.com", models: [{ id: "deepseek-chat", label: "DeepSeek Chat" }] }],
  vision: { provider: "", model: "deepseek-chat", base_url: null, key_set: false, inherits: true, configured: false, hint: "视觉未配置" },
};

function renderAt(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <App>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/admin/settings/:tab" element={<SettingsPage />} />
            <Route path="/admin/mcp-approvals" element={<McpApprovalsPage />} />
          </Routes>
        </MemoryRouter>
      </App>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  perms = ["admin.llm"];
  me = { id: "u1", username: "u1", permissions: [], project_permissions: {} };
  call.mockReset();
  call.mockImplementation(async (_m: string, path: string) => {
    if (path === "/api/settings") return LLM;
    if (path === "/api/embedding-models") return [];
    if (path === "/api/embedding-models/switch/status") return { status: "idle", progress: 0 };
    if (path === "/api/settings/secrets") return { secrets: [
      { id: "tavily_api_key", env_key: "TAVILY_API_KEY", label: "Tavily", group: "search", set: true, masked: "tvly…9f2c" },
    ] };
    if (path === "/api/mcp/approvals/pending") return { items: [
      { request_id: 7, server_id: "fs", tool_name: "read_file", session_id: "s1", project_id: null, age_s: 12.3, arguments: { path: "/etc/x", api_key: "***" } },
    ], total: 1 };
    throw new Error(`unexpected ${path}`);
  });
});

describe("SettingsPage", () => {
  it("renders the model tab from /api/settings and never pre-fills the key", async () => {
    renderAt("/admin/settings/model");
    expect(await screen.findByText("文本模型 · Text")).toBeInTheDocument();
    expect(screen.getByText("视觉模型 · Vision")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("已配置 — 输入新密钥以覆盖")).toHaveValue("");
    expect(screen.getByText("视觉未配置")).toBeInTheDocument();
  });

  it("shows only the masked secret", async () => {
    renderAt("/admin/settings/keys");
    expect(await screen.findByText("tvly…9f2c")).toBeInTheDocument();
    expect(screen.getByText("TAVILY_API_KEY")).toBeInTheDocument();
  });

  it("is forbidden without admin.llm", () => {
    perms = [];
    renderAt("/admin/settings/model");
    expect(screen.getByText("403")).toBeInTheDocument();
    expect(call).not.toHaveBeenCalled();
  });

  it("opens for a connectors-only user (W1-6/D2 OR gate)", async () => {
    perms = ["admin.connectors"];
    renderAt("/admin/settings/model");
    expect(await screen.findByText("文本模型 · Text")).toBeInTheDocument();
    expect(screen.queryByText("403")).not.toBeInTheDocument();
  });
});

describe("argsPreview", () => {
  it("formats and truncates call arguments", async () => {
    const { argsPreview } = await import("./McpApprovalsPage");
    expect(argsPreview(null)).toBeNull();
    expect(argsPreview({})).toBe("{}");
    expect(argsPreview({ a: 1, b: { c: "d" } })).toBe('a=1, b={"c":"d"}');
    expect(argsPreview({ s: "x".repeat(100) }, 10)).toHaveLength(10);
  });
});

describe("McpApprovalsPage", () => {
  it("lists pending approvals and posts a decision", async () => {
    renderAt("/admin/mcp-approvals");
    expect(await screen.findByText("read_file")).toBeInTheDocument();
    expect(screen.getByText("path=/etc/x, api_key=***")).toBeInTheDocument();
    screen.getByRole("button", { name: /允许一次/ }).click();
    await waitFor(() => expect(call).toHaveBeenCalledWith("POST", "/api/mcp/approvals/7/decide", { decision: "allow", scope: "once" }));
  });

  it("shows Forbidden when logged out (W0: deciding is a personal action)", async () => {
    me = null;
    renderAt("/admin/mcp-approvals");
    expect(await screen.findByText("403")).toBeInTheDocument();
    expect(call.mock.calls.filter((c) => c[1] === "/api/mcp/approvals/pending")).toHaveLength(0);
  });
});

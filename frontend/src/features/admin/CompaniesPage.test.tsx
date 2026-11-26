import { render, screen } from "@testing-library/react";
import { App as AntApp } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/app/i18n";

const call = vi.fn();
vi.mock("@/api/client", () => ({ call: (...a: unknown[]) => call(...a), download: vi.fn(), ApiError: class extends Error {} }));
let perms: string[] = ["admin.companies"];
vi.mock("@/app/session", () => ({ useSession: () => ({ can: (p: string) => perms.includes(p) }) }));

import CompaniesPage from "./CompaniesPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AntApp>
        <CompaniesPage />
      </AntApp>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  perms = ["admin.companies"];
  call.mockReset();
  call.mockImplementation(async (_m: string, path: string) => {
    if (path === "/api/plm/companies") return [];
    if (path === "/api/plm/regulations") return [];
    return [];
  });
});

describe("CompaniesPage Excel import gate (X-P3-2)", () => {
  it("disables Excel import for an admin.companies-only user (sys_admin has no plm.document.write)", async () => {
    renderPage();
    const btn = await screen.findByRole("button", { name: "Excel 导入" });
    expect(btn).toBeDisabled();
  });

  it("enables Excel import with plm.document.write", async () => {
    perms = ["admin.companies", "plm.document.write"];
    renderPage();
    const btn = await screen.findByRole("button", { name: "Excel 导入" });
    expect(btn).toBeEnabled();
  });
});

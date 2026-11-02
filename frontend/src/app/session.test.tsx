import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "@/api/client";
import { SessionProvider, useSession } from "./session";
import { useUi } from "./store";

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, call: vi.fn() };
});
vi.mock("@/api/auth", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/auth")>();
  return {
    ...actual,
    getAccessToken: () => "tok",
    refreshAccessToken: vi.fn().mockResolvedValue(undefined),
    setAccessToken: vi.fn(),
    rememberRefreshToken: vi.fn().mockResolvedValue(undefined),
    logoutBody: vi.fn().mockResolvedValue({}),
  };
});

import { call } from "@/api/client";

const mockedCall = vi.mocked(call);

function Probe({ onSession }: { onSession?: (s: ReturnType<typeof useSession>) => void }) {
  const s = useSession();
  onSession?.(s);
  return (
    <div>
      <span data-testid="ready">{s.ready ? "yes" : "no"}</span>
      <span data-testid="me">{s.me ? s.me.id : "null"}</span>
      <span data-testid="error">{s.error ? "yes" : "no"}</span>
    </div>
  );
}

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><SessionProvider>{ui}</SessionProvider></QueryClientProvider>);
}

describe("session (SR-075)", () => {
  it("a non-401 /me failure becomes error state, not an unhandled rejection or a login bounce", async () => {
    mockedCall.mockRejectedValueOnce(new ApiError(500, "boom"));
    wrap(<Probe />);
    await waitFor(() => expect(screen.getByTestId("ready")).toHaveTextContent("yes"));
    expect(screen.getByTestId("me")).toHaveTextContent("null");
    expect(screen.getByTestId("error")).toHaveTextContent("yes");
  });

  it("logout clears the persisted project selection", async () => {
    mockedCall.mockResolvedValue({});
    useUi.setState({ projectId: "p1" });
    let captured: ReturnType<typeof useSession> | null = null;
    wrap(<Probe onSession={(s) => { captured = s; }} />);
    await waitFor(() => expect(screen.getByTestId("ready")).toHaveTextContent("yes"));
    await captured!.logout();
    expect(useUi.getState().projectId).toBeNull();
  });
});

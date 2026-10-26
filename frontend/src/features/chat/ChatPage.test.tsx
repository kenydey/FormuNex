import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "antd";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const call = vi.fn();
const postSse = vi.fn();
vi.mock("@/api/client", () => ({ call: (...a: unknown[]) => call(...a), ApiError: class extends Error {} }));
vi.mock("@/app/session", () => ({ useSession: () => ({ can: () => true }) }));
vi.mock("@/api/stream", () => ({ postSse: (...a: unknown[]) => postSse(...a) }));
vi.mock("@/shared/Markdown", () => ({ Markdown: ({ content }: { content: string }) => <div>{content}</div> }));

import ChatPage from "./ChatPage";
import { ErrorBoundary } from "./ErrorBoundary";

function wrap(ui: React.ReactNode, path = "/knowledge/chat") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <App>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/knowledge/chat" element={ui} />
            <Route path="/knowledge/chat/:sessionId" element={ui} />
          </Routes>
        </MemoryRouter>
      </App>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  call.mockReset();
  postSse.mockReset();
  call.mockImplementation(async (method: string, path: string) => {
    if (path.startsWith("/api/session/list")) return { sessions: [{ session_id: "chat-old", history_count: 2, has_context: false, title: "旧会话" }] };
    if (path === "/api/skills") return { skills: [{ id: "summarize", kind: "chat_skill", title: "总结", summary: "", icon: "", enabled: true }] };
    if (path === "/api/kb/sources") return { sources: [] };
    if (path === "/api/session/save") return { ok: "1" };
    if (path.startsWith("/api/settings/wiki-chat-mode") || path.startsWith("/api/research/rag")) throw new Error("n/a");
    throw new Error(`unexpected ${method} ${path}`);
  });
});

describe("ChatPage", () => {
  it("streams an answer with citations and saves the session", async () => {
    postSse.mockImplementation(async (_p: string, body: { question: string; chat_session_id: string }, onEvent: (e: unknown) => void) => {
      expect(body.question).toBe("防腐机理？");
      expect(body.chat_session_id).toMatch(/^chat-/);
      onEvent({ type: "phase", phase: "retrieval" });
      onEvent({ type: "token", delta: "磷酸锌" });
      onEvent({ type: "done", answer: "磷酸锌钝化 [^1]", kb_chunks_used: 2,
        citations: [{ source: "kb", identifier: "kb:S1#c0", title: "专利 A", snippet: "片段", relevance: 0.9, page: 5 }] });
    });
    wrap(<ChatPage />);
    expect(await screen.findByText("旧会话")).toBeInTheDocument();
    fireEvent.change(screen.getByTestId("chat-draft-input"), { target: { value: "防腐机理？" } });
    fireEvent.click(screen.getByTestId("chat-send"));
    expect(await screen.findByTestId("citation-chips")).toBeInTheDocument();
    expect(screen.getByText(/知识库 ×/)).toBeInTheDocument();
    await waitFor(() => expect(call).toHaveBeenCalledWith("POST", "/api/session/save", expect.objectContaining({ title: "防腐机理？" })));
  });

  it("picks a skill from the / menu instead of sending", async () => {
    wrap(<ChatPage />);
    const input = await screen.findByTestId("chat-draft-input");
    fireEvent.change(input, { target: { value: "/sum" } });
    expect(await screen.findByText("/summarize")).toBeInTheDocument();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(postSse).not.toHaveBeenCalled();
    expect(screen.getByTestId("composer-selection-chips")).toHaveTextContent("/summarize");
  });
});

describe("ErrorBoundary", () => {
  it("shows the fallback with the error message", () => {
    const Boom = () => {
      throw new Error("kaboom");
    };
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(<ErrorBoundary><Boom /></ErrorBoundary>);
    expect(screen.getByText("kaboom")).toBeInTheDocument();
    expect(screen.getByText("界面发生错误")).toBeInTheDocument();
  });
});

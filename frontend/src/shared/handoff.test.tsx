import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { HandoffBanner, handoffQuestion, ingredientInQuestion } from "./handoff";

describe("handoff from chat playbooks", () => {
  it("reads the question from ?q= first, then the navigation state", () => {
    expect(handoffQuestion(new URLSearchParams("q=+耐盐雾+"), { question: "旧" })).toBe("耐盐雾");
    expect(handoffQuestion(new URLSearchParams("tab=classic"), { question: " 降低 VOC " })).toBe("降低 VOC");
    expect(handoffQuestion(new URLSearchParams(""), { question: 42 })).toBe("");
    expect(handoffQuestion(new URLSearchParams(""), null)).toBe("");
  });

  it("picks the longest ingredient the question names, never a guess", () => {
    const names = ["环氧树脂", "环氧树脂E-51", "磷酸锌", "水"];
    expect(ingredientInQuestion("把环氧树脂e-51换成生物基的", names)).toBe("环氧树脂E-51");
    expect(ingredientInQuestion("磷酸锌供应紧张，有什么替代？", names)).toBe("磷酸锌");
    expect(ingredientInQuestion("提高耐盐雾性能", names)).toBe("");
    expect(ingredientInQuestion("加点水", names)).toBe(""); // one-character names match too easily
  });

  it("shows the handed-over question and can be dismissed", () => {
    render(
      <MemoryRouter initialEntries={[{ pathname: "/lab/inverse-design", state: { question: "盐雾 1000 h 的水性环氧底漆" } }]}>
        <HandoffBanner />
      </MemoryRouter>,
    );
    expect(screen.getByText("来自问答的问题")).toBeInTheDocument();
    expect(screen.getByText("盐雾 1000 h 的水性环氧底漆")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(screen.queryByTestId("handoff-banner")).toBeNull();
  });

  it("renders nothing without a question", () => {
    const { container } = render(<MemoryRouter initialEntries={["/lab/doe?tab=classic"]}><HandoffBanner /></MemoryRouter>);
    expect(container).toBeEmptyDOMElement();
  });
});

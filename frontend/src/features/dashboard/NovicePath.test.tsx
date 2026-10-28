import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/app/session", () => ({ useSession: () => ({ can: () => true }) }));

import { NovicePath } from "./NovicePath";

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="probe">{loc.pathname}|{JSON.stringify(loc.state)}</div>;
}

function wrap() {
  render(
    <MemoryRouter initialEntries={["/dashboard"]}>
      <Routes>
        <Route path="/dashboard" element={<NovicePath />} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("NovicePath", () => {
  it("renders the 8 groups in the confirmed order", () => {
    wrap();
    const steps = document.querySelectorAll("[data-testid^='novice-step-']");
    expect(steps).toHaveLength(8);
    expect(Array.from(steps).map((el) => el.getAttribute("data-testid"))).toEqual([
      "novice-step-nav.dashboard",
      "novice-step-nav.data",
      "novice-step-nav.spec",
      "novice-step-nav.lab",
      "novice-step-nav.cockpit",
      "novice-step-nav.product",
      "novice-step-nav.materials",
      "novice-step-nav.admin",
    ]);
  });

  it("a non-dashboard step navigates to the group's first leaf with the group-home state", () => {
    wrap();
    fireEvent.click(screen.getByTestId("novice-step-nav.data"));
    expect(screen.getByTestId("probe").textContent).toBe("/knowledge/search|{\"groupHome\":true}");
  });
});

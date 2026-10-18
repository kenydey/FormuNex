import "@testing-library/jest-dom/vitest";

// antd reads these in jsdom.
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false, media: query, onchange: null, addListener: () => {}, removeListener: () => {},
    addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
  }),
});
class RO { observe() {} unobserve() {} disconnect() {} }
(window as unknown as { ResizeObserver: unknown }).ResizeObserver = RO;
window.getComputedStyle = ((orig) => (elt: Element) => orig(elt))(window.getComputedStyle);

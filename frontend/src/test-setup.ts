import "@testing-library/jest-dom/vitest";

// 以下 stub 只在 jsdom 环境下生效：跑在 node 环境的测试文件
//（如 auth.desktop.test.ts，因 jsdom/undici AbortSignal 跨 realm 问题
// 必须用 node 环境）没有 window，跳过即可。
if (typeof window !== "undefined") {
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
}

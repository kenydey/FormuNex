import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.restoreAllMocks();
  vi.resetModules();
  delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
});

describe("saveBlob", () => {
  it("downloads through an anchor in the browser", async () => {
    const { saveBlob } = await import("./saveFile");
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    URL.createObjectURL = vi.fn(() => "blob:x");
    URL.revokeObjectURL = vi.fn();
    expect(await saveBlob(new Blob(["a"]), "a.csv")).toBe(true);
    expect(click).toHaveBeenCalledOnce();
  });

  it("uses the native save dialog and writes the bytes on desktop", async () => {
    (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
    const save = vi.fn(async () => "/tmp/a.csv");
    const writeFile = vi.fn(async () => undefined);
    vi.doMock("@tauri-apps/plugin-dialog", () => ({ save }));
    vi.doMock("@tauri-apps/plugin-fs", () => ({ writeFile }));
    const { saveBlob } = await import("./saveFile");
    // jsdom's Blob has no arrayBuffer(); real WebViews do
    const blob = Object.assign(new Blob(["hi"]), { arrayBuffer: async () => new TextEncoder().encode("hi").buffer });
    expect(await saveBlob(blob, "a.csv")).toBe(true);
    expect(save).toHaveBeenCalledWith({ defaultPath: "a.csv" });
    expect(writeFile).toHaveBeenCalledWith("/tmp/a.csv", new Uint8Array([104, 105]));
    save.mockResolvedValueOnce(null as unknown as string);
    expect(await saveBlob(new Blob(["x"]), "b.csv")).toBe(false);
  });
});

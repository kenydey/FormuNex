import { describe, expect, it } from "vitest";
import { emptyHistory, record, redo, shortcut, undo, UNDO_LIMIT } from "./undo";

describe("ledger undo history", () => {
  it("undoes and redoes in order, and a new edit clears redo", () => {
    let h = emptyHistory<number>();
    h = record(h, 0, "a", 0);
    h = record(h, 1, "b", 10);
    const u1 = undo(h, 2)!;
    expect(u1.state).toBe(1);
    const u2 = undo(u1.history, u1.state)!;
    expect(u2.state).toBe(0);
    expect(undo(u2.history, 0)).toBeNull();
    const r1 = redo(u2.history, 0)!;
    expect(r1.state).toBe(1);
    const after = record(r1.history, 1, "c", 20);
    expect(after.future).toEqual([]);
    expect(redo(after, 5)).toBeNull();
  });

  it("coalesces rapid edits of the same cell and bounds the history", () => {
    let h = emptyHistory<number>();
    h = record(h, 0, "a", 0);
    h = record(h, 1, "a", 300);
    h = record(h, 2, "a", 600);
    expect(h.past).toEqual([0]);
    h = record(h, 3, "a", 5000);
    expect(h.past).toEqual([0, 3]);
    let big = emptyHistory<number>();
    for (let i = 0; i < UNDO_LIMIT + 10; i++) big = record(big, i, `k${i}`, i);
    expect(big.past.length).toBe(UNDO_LIMIT);
    expect(big.past[0]).toBe(10);
  });

  it("maps keyboard shortcuts", () => {
    const k = (key: string, o: Partial<KeyboardEvent> = {}) => ({ key, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...o });
    expect(shortcut(k("z", { ctrlKey: true }))).toBe("undo");
    expect(shortcut(k("Z", { ctrlKey: true, shiftKey: true }))).toBe("redo");
    expect(shortcut(k("y", { metaKey: true }))).toBe("redo");
    expect(shortcut(k("z"))).toBeNull();
    expect(shortcut(k("s", { ctrlKey: true }))).toBeNull();
  });
});

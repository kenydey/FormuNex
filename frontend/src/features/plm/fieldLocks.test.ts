import { describe, expect, it } from "vitest";
import { isLocked, lockField, locksFor, snapshotValue, unlockField } from "./fieldLocks";

describe("field locks", () => {
  it("locks, unlocks and records history", () => {
    const { store, history } = lockField({}, [], "doc-1",
      { fieldKey: "s1.name", label: "产品名称", value: "X" }, "u1", "已核对");
    expect(isLocked(store, "doc-1", "s1.name")).toBe(true);
    expect(locksFor(store, "doc-1")).toHaveLength(1);
    expect(history[0]).toMatchObject({ action: "lock", by: "u1", note: "已核对" });
    // Re-locking the same field replaces it, not duplicates it.
    const again = lockField(store, history, "doc-1", { fieldKey: "s1.name", label: "产品名称", value: "Y" }, "u2");
    expect(locksFor(again.store, "doc-1")).toHaveLength(1);
    expect(locksFor(again.store, "doc-1")[0].value).toBe("Y");
    const off = unlockField(again.store, again.history, "doc-1", "s1.name", "u2");
    expect(isLocked(off.store, "doc-1", "s1.name")).toBe(false);
    expect(off.history[0]).toMatchObject({ action: "unlock", by: "u2" });
  });

  it("snapshots values for the lock record", () => {
    expect(snapshotValue(null)).toBe("—");
    expect(snapshotValue("abc")).toBe("abc");
    expect(snapshotValue([{ code: "H314", text: "造成严重皮肤灼伤" }])).toContain("H314");
  });
});

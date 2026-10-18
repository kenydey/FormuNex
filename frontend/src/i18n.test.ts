import { describe, expect, it } from "vitest";
// @ts-expect-error plain ESM helper without types
import { extract } from "../scripts/i18n-keys.mjs";
import en from "./locales/en-US";
import { NAV, type NavItem } from "./app/nav";
import { STATUS_ZH } from "./shared/tags";

const navKeys = (items: NavItem[]): string[] => items.flatMap((i) => [i.key, ...navKeys(i.children ?? [])]);

describe("i18n", () => {
  it("every static key in the code has an English string", () => {
    const keys = [...(extract("src") as Map<string, string>).keys()];
    expect(keys.length).toBeGreaterThan(400);
    expect(keys.filter((k) => !(k in en))).toEqual([]);
  });

  it("dynamic keys (navigation, statuses) are translated", () => {
    expect(navKeys(NAV).filter((k) => !(k in en))).toEqual([]);
    expect(Object.keys(STATUS_ZH).map((s) => `status.${s}`).filter((k) => !(k in en))).toEqual([]);
  });
});

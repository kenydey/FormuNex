// List every t("key", "默认中文") used in src (static keys only) — used by i18n.test.ts and to draft en-US.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

export function walk(dir, out = []) {
  for (const f of readdirSync(dir)) {
    const p = join(dir, f);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.tsx?$/.test(f) && !/\.test\.tsx?$/.test(f)) out.push(p);
  }
  return out;
}

export function extract(root = "src") {
  const keys = new Map();
  const re = /\bt\(\s*"([a-z][\w.]*)"\s*,\s*"((?:[^"\\]|\\.)*)"/g;
  for (const file of walk(root)) {
    const src = readFileSync(file, "utf8");
    for (const m of src.matchAll(re)) if (!keys.has(m[1])) keys.set(m[1], m[2]);
  }
  return keys;
}

if (process.argv[1]?.endsWith("i18n-keys.mjs")) {
  for (const [k, v] of extract()) console.log(`${k}\t${v}`);
}

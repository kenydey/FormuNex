"""Stage 3: vote glyph key → character from length-matched OCR lines, then decode every line
from its glyph keys and assemble table entries.

usage: decode.py lines.json glyphs.json overrides.json out_prefix
overrides.json: {glyph key: char} — manual labels for keys the vote could not settle.
"""
import collections
import json
import re
import sys

LINES, GLYPHS, OVR, OUT = sys.argv[1:5]
lines = json.load(open(LINES))
overrides = json.load(open(OVR)) if OVR != "-" else {}

# ---------------------------------------------------------------- vote
votes: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
seen: collections.Counter = collections.Counter()
for ln in lines:
    for k in ln["keys"]:
        seen[k] += 1
    text = ln["ocr"].replace(" ", "")
    if len(text) != len(ln["keys"]) or ln["score"] < 0.5:
        continue
    for k, ch in zip(ln["keys"], text):
        votes[k][ch] += ln["score"]

label: dict[str, str] = {}
report = []
for k in seen:
    if k in overrides:
        label[k] = overrides[k]
        continue
    v = votes.get(k)
    if not v:
        report.append({"k": k, "n": seen[k], "votes": {}, "why": "no votes"})
        label[k] = "�"
        continue
    (top, w), total = v.most_common(1)[0], sum(v.values())
    label[k] = top
    if w / total < 0.9 or total < 2:
        report.append({"k": k, "n": seen[k], "votes": dict(v.most_common(4)), "why": f"share {w / total:.2f}"})

# ---------------------------------------------------------------- decode lines (spaces from geometry)
# attach glyphs to lines (same page, same keys order, within bbox)
by_page = collections.defaultdict(list)
for p in json.load(open(GLYPHS)):
    if "skip" not in p:
        by_page[p["page"]] = p
for ln in lines:
    p = by_page[ln["page"]]
    x0, y0, x1, y1 = ln["bbox"]
    gs = [g for g in p["glyphs"] if x0 <= g["x"] <= x1 and y0 <= g["y"] <= y1
          and tuple(g["dir"]) == (0.0, -1.0)]
    gs.sort(key=lambda g: -g["y"])
    if [g["k"] for g in gs] == ln["keys"]:
        ln["_g"] = gs

# Spaces: the fonts carry bogus widths, so learn each glyph's normal advance (origin-to-origin
# distance to the next glyph, in em) as its most common value; a space shows as extra advance.
adv: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
pair: dict[tuple, collections.Counter] = collections.defaultdict(collections.Counter)
for ln in lines:
    gs = ln.get("_g") or []
    for a, b in zip(gs, gs[1:]):
        d = round((a["o"][1] - b["o"][1]) / a["size"], 2)
        adv[a["k"]][d] += 1
        pair[(a["k"], b["k"])][d] += 1
mode_adv = {k: c.most_common(1)[0][0] for k, c in adv.items()}
# A pair's normal advance (kerning) — only trusted when the pair is common and its most common
# advance is not itself a spaced one.
mode_pair = {}
for kk, c in pair.items():
    (d, n) = c.most_common(1)[0]
    if n >= 3 and d - mode_adv[kk[0]] <= 0.15:
        mode_pair[kk] = min(d for d, _ in c.items() if c[d] >= max(2, n // 4))


def line_text(ln):
    text = "".join(label[k] for k in ln["keys"])
    if ln["col"] != 3:
        return text
    o = ln["ocr"].strip()
    if len(o.replace(" ", "")) != len(ln["keys"]):
        return text
    # Letters from the glyph layer, word breaks from the OCR line.
    out, i = "", 0
    for ch in o:
        if ch == " ":
            out += " "
        else:
            out += text[i]
            i += 1
    return re.sub(r" +", " ", out)


for ln in lines:
    ln["text"] = line_text(ln)

# ---------------------------------------------------------------- assemble cells
# Column bands come from constant-y rules; every vertical rule inside an entry splits only the
# columns it actually crosses, so per column the cell boundaries differ. An entry's output rows
# are the finest partition; a cell spanning several of them is copied into each.
import pymupdf as fitz  # noqa: E402

PDF = "gb12268-2025.pdf"
doc = fitz.open(PDF)


def vsegs(pno):
    out = []
    for dr in doc[pno].get_drawings():
        for it in dr["items"]:
            if it[0] == "l":
                a, b = it[1], it[2]
                if abs(a.x - b.x) < 0.5:
                    out.append((a.x, min(a.y, b.y), max(a.y, b.y)))
    return out


def join(texts, col):
    out = ""
    for t in texts:
        t = t.strip()
        if not t:
            continue
        if not out:
            out = t
        elif col == 3:
            out += t if out.endswith("-") else " " + t
        elif out[-1].isascii() and t[0].isascii() and col != 2:
            out += " " + t
        else:
            out += t
    return out


by_line_page = collections.defaultdict(list)
for ln in lines:
    xs = [g["x"] for g in ln.get("_g") or []] or [(ln["bbox"][0] + ln["bbox"][2]) / 2]
    ln["cx"] = sum(xs) / len(xs)
    by_line_page[ln["page"]].append(ln)

entries = []
for page in sorted(by_page):
    p = by_page[page]
    cols = p["cols"]                      # ascending y; band i is column 13 - i
    bands = {13 - i: (cols[i], cols[i + 1]) for i in range(13)}
    rows = p["rows"]
    segs = vsegs(page)
    for ri in range(len(rows) - 1):
        x0, x1 = rows[ri], rows[ri + 1]
        inner = [s for s in segs if x0 + 1 < s[0] < x1 - 1]
        bounds = {}
        for col, (lo, hi) in bands.items():
            xs = sorted({round(s[0], 1) for s in inner if s[1] <= lo + 1 and s[2] >= hi - 1})
            bounds[col] = [x0] + xs + [x1]
        finest = sorted({x for b in bounds.values() for x in b})
        parts = list(zip(finest[:-1], finest[1:]))
        lns = [l for l in by_line_page[page] if x0 <= l["cx"] < x1]
        def cell(col, a, b):
            bl = bounds[col]
            for lo, hi in zip(bl[:-1], bl[1:]):
                if lo - 0.5 <= a and b <= hi + 0.5:
                    sel = sorted((l for l in lns if l["col"] == col and lo <= l["cx"] < hi), key=lambda l: l["cx"])
                    return join([l["text"] for l in sel], col)
            return ""
        out_rows = []
        for a, b in parts:
            out_rows.append({str(col): cell(col, a, b) for col in range(1, 14)})
        if not out_rows or "联合国" in out_rows[0]["1"] or out_rows[0]["1"].startswith(("(1", "（1")):
            continue
        entries.append({"page": page, "row": ri, "rows": out_rows})

json.dump(label, open(OUT + "_labels.json", "w"), ensure_ascii=False)
json.dump(report, open(OUT + "_report.json", "w"), ensure_ascii=False, indent=0)
json.dump(entries, open(OUT + "_entries.json", "w"), ensure_ascii=False)
print("keys", len(seen), "to review", len(report), "no votes", sum(1 for r in report if r["why"] == "no votes"),
      "entries", len(entries), "rows", sum(len(e["rows"]) for e in entries),
      "lines w/o geometry", sum(1 for l in lines if "_g" not in l))

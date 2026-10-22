"""Stage 1: glyph layer of GB 12268-2025 table pages.

For every table page: column boundaries (constant-y lines), row / sub-row boundaries
(constant-x lines), and every glyph with its bbox, cell and an outline key (hash of the
glyph outline in font units, so the same character has the same key on every page even
though each page carries its own font subset with its own codes).
"""
import hashlib
import io
import json
import sys
import warnings

warnings.filterwarnings("ignore")
import pymupdf as fitz  # noqa: E402
from fontTools.cffLib import CFFFontSet  # noqa: E402
from fontTools.pens.recordingPen import RecordingPen  # noqa: E402
from fontTools.ttLib import TTFont  # noqa: E402

doc = fitz.open(sys.argv[1])
_font_cache: dict[int, object] = {}


def _glyphs(xref: int):
    """gid → outline key for one embedded font."""
    if xref in _font_cache:
        return _font_cache[xref]
    name, ext, _typ, buf = doc.extract_font(xref)
    out = {}
    if ext in ("cff", "cid"):
        cff = CFFFontSet()
        cff.decompile(io.BytesIO(buf), None)
        top = cff[cff.fontNames[0]]
        cs = top.CharStrings
        order = top.charset
        def key(gid):
            pen = RecordingPen()
            cs[order[gid]].draw(pen)
            return pen.value
    elif ext == "ttf":
        tt = TTFont(io.BytesIO(buf))
        gs = tt.getGlyphSet()
        order = tt.getGlyphOrder()
        def key(gid):
            pen = RecordingPen()
            gs[order[gid]].draw(pen)
            return pen.value
    else:
        raise SystemExit(f"font {xref} {name} {ext}")
    class K:
        def __getitem__(self, gid):
            if gid not in out:
                try:
                    v = key(gid)
                except Exception as e:  # noqa: BLE001
                    v = ("ERR", str(e))
                out[gid] = hashlib.sha1(repr(v).encode()).hexdigest()[:16] if v else "EMPTY"
            return out[gid]
    _font_cache[xref] = K()
    return _font_cache[xref]


def _lines(page):
    H, V = set(), set()
    for dr in page.get_drawings():
        for it in dr["items"]:
            if it[0] == "l":
                a, b = it[1], it[2]
                if abs(a.y - b.y) < 0.5:
                    H.add((round(a.y, 1), round(min(a.x, b.x), 1), round(max(a.x, b.x), 1)))
                elif abs(a.x - b.x) < 0.5:
                    V.add((round(a.x, 1), round(min(a.y, b.y), 1), round(max(a.y, b.y), 1)))
    return H, V


def _cluster(vals, tol=1.5):
    out = []
    for v in sorted(vals):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(c) / len(c) for c in out]


pages = []
for pno in range(doc.page_count):
    page = doc[pno]
    H, V = _lines(page)
    if not H or not V:
        continue
    cols = _cluster([h[0] for h in H])
    if len(cols) != 14:
        pages.append({"page": pno, "skip": f"{len(cols)} column lines"})
        continue
    top = cols[-1]  # column (1)'s outer edge (largest y)
    full = _cluster([v[0] for v in V if v[2] >= top - 2])          # entry boundaries
    sub = _cluster([v[0] for v in V if v[2] < cols[-4] + 2])        # sub-row boundaries, columns 4–13 only
    fonts = {f[3].split("+", 1)[-1]: f[0] for f in page.get_fonts()}
    glyphs = []
    for span in page.get_texttrace():
        fname = span["font"]
        xref = next((x for n, x in fonts.items() if n.startswith(fname) or fname.startswith(n)), None)
        if xref is None:
            raise SystemExit(f"page {pno}: font {fname} not found in {list(fonts)}")
        K = _glyphs(xref)
        for ch in span["chars"]:
            bx = ch[3]
            cx, cy = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
            glyphs.append({"k": K[ch[1]], "b": [round(v, 2) for v in bx], "x": round(cx, 2), "y": round(cy, 2),
                           "o": [round(ch[2][0], 2), round(ch[2][1], 2)],
                           "f": fname, "dir": span["dir"], "size": round(span["size"], 2)})
    pages.append({"page": pno, "cols": cols, "rows": full, "subrows": sub, "glyphs": glyphs,
                  "w": page.rect.width, "h": page.rect.height})
    if pno % 50 == 0:
        print(pno, len(glyphs), file=sys.stderr)

json.dump(pages, open(sys.argv[2], "w"))
ok = [p for p in pages if "skip" not in p]
print("table pages", len(ok), "first", ok[0]["page"], "last", ok[-1]["page"],
      "skipped", [(p["page"], p["skip"]) for p in pages if "skip" in p][:30])
keys = {g["k"] for p in ok for g in p["glyphs"]}
print("glyphs", sum(len(p["glyphs"]) for p in ok), "unique keys", len(keys))

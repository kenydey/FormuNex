"""Stage 2: group the glyphs of each table cell into text lines and OCR every line image.

Output (lines.json): per line — page, cell (row index, sub-row index, column), glyph keys in
reading order, and the OCR text with its score. Stage 3 votes glyph key → character.
"""
import json
import sys
import warnings
from multiprocessing import Pool

warnings.filterwarnings("ignore")
import numpy as np  # noqa: E402
import pymupdf as fitz  # noqa: E402

PDF, GLYPHS, OUT = sys.argv[1:4]


def _band(v, edges):
    """index i with edges[i] <= v < edges[i+1] (edges ascending), or None."""
    for i in range(len(edges) - 1):
        if edges[i] <= v < edges[i + 1]:
            return i
    return None


def page_lines(p):
    cols = p["cols"]        # ascending y; column (1) is the last band (largest y)
    rows = p["rows"]        # ascending x; entry boundaries
    subs = sorted(set(p["subrows"]) | set(rows))
    cells = {}
    for g in p["glyphs"]:
        if g["dir"] != [0.0, -1.0] and tuple(g["dir"]) != (0.0, -1.0):
            continue
        ci = _band(g["y"], cols)
        ri = _band(g["x"], rows)
        if ci is None or ri is None:
            continue
        col = 13 - ci       # 1..13
        si = _band(g["x"], subs)
        key = (ri, si if col >= 4 else None, col)
        cells.setdefault(key, []).append(g)
    lines = []
    for (ri, si, col), gs in cells.items():
        gs.sort(key=lambda g: g["x"])
        groups = []
        for g in gs:
            if groups and abs(g["x"] - groups[-1][-1]["x"]) <= 2.5:
                groups[-1].append(g)
            else:
                groups.append([g])
        for li, grp in enumerate(groups):
            grp.sort(key=lambda g: -g["y"])       # text runs upward
            x0 = min(g["b"][0] for g in grp); x1 = max(g["b"][2] for g in grp)
            y0 = min(g["b"][1] for g in grp); y1 = max(g["b"][3] for g in grp)
            lines.append({"page": p["page"], "row": ri, "sub": si, "col": col, "line": li,
                          "keys": [g["k"] for g in grp], "bbox": [x0 - 1.5, y0 - 2, x1 + 1.5, y1 + 2]})
    return lines


_engine = None
_doc = None


def ocr_page(p):
    global _engine, _doc
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _engine = RapidOCR()
        _doc = fitz.open(PDF)
    page = _doc[p["page"]]
    out = []
    for ln in page_lines(p):
        pix = page.get_pixmap(clip=fitz.Rect(ln["bbox"]), dpi=300)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3]
        img = np.ascontiguousarray(np.rot90(img, k=-1))   # upward text → left-to-right
        img = np.pad(img, ((8, 8), (8, 8), (0, 0)), constant_values=255)
        res, _ = _engine(img, use_det=False, use_cls=False, use_rec=True)
        text, score = (res[0][0], float(res[0][1])) if res else ("", 0.0)
        out.append({**ln, "ocr": text, "score": round(score, 3)})
    return out


if __name__ == "__main__":
    pages = [p for p in json.load(open(GLYPHS)) if "skip" not in p]
    if len(sys.argv) > 4:
        pages = [p for p in pages if p["page"] in {int(x) for x in sys.argv[4].split(",")}]
    with Pool(4) as pool:
        res = []
        for i, r in enumerate(pool.imap(ocr_page, pages, chunksize=2)):
            res.extend(r)
            if i % 25 == 0:
                print(i, len(res), file=sys.stderr, flush=True)
    json.dump(res, open(OUT, "w"), ensure_ascii=False)
    ok = sum(1 for r in res if len(r["ocr"].replace(" ", "")) == len(r["keys"]))
    print("lines", len(res), "length-match", ok)

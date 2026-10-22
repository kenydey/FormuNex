"""Stage 4: normalise the decoded rows into the importer's resource format.

usage: finalize.py r_entries.json pdf_sha256 out.json [un_reference.xlsx]

The optional reference (a 2017 UN dangerous-goods list) only supplies word spacing for an
English name whose letters are identical to the decoded ones; nothing else is taken from it.
"""
import collections
import json
import re
import sys

ENTRIES, SHA, OUT = sys.argv[1:4]
REF = sys.argv[4] if len(sys.argv) > 4 else None

ref = collections.defaultdict(list)
if REF:
    import openpyxl

    ws = openpyxl.load_workbook(REF, read_only=True).worksheets[0]
    for r in ws.iter_rows(values_only=True):
        if isinstance(r[0], (int, float)) and r[1]:
            ref["%04d" % int(r[0])].append(re.sub(r"\s+", " ", str(r[1]).replace("†", "")).strip())

ZH = str.maketrans({",": "，", ";": "；", ":": "：", "(": "（", ")": "）"})
EN = str.maketrans({"，": ",", "；": ";", "：": ":", "（": "(", "）": ")"})
PG = {"I": "I", "Ⅰ": "I", "Ⅱ": "II", "Ⅲ": "III"}


def none_dash(v):
    v = (v or "").strip()
    return "" if v in ("—", "——") else v


def zh(v):
    return re.sub(r"\s+", "", v).translate(ZH)


def en(un, v):
    v = v.translate(EN)
    letters = re.sub(r"\s", "", v)
    for cand in ref.get(un, []):
        c = cand.translate(EN)
        if re.sub(r"\s", "", c) == letters:
            return c, "reference"
    v = re.sub(r"\s+", " ", v)
    v = re.sub(r"\s+([,;:)\]%])", r"\1", v)
    v = re.sub(r"([(\[])\s+", r"\1", v)
    v = re.sub(r",(?=[A-Za-z])", ", ", v)
    return v.strip(), "ocr"


def qty(v):
    v = none_dash(v)
    return re.sub(r"^(\d+)\s*(mL|L|kg|g)$", r"\1 \2", v)


entries = json.load(open(ENTRIES))
rows, spacing = [], collections.Counter()
for e in entries:
    for r in e["rows"]:
        name_en, how = en(r["1"], r["3"])
        spacing[how] += 1
        rows.append({
            "un": r["1"],
            "name_cn": zh(r["2"]),
            "name_en": name_en,
            "class": none_dash(r["4"]),
            "subsidiary": " ".join(none_dash(r["5"]).split()),
            "pg": PG.get(none_dash(r["6"])) or None,
            "special_provisions": " ".join(none_dash(r["7"]).split()),
            "limited_quantity": qty(r["8"]),
            "excepted_quantity": none_dash(r["9"]),
            "packing_instructions": none_dash(r["10"]),
            "packing_provisions": none_dash(r["11"]),
            "tank_instructions": none_dash(r["12"]),
            "tank_provisions": none_dash(r["13"]),
            "page": e["page"] + 1,
        })

# exact duplicates (a merged cell copied into identical sub-rows) collapse to one
seen, uniq = set(), []
for r in rows:
    key = tuple((k, v) for k, v in r.items() if k != "page")
    if key not in seen:
        seen.add(key)
        uniq.append(r)

data = {
    "meta": {
        "standard": "GB 12268-2025",
        "title": "危险货物品名表（表 1）",
        "source_pdf_sha256": SHA,
        "pages": "PDF 第 12–388 页（标准印刷页码第 6–382 页）",
        "method": "PDF 字体为方正私有编码，文本层不可直接读取：按字形轮廓哈希（1,181 种字形）"
                  "对 58,984 行文字做 OCR 并按字形投票定字，人工逐个核对 1,070 余种低频 / 有分歧 / 化学专用字形；"
                  "表格结构取自 PDF 表格线（合并单元格按其实际跨越的栏展开）。",
        "verification": "联合国编号 2,347 个、严格递增；类别 / 包装类别值域校验全部通过；"
                        "92% 行的英文名称字母与独立的 2017 年联合国品名表逐字一致，其余差异为版本修订与原文印刷。",
        "printed_typos_kept": ["UN 1756 CHROMIC FLURIDE SOLID", "UN 1913 NE0N（数字 0）",
                               "UN 2252 1,2-DIMETHYOXYETHANE", "UN 3482 ALKALI MEAL DISPERSION"],
        "english_spacing": dict(spacing),
        "rows": len(uniq),
        "un_numbers": len({r["un"] for r in uniq}),
    },
    "entries": uniq,
}
json.dump(data, open(OUT, "w"), ensure_ascii=False, indent=1)
print(json.dumps(data["meta"], ensure_ascii=False)[:600])

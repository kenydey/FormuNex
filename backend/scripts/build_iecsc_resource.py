#!/usr/bin/env python3
"""Build ``app/resources/regulatory/iecsc.json.gz`` from the official 《中国现有化学物质名录》 files.

Sources (生态环境部, https://www.mee.gov.cn/ywgz/gtfwyhxpgl/hxphjgl/wzml/):

* 2013 版名录（环境保护部公告 2013 年第 1 号）: the RAR attachment holding ``名录2013年版.pdf``
  (42,342 public entries; the 3,270 confidential entries are not published).
* Every later 公告 on the index page — 增补 batches and 已登记新化学物质列入 batches. Their
  lists are either HTML tables on the page or PDF attachments. 变更 attachments (allowed-use
  changes of entries already listed) add nothing and are skipped.

Only CAS numbers are kept (plus the 2013 版 流水号 of entries published without one): the importer
uses them to set ``substances.iecsc_listed``. A CAS printed with a wrong check digit in the
official text is recorded under ``invalid`` exactly as printed — never repaired.

Re-run when MEE publishes a new 公告; the downloads are cached in ``--cache``.

用法:
    .venv/bin/python scripts/build_iecsc_resource.py --cache /tmp/iecsc
"""
from __future__ import annotations

import argparse
import collections
import gzip
import html
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.plm.regulatory import cas as cas_mod  # noqa: E402

INDEX = "https://www.mee.gov.cn/ywgz/gtfwyhxpgl/hxphjgl/wzml/"
INDEX_PAGES = ("index.shtml", "index_1.shtml", "index_2.shtml", "index_3.shtml")
BASE_2013 = "t20130131_245810"
OUT = Path(__file__).resolve().parent.parent / "app" / "resources" / "regulatory" / "iecsc.json.gz"

_CAS_EXACT = re.compile(r"^\d{2,7}-\d{2}-\d$")
_CAS_IN_TEXT = re.compile(r"(?<![\d-])(\d{2,7})\s*-\s*(\d{2})\s*-\s*(\d)(?![\d-])")


def _get(url: str, dest: Path) -> Path:
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=300) as r:  # noqa: S310 — fixed government host
            dest.write_bytes(r.read())
    return dest


def _announcements(cache: Path) -> list[tuple[str, str, str]]:
    """[(page id, title, url)] for every 公告 on the index pages, oldest first."""
    seen: dict[str, tuple[str, str, str]] = {}
    for page in INDEX_PAGES:
        text = _get(INDEX + page, cache / "index" / page).read_text(encoding="utf-8", errors="ignore")
        for href, title in re.findall(r'href="([^"]+)"[^>]*>([^<]*公告)</a>', text):
            url = urllib.parse.urljoin(INDEX, href)
            pid = url.rsplit("/", 1)[1].split(".")[0]
            seen[pid] = (pid, title.strip(), url)
    return sorted(seen.values(), key=lambda a: a[0])


def _attachments(page_html: str) -> list[str]:
    return sorted(set(re.findall(r'href="([^"]+\.(?:pdf|rar))"', page_html, re.I)))


def _page_text(page_html: str) -> str:
    body = re.sub(r"(?s)<script.*?</script>|<style.*?</style>", "", page_html)
    body = re.sub(r"</t[dh]>", " | ", re.sub(r"</tr>", "\n", body))
    return html.unescape(re.sub(r"<[^>]+>", " ", body))


def _pdf_text(path: Path) -> str:
    import fitz

    with fitz.open(path) as doc:
        return "\n".join(p.get_text() for p in doc)


def _parse_2013(pdf: Path) -> dict[int, str]:
    """{序号: CAS 号或流水号 as printed}. The pages are rotated: rows run along x, the 序号
    column sits at the top (y > 735) and the CAS column at the bottom (y < 125)."""
    import fitz

    rows: dict[int, list[str]] = collections.defaultdict(list)
    with fitz.open(pdf) as doc:
        for page in doc:
            words = page.get_text("words")
            heads = {w[4] for w in words}
            if "序号" not in heads or "CAS" not in heads:
                continue  # cover / preface pages
            hdr_x1 = max(w[2] for w in words if w[4] in ("序号", "CAS", "或流水号"))
            body = [w for w in words if w[0] > hdr_x1 - 1]
            nos = [w for w in body if w[1] > 735 and re.fullmatch(r"\d{1,5}", w[4])]
            if not nos:
                continue
            for n in nos:
                rows.setdefault(int(n[4]), [])
            for w in (w for w in body if w[3] < 125):
                cx = (w[0] + w[2]) / 2
                n = min(nos, key=lambda n: abs((n[0] + n[2]) / 2 - cx))
                rows[int(n[4])].append(w[4])
    return {no: "".join(toks) for no, toks in rows.items()}


def build(cache: Path) -> dict:
    listed: dict[str, str] = {}      # CAS → source id (first listing wins)
    serials: dict[str, str] = {}     # 2013 版流水号 → source id (later lists: no fixed column to read it from)
    invalid: list[dict] = []
    sources: list[dict] = []

    def add_cas(cas: str, src: str) -> None:
        listed.setdefault(cas, src)

    for pid, title, url in _announcements(cache):
        page = _get(url, cache / pid / "page.html").read_text(encoding="utf-8", errors="ignore")
        if pid == BASE_2013:
            rar = _get(urllib.parse.urljoin(url, _attachments(page)[0]), cache / pid / "iecsc2013.rar")
            pdf = cache / pid / "名录2013年版.pdf"
            if not pdf.exists():
                subprocess.run(["unar", "-q", "-f", "-D", "-e", "GB18030", "-o", str(pdf.parent), str(rar)], check=True)
            rows = _parse_2013(pdf)
            if sorted(rows) != list(range(1, len(rows) + 1)):
                raise SystemExit("2013 版序号不连续，版式解析有误")
            n_cas = 0
            for no, raw in rows.items():
                if _CAS_EXACT.match(raw) and cas_mod.is_valid(raw):
                    add_cas(raw, "2013")
                    n_cas += 1
                elif re.fullmatch(r"\d{3,6}", raw):
                    serials.setdefault(raw, "2013")
                else:
                    invalid.append({"source": "2013", "no": no, "printed": raw})
            sources.append({"id": "2013", "title": title, "url": url, "entries": len(rows), "cas": n_cas})
            continue
        texts = [("page", _page_text(page))]
        for att in _attachments(page):
            au = urllib.parse.urljoin(url, att)
            path = _get(au, cache / pid / au.rsplit("/", 1)[1])
            text = _pdf_text(path)
            if "变更" in text[:300]:
                continue  # allowed-use changes of entries already listed
            texts.append((au.rsplit("/", 1)[1], text))
        n_cas = 0
        for _, text in texts:
            for m in _CAS_IN_TEXT.finditer(text):
                cas = "%s-%s-%s" % m.groups()
                if cas_mod.is_valid(cas):
                    if cas not in listed:
                        n_cas += 1
                    add_cas(cas, pid)
                else:
                    invalid.append({"source": pid, "printed": cas})
        sources.append({"id": pid, "title": title, "url": url, "new_cas": n_cas})
    return {
        "meta": {
            "name": "中国现有化学物质名录（IECSC）",
            "publisher": "生态环境部",
            "index": INDEX,
            "retrieved": date.today().isoformat(),
            "note": "仅含公开条目的 CAS 号；保密条目（2013 版 3,270 条）未公开，"
                    "不在本表中不等于未列入名录。印刷错误的 CAS 原样记入 invalid，不做修正。",
            "sources": sources,
        },
        "cas": dict(sorted(listed.items())),
        "serials": dict(sorted(serials.items())),
        "invalid": invalid,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache", type=Path, required=True, help="Download cache directory")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    data = build(args.cache)
    with gzip.open(args.out, "wt", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(json.dumps({"cas": len(data["cas"]), "serials": len(data["serials"]), "invalid": len(data["invalid"]),
                      "sources": len(data["meta"]["sources"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

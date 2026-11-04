"""Three-library C1: header/footer spans, label registry, figure roles,
text reaction dedup, white-row split + QC, figure cache."""
from __future__ import annotations

import io

from PIL import Image, ImageDraw

from app.services import (
    doc_label_registry as dlr,
    figure_cache as fc,
    figure_roles as fr,
    text_header_footer as thf,
    text_reaction_dedup as trd,
    white_row_split as wrs,
)


def _page(height, spans):
    return {"height": height, "spans": spans}


def test_header_footer_repeat_symbols_doi():
    pages = [
        _page(1000, [
            {"text": "Journal of Chemistry", "y0": 10, "y1": 30},
            {"text": "— — —", "y0": 950, "y1": 970},
            {"text": "Body text here", "y0": 500, "y1": 520},
            {"text": "doi: 10.1000/xyz123", "y0": 15, "y1": 25},
        ]),
        _page(1000, [
            {"text": "Journal of Chemistry", "y0": 10, "y1": 30},
            {"text": "Unique Header Title", "y0": 10, "y1": 30},
            {"text": "Body text two", "y0": 500, "y1": 520},
        ]),
    ]
    out = thf.strip_header_footer(pages)
    p1_texts = [s["text"] for s in out[0]["spans"]]
    assert "Journal of Chemistry" not in p1_texts  # repeats on 2 pages
    assert "— — —" not in p1_texts  # pure symbols in footer zone
    assert "doi: 10.1000/xyz123" not in p1_texts  # DOI link
    assert "Body text here" in p1_texts
    p2_texts = [s["text"] for s in out[1]["spans"]]
    assert "Unique Header Title" in p2_texts  # unique header-zone text kept
    assert "Body text two" in p2_texts


def test_label_registry_drawn_priority_and_backfill():
    reg = dlr.DocumentLabelRegistry()
    reg.register_text("1a", "CCN")  # text name first
    reg.register_drawn("1a", "CCO")  # drawn beats text
    assert reg.resolve("1a") == "CCO"
    assert reg.resolve("unknown") is None
    sides, filled = reg.backfill([
        {"smiles": "", "label": "1a"},
        {"smiles": "CCC", "label": "2a"},
        {"smiles": "", "label": "9z"},
    ])
    assert sides[0]["smiles"] == "CCO"
    assert sides[0]["source"] == "registry"
    assert sides[1]["smiles"] == "CCC"  # already has a structure
    assert sides[2]["smiles"] == ""  # unknown: never guessed
    assert filled == ["1a"]


def test_label_registry_si_merge_first_drawn_wins():
    main = dlr.DocumentLabelRegistry()
    main.register_drawn("1a", "CCO")
    si = dlr.DocumentLabelRegistry()
    si.register_drawn("1a", "CCN")
    si.register_drawn("2a", "CCC")
    main.merge(si)
    assert main.resolve("1a") == "CCO"  # main doc keeps priority
    assert main.resolve("2a") == "CCC"


def test_figure_roles_caption_first():
    assert fr.classify_figure("Figure 1. 1H NMR spectrum of 1a") == fr.ROLE_SPECTRA
    assert fr.classify_figure("Scheme 1. Synthesis of 1a") == fr.ROLE_REACTION_SCHEME
    assert fr.classify_figure("Figure 2. Yield vs. temperature plot") == fr.ROLE_DATA_PLOT
    assert fr.classify_figure(
        "Figure 3. Compound 1a", ["As shown in the NMR spectrum (Fig. 3)."]
    ) == fr.ROLE_SPECTRA
    assert fr.classify_figure("Figure 4. Compound 1a") == fr.ROLE_OTHER
    assert fr.skip_extraction(fr.ROLE_SPECTRA) is True
    assert fr.skip_extraction(fr.ROLE_REACTION_SCHEME) is False


def test_text_reaction_dedup():
    figure = [{"reactants": ["CCN", "CCO"], "products": ["CCCO"]}]
    text = [
        {"reactants": ["CCO", "CCN"], "products": ["CCCO"]},  # order differs
        {"reactants": ["CCO"], "products": ["CCN"]},  # new
    ]
    out = trd.dedup_text_reactions(text, figure)
    assert out[0].get("text_mention") is True
    assert "source" not in out[0]
    assert out[1].get("source") == "text"


def _banded_image():
    img = Image.new("RGB", (100, 200), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 99, 59], fill="black")
    draw.rectangle([0, 80, 99, 179], fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_white_row_split_and_qc():
    segments, cuts = wrs.split_white_rows(_banded_image())
    assert cuts == [60, 180]
    assert len(segments) == 3
    blank = Image.new("RGB", (50, 50), "white")
    buf = io.BytesIO()
    blank.save(buf, format="PNG")
    assert wrs.qc_segment(buf.getvalue()) == wrs.QC_NO_IMAGE_CONTENT
    assert wrs.qc_segment(segments[1], captions=[]) == wrs.QC_NO_CAPTION
    assert wrs.qc_segment(
        segments[1], captions=["Fig. 1", "Fig. 2"]
    ) == wrs.QC_MULTIPLE_CAPTIONS
    assert wrs.qc_segment(segments[1], captions=["Fig. 1"]) == wrs.QC_OK


def test_recall_gate_json_bool_not_substring():
    assert wrs.recall_gate(True) is True
    assert wrs.recall_gate(False) is False
    assert wrs.recall_gate("true") is True
    assert wrs.recall_gate("false") is False
    assert wrs.recall_gate("true-ish") is True  # unparsable -> uncertain -> keep
    assert wrs.recall_gate("yes") is True
    assert wrs.recall_gate(None) is True
    kept = wrs.filter_segments(["a", "b"], [wrs.QC_NO_IMAGE_CONTENT, wrs.QC_OK])
    assert kept == ["b"]


def test_figure_cache_roundtrip_and_fail_open(tmp_path):
    cache = fc.FigureCache(tmp_path)
    key = fc.figure_key("sha", 3, (0, 0, 10, 10))
    assert cache.get(fc.FIGURE_KIND, key) is None
    assert cache.put(fc.FIGURE_KIND, key, {"smiles": "CCO"}) is True
    assert cache.get(fc.FIGURE_KIND, key) == {"smiles": "CCO"}
    assert cache.get(fc.DETECTION_KIND, fc.detection_key("sha", 3)) is None
    # Corrupt entry fails open.
    path = cache._path(fc.FIGURE_KIND, key)
    path.write_text("{not json", encoding="utf-8")
    assert cache.get(fc.FIGURE_KIND, key) is None

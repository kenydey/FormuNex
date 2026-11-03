"""Chunk-level chemistry & commercial-product entity extraction (rule tier).

Runs inside ``kb_index.index_source`` on every chunk, fully offline:

* **CAS numbers** — format + check-digit validation;
* **molecular formulas** — candidates validated by the self-contained parser
  in ``domain.chemistry.molar_mass`` (raises on garbage), plus anti-acronym
  heuristics so prose like "NO" or "In" doesn't pollute the index;
* **SMILES** — only when RDKit is installed (candidates must parse; canonical
  form is stored), because without a real parser SMILES regexes are noise;
* **reaction equations** — ``A + B → C`` arrow patterns (unicode / ASCII /
  LaTeX arrows) whose sides contain valid formula tokens;
* **commercial products** — trade-name + grade patterns (``Epon 828``,
  ``BYK-333``), accepted only with corroboration: a ™/® mark, a known brand
  prefix, or supplier-context wording nearby (EN + CN).  The LLM tier
  (``SourceGuideSchema.products``) complements this per document.

Everything returns plain dicts sized for ``DocumentChunk.meta`` (JSON).
"""
from __future__ import annotations

import logging
import re

from .errors import degrade_return

logger = logging.getLogger(__name__)

_MAX_CHEM_PER_CHUNK = 20
_MAX_PRODUCTS_PER_CHUNK = 10

# ── CAS ──────────────────────────────────────────────────────────────────────

_CAS_RE = re.compile(r"\b(\d{2,7})-(\d{2})-(\d)\b")


def _cas_checksum_ok(part1: str, part2: str, check: str) -> bool:
    digits = f"{part1}{part2}"
    total = sum(int(d) * w for d, w in zip(reversed(digits), range(1, len(digits) + 1)))
    return total % 10 == int(check)


def extract_cas(text: str) -> list[str]:
    out: list[str] = []
    for m in _CAS_RE.finditer(text or ""):
        if _cas_checksum_ok(m.group(1), m.group(2), m.group(3)):
            cas = m.group(0)
            if cas not in out:
                out.append(cas)
    return out


# ── molecular formulas ───────────────────────────────────────────────────────

_FORMULA_CAND_RE = re.compile(
    r"\b(?:[A-Z][a-z]?\d*|\((?:[A-Z][a-z]?\d*)+\)\d*)+\b"
)
# English words that happen to parse as element sequences.
_FORMULA_STOPWORDS = {
    "I", "V", "Y", "W", "U", "K", "B", "C", "N", "O", "P", "S", "F", "H",
    "In", "As", "At", "Be", "He", "No", "Os", "Es", "Al", "Am", "Si", "Sn",
    "NO", "ON", "IN", "AS", "BN", "PC", "CN", "US", "EP", "WO", "OK", "PH",
    "CoO", "NiP", "VOC", "COO", "HNO", "NaN",
}
_TWO_LETTER_ELEMENT_RE = re.compile(r"[A-Z][a-z]")
# v7 KB-6: 单元素符号+纯数字（如 S1、Fe2）多为样品/章节编号，非化学式。
# 白名单放行真实存在的同素异形体/双原子分子。
_SINGLE_ELEMENT_DIGIT_RE = re.compile(r"^[A-Z][a-z]?\d+$")
_SINGLE_ELEMENT_ALLOW = frozenset({"H2", "O2", "N2", "F2", "Cl2", "Br2", "I2", "S8", "P4", "O3", "C60", "C70"})


def _formula_plausible(token: str) -> bool:
    if token in _FORMULA_STOPWORDS or len(token) < 2:
        return False
    # v7 KB-6: S1/S2 这类单元素+数字先拦，白名单除外。
    if _SINGLE_ELEMENT_DIGIT_RE.match(token) and token not in _SINGLE_ELEMENT_ALLOW:
        return False
    has_digit = any(ch.isdigit() for ch in token)
    caps = len(re.findall(r"[A-Z]", token))
    has_two_letter = bool(_TWO_LETTER_ELEMENT_RE.search(token))
    # Require a stoichiometric digit (H2O, Zn3(PO4)2) or a multi-element
    # combination containing a two-letter element (NaCl, ZnO).
    return has_digit or (caps >= 2 and has_two_letter)


def extract_formulas(text: str) -> list[str]:
    from ..domain.chemistry import molar_mass, canonical_formula, sanitize_formula

    out: list[str] = []
    seen_canon: set[str] = set()
    for m in _FORMULA_CAND_RE.finditer(text or ""):
        token = m.group(0)
        if not _formula_plausible(token):
            continue
        # P1-5: recover OCR/LLM case-mangled formulas upstream (ZNO→ZnO);
        # unsalvageable tokens are dropped as before.
        token = sanitize_formula(token)
        if not token:
            continue
        try:
            if molar_mass(token) <= 0:
                continue
        except Exception:
            continue
        # M-B: Hill 序去重 — Zn3(PO4)2 与 Zn3P2O8 判同（保留首个展示形式）
        canon = canonical_formula(token) or token
        if canon in seen_canon:
            continue
        seen_canon.add(canon)
        out.append(token)
    return out


# ── SMILES (RDKit-gated) ─────────────────────────────────────────────────────

_SMILES_CAND_RE = re.compile(r"(?<![\w/])[A-Za-z0-9@+\-\[\]()=#$/\\%:.]{5,120}(?![\w/])")
_SMILES_HINT_RE = re.compile(r"[=#\[\]]|[a-z]\d|\d\(")


def _has_ring_closure(token: str) -> bool:
    """A repeated bare digit (e.g. the two '1's in C1CO1) marks a SMILES ring
    bond pair — formulas never reuse a subscript digit, so this disambiguates
    aliphatic ring SMILES (no brackets/bonds) from stoichiometric formulas."""
    digits = [ch for ch in token if ch.isdigit()]
    return len(digits) != len(set(digits))


def extract_smiles(text: str) -> list[dict]:
    """SMILES tokens verified by RDKit; [] when RDKit is absent (too noisy)."""
    try:
        from rdkit import Chem  # type: ignore
        from rdkit import RDLogger  # type: ignore

        RDLogger.DisableLog("rdApp.*")
    except Exception:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for m in _SMILES_CAND_RE.finditer(text or ""):
        token = m.group(0).strip(".,;:")
        if len(token) < 5 or " " in token:
            continue
        ring_closure = _has_ring_closure(token)
        if not (_SMILES_HINT_RE.search(token) or ring_closure):
            continue
        if token in seen or any(ch.isdigit() for ch in token[:1]):
            continue
        # Skip things already recognised as formulas (Zn3(PO4)2 etc.), unless
        # a ring-closure digit pair makes SMILES the more plausible reading.
        if not ring_closure and _FORMULA_CAND_RE.fullmatch(token):
            continue
        try:
            mol = Chem.MolFromSmiles(token, sanitize=True)
        except Exception:
            mol = None
        if mol is None or mol.GetNumAtoms() < 2:
            continue
        canonical = Chem.MolToSmiles(mol)
        seen.add(token)
        out.append({"raw": token, "canonical": canonical})
    return out


# ── reaction equations ───────────────────────────────────────────────────────

_ARROW_RE = re.compile(r"→|⟶|-->|->|\\rightarrow|\\longrightarrow|\\to\b")
_REACTION_SIDE_RE = re.compile(r"[A-Za-z0-9()\[\]·.\s+＋_^{}\\-]{2,160}")


def _reaction_species(side: str) -> list[str]:
    """Chemical species on one reaction side: split on '+', strip coefficients,
    require the *whole term* to be a parseable formula (prose terms fail)."""
    from ..domain.chemistry import molar_mass

    found: list[str] = []
    for term in re.split(r"[+＋]", side):
        term = term.strip().strip(".,;:。，；")
        term = re.sub(r"^\d+\s*", "", term)  # stoichiometric coefficient: 3H2SO4
        term = re.sub(r"[_^{}\\$]|\((?:aq|s|l|g)\)", "", term).strip()
        if not term:
            continue
        m = _FORMULA_CAND_RE.fullmatch(term)
        if not m:
            continue
        try:
            if molar_mass(term) <= 0:
                continue
        except Exception:
            continue
        found.append(term)
    return found


def extract_reactions(text: str) -> list[dict]:
    out: list[dict] = []
    for line in (text or "").split("\n"):
        m = _ARROW_RE.search(line)
        if not m:
            continue
        left, right = line[: m.start()], line[m.end():]
        # Keep only the chemistry-looking run adjacent to the arrow (prose on
        # either end of the sentence gets cut at the first non-formula char).
        lm = _REACTION_SIDE_RE.findall(left)
        rm = _REACTION_SIDE_RE.findall(right)
        left_f = _reaction_species(lm[-1] if lm else "")
        right_f = _reaction_species(rm[0] if rm else "")
        if not left_f or not right_f:
            continue
        raw = line.strip()[:200]
        if any(r["raw"] == raw for r in out):
            continue
        out.append({"raw": raw, "reactants": left_f[:6], "products": right_f[:6]})
    return out


# ── commercial products ──────────────────────────────────────────────────────

# Major coating / specialty-chemical suppliers (EN + CN), lowercase keys.
KNOWN_SUPPLIERS: dict[str, str] = {
    "basf": "BASF", "evonik": "Evonik", "byk": "BYK", "altana": "ALTANA",
    "hexion": "Hexion", "olin": "Olin", "dow": "Dow", "dupont": "DuPont",
    "covestro": "Covestro", "bayer": "Bayer", "wacker": "Wacker",
    "momentive": "Momentive", "shin-etsu": "Shin-Etsu", "shinetsu": "Shin-Etsu",
    "clariant": "Clariant", "lubrizol": "Lubrizol", "arkema": "Arkema",
    "allnex": "Allnex", "dsm": "DSM", "eastman": "Eastman",
    "huntsman": "Huntsman", "sika": "Sika", "henkel": "Henkel", "3m": "3M",
    "ppg": "PPG", "akzonobel": "AkzoNobel", "akzo nobel": "AkzoNobel",
    "sherwin-williams": "Sherwin-Williams", "nippon paint": "Nippon Paint",
    "kansai": "Kansai", "jotun": "Jotun", "hempel": "Hempel",
    "cabot": "Cabot", "kronos": "Kronos", "tronox": "Tronox",
    "chemours": "Chemours", "venator": "Venator", "heubach": "Heubach",
    "ferro": "Ferro", "elementis": "Elementis", "ashland": "Ashland",
    "croda": "Croda", "stepan": "Stepan", "solvay": "Solvay",
    "mitsubishi": "Mitsubishi", "toray": "Toray", "kuraray": "Kuraray",
    "sabic": "SABIC", "lanxess": "LANXESS", "umicore": "Umicore",
    "king industries": "King Industries", "borchers": "Borchers",
    "万华": "万华化学", "万华化学": "万华化学", "蓝星": "蓝星集团",
    "巴陵石化": "巴陵石化", "宏昌电子": "宏昌电子", "三木集团": "三木集团",
    "国都化工": "国都化工", "南亚": "南亚塑胶", "德谦": "德谦化学",
    "海名斯": "海名斯", "赢创": "赢创（Evonik）", "毕克": "毕克（BYK）",
    "科思创": "科思创（Covestro）", "陶氏": "陶氏（Dow)", "巴斯夫": "巴斯夫（BASF）",
}

# Well-known coating-industry brand prefixes → accepted without extra context.
KNOWN_BRANDS: tuple[str, ...] = (
    "epon", "epikote", "epotec", "araldite", "der", "den", "der.", "d.e.r",
    "epalloy", "kukdo", "yd", "npel", "npes", "jer", "aerosil", "cab-o-sil",
    "hdk", "byk", "tego", "dynasylan", "silquest", "coatosil", "geniosil",
    "desmodur", "desmophen", "bayhydrol", "bayhydur", "tolonate", "vestanat",
    "ti-pure", "tipure", "kronos", "tiona", "halox", "heucophos", "shieldex",
    "novacite", "nubirox", "pigmentan", "disperbyk", "anti-terra", "efka",
    "tegokat", "borchi", "k-cure", "nacure", "cardolite", "capa", "capcure",
    "ancamine", "ancamide", "aradur", "jeffamine", "versamid", "genamid",
    "sunpoly", "phenalkamine", "beckopox", "duroxyn", "resydrol", "daotan",
    "bentone", "claytone", "garamite", "aquatix", "acrysol", "texanol",
    "dowanol", "optifilm", "eastman", "solsperse", "tamol", "triton",
    "surfynol", "dynol", "envirogem", "carbowet", "zonyl", "capstone",
)

_TM_NAME_RE = re.compile(r"\b([A-Z][A-Za-z]{2,20})\s?[®™]")
_BRAND_GRADE_RE = re.compile(
    r"\b([A-Z][A-Za-z]{1,15}(?:-[A-Za-z]{1,10})?)\s?[®™]?[-–—\s]\s?([A-Z]?-?\d{2,5}(?:[A-Z]{1,3}|[A-Z]?\d{0,2}))\b"
)
# 字母牌号：品牌 + 1-2 大写字母 + 空格 + 数字（如 Desmodur N 3300 → N）
_BRAND_LETTER_GRADE_RE = re.compile(
    r"\b([A-Z][A-Za-z]{1,15})\s+([A-Z]{1,2})\s+\d{2,5}\b"
)
# 单词牌号：品牌 + 首字母大写单词 + 数字（如 Tego Foamex 810 → Foamex）
_BRAND_WORD_GRADE_RE = re.compile(
    r"\b([A-Z][A-Za-z]{1,15})\s+([A-Z][a-z]{2,15})\s+\d{2,5}\b"
)
_GRADE_STOP_PREFIX = {
    "table", "figure", "fig", "example", "examples", "claim", "claims",
    "patent", "section", "chapter", "page", "pages", "iso", "astm", "din",
    "gb", "en", "us", "ep", "cn", "wo", "jp", "kr", "cas", "ref", "refs",
    "eq", "equation", "scheme", "step", "test", "run", "sample", "batch",
    "method", "comparative", "embodiment", "formula", "type", "grade",
    "model", "no", "num", "number", "item", "level", "phase", "week", "day",
    "hour", "min", "year", "version", "issue", "vol", "volume", "part",
}
_SUPPLIER_CONTEXT_RE = re.compile(
    r"available\s+from|supplied\s+by|purchased\s+from|obtained\s+from|"
    r"manufactured\s+by|product\s+of|购自|采购自|购于|厂商|供应商|生产商|出品",
    re.IGNORECASE,
)


def _nearby_supplier(text: str, start: int, end: int, window: int = 90) -> str:
    ctx = text[max(0, start - window): min(len(text), end + window)]
    low = ctx.lower()
    for key, canonical in KNOWN_SUPPLIERS.items():
        if key in low:
            return canonical
    return ""


def extract_products(text: str) -> list[dict]:
    """Trade-name mentions with corroboration; [{trade_name, grade, supplier}]."""
    text = text or ""
    out: list[dict] = []
    seen: set[str] = set()

    def add(trade: str, grade: str, supplier: str) -> None:
        trade = trade.strip().strip("-–—")
        grade = grade.strip()
        if not trade or trade.lower() in _GRADE_STOP_PREFIX:
            return
        key = f"{trade.lower()}|{grade.lower()}"
        if key in seen or len(out) >= _MAX_PRODUCTS_PER_CHUNK:
            return
        seen.add(key)
        out.append({"trade_name": trade, "grade": grade, "supplier": supplier})

    # 1) explicit ™/® marks — accept unconditionally
    for m in _TM_NAME_RE.finditer(text):
        supplier = _nearby_supplier(text, m.start(), m.end())
        # grade may follow the mark: "Aerosil® 200"
        after = text[m.end(): m.end() + 12]
        gm = re.match(r"\s?(\d{2,5}[A-Z]{0,3})\b", after)
        add(m.group(1), gm.group(1) if gm else "", supplier)

    # 2) brand-grade pairs — need corroboration
    for m in _BRAND_GRADE_RE.finditer(text):
        brand, grade = m.group(1), m.group(2)
        if brand.lower() in _GRADE_STOP_PREFIX:
            continue
        known_brand = brand.lower() in KNOWN_BRANDS
        supplier = _nearby_supplier(text, m.start(), m.end())
        has_context = bool(
            supplier
            or _SUPPLIER_CONTEXT_RE.search(
                text[max(0, m.start() - 90): min(len(text), m.end() + 90)]
            )
        )
        if known_brand or has_context:
            add(brand, grade, supplier)

    # 2b) 字母牌号：品牌 + 大写字母 + 数字（如 Desmodur N 3300）
    for m in _BRAND_LETTER_GRADE_RE.finditer(text):
        brand, grade = m.group(1), m.group(2)
        if brand.lower() in _GRADE_STOP_PREFIX:
            continue
        known_brand = brand.lower() in KNOWN_BRANDS
        supplier = _nearby_supplier(text, m.start(), m.end())
        has_context = bool(
            supplier
            or _SUPPLIER_CONTEXT_RE.search(
                text[max(0, m.start() - 90): min(len(text), m.end() + 90)]
            )
        )
        if known_brand or has_context:
            add(brand, grade, supplier)

    # 2c) 单词牌号：品牌 + 单词 + 数字（如 Tego Foamex 810）
    for m in _BRAND_WORD_GRADE_RE.finditer(text):
        brand, grade = m.group(1), m.group(2)
        if brand.lower() in _GRADE_STOP_PREFIX:
            continue
        # 单词牌号必须品牌在白名单（避免误报普通短语）
        if brand.lower() not in KNOWN_BRANDS:
            continue
        supplier = _nearby_supplier(text, m.start(), m.end())
        add(brand, grade, supplier)

    return out


# ── compound labels: coref linking + OCR confusable repair (A3) ─────────────
# OpenChemIE coref + ChemEAGLE guards, as pure rules. Label linking NEVER
# rewrites body text: OCR repair is only tried as a lookup variant when
# matching a candidate token against a KNOWN label map.

_LABEL_RE = re.compile(r"\b\d+[a-zA-Z]{0,2}\b")
# A bare number is a compound label only after one of these words.
_LABEL_CONTEXT_OK = frozenset(
    {"compound", "intermediate", "derivative", "analogue", "analog", "product"}
)
# A number after one of these words is document structure, not a compound.
_PROSE_PRECEDING = frozenset({
    "step", "table", "figure", "fig", "scheme", "page", "section", "chapter",
    "part", "run", "sample", "test", "batch", "method", "claim", "patent",
    "equation", "grade", "model", "version", "no", "number", "example",
    "examples", "ref", "refs", "eq",
})
# OCR digit→letter confusions, tried as lookup variants only.
_OCR_CONFUSABLES = {"1": "l", "0": "o", "5": "s", "9": "g"}
_MAX_LABEL_LINKS = 20


def _preceding_word(text: str, start: int) -> str:
    words = re.findall(r"[A-Za-z]+", text[:start])
    return words[-1].lower() if words else ""


def is_label_like(token: str, *, preceding_word: str = "") -> bool:
    """Is ``token`` shaped like a compound number (``3a`` / ``12b``)?

    Letter-suffixed tokens pass unless preceded by a prose-reference word
    (``Table 3a`` is a table, not a compound). Bare digits pass only after
    an explicit compound keyword (``compound 12``); anything else (years,
    quantities, ``step 4``) is rejected — this is the guard that stops
    ``4`` or ``Product A`` from being parsed as a compound name.
    """
    if not token or not _LABEL_RE.fullmatch(token):
        return False
    prev = (preceding_word or "").lower()
    if prev in _PROSE_PRECEDING:
        return False
    if re.search(r"[a-zA-Z]", token):
        return True
    return prev in _LABEL_CONTEXT_OK


def repair_ocr_confusables(token: str) -> str:
    """Full OCR repair (all confusable digits → letters). Fallback only —
    prefer _repair_candidates, whose single-position variants preserve real
    digits (``129`` → ``12g`` for an OCR'd ``12g``; full repair would give
    the wrong ``l2g``)."""
    return "".join(_OCR_CONFUSABLES.get(ch, ch) for ch in token)


def _repair_candidates(token: str) -> list[str]:
    """Lookup variants in order: exact, one single-position repair each,
    then full repair. Deduplicated, order-preserving."""
    candidates = [token]
    for i, ch in enumerate(token):
        if ch in _OCR_CONFUSABLES:
            candidates.append(token[:i] + _OCR_CONFUSABLES[ch] + token[i + 1:])
    full = repair_ocr_confusables(token)
    if full != token:
        candidates.append(full)
    seen: set[str] = set()
    out: list[str] = []
    for cand in candidates:
        if cand not in seen:
            seen.add(cand)
            out.append(cand)
    return out


def link_labels(text: str, label_to_smiles: dict) -> list[dict]:
    """Link compound-number mentions in ``text`` to identified structures.

    Each hit is ``{label (as written), canonical_label (map key), smiles,
    start, end}``. A hit requires the (possibly OCR-repaired) candidate to
    be label-like AND present in the map — repair alone never creates a
    link to an unknown label. Runs after the CAS / formula / SMILES
    validations: it resolves mentions, it does not validate structures.
    """
    if not text or not label_to_smiles:
        return []
    out: list[dict] = []
    for m in _LABEL_RE.finditer(text):
        prev = _preceding_word(text, m.start())
        for cand in _repair_candidates(m.group(0)):
            if cand in label_to_smiles and is_label_like(cand, preceding_word=prev):
                out.append({
                    "label": m.group(0),
                    "canonical_label": cand,
                    "smiles": label_to_smiles[cand],
                    "start": m.start(),
                    "end": m.end(),
                })
                break
        if len(out) >= _MAX_LABEL_LINKS:
            break
    return out


def resolve_side_label(label: str, label_to_smiles: dict) -> str | None:
    """Resolve one reaction-scheme side label to SMILES (or None).

    Scheme context: the whole label may itself be a compound number
    (``12``), or contain one (``compound 3a``). Prose-like labels
    (``Product A``, ``step 4`` as a whole label) never resolve. The caller
    still runs the result through the RDKit gate — resolution never
    bypasses verification.
    """
    if not label or not label_to_smiles:
        return None
    text = label.strip()
    tokens: list[str] = []
    if _LABEL_RE.fullmatch(text):
        tokens.append(text)
    for m in _LABEL_RE.finditer(text):
        if m.group(0) not in tokens:
            tokens.append(m.group(0))
    for token in tokens:
        # Bare digits resolve only when the whole label IS the number.
        if not re.search(r"[a-zA-Z]", token) and text != token:
            continue
        for cand in _repair_candidates(token):
            if cand in label_to_smiles and _LABEL_RE.fullmatch(cand):
                return label_to_smiles[cand]
    return None


# ── aggregate entry point ────────────────────────────────────────────────────


def extract_entities(text: str, *, label_to_smiles: dict | None = None) -> dict | None:
    """All entity families for one chunk → compact meta dict (None if empty)."""
    try:
        chem: list[dict] = []
        for cas in extract_cas(text)[:_MAX_CHEM_PER_CHUNK]:
            chem.append({"type": "cas", "value": cas})
        for formula in extract_formulas(text)[:_MAX_CHEM_PER_CHUNK]:
            chem.append({"type": "formula", "value": formula})
        for smi in extract_smiles(text)[:_MAX_CHEM_PER_CHUNK]:
            chem.append({"type": "smiles", "value": smi["canonical"], "raw": smi["raw"]})
        for rxn in extract_reactions(text)[:5]:
            chem.append(
                {
                    "type": "reaction",
                    "value": rxn["raw"],
                    "reactants": rxn["reactants"],
                    "products": rxn["products"],
                }
            )
        products = extract_products(text)
        meta: dict = {}
        if chem:
            meta["chem"] = chem[: _MAX_CHEM_PER_CHUNK + 5]
        if products:
            meta["products"] = products
        # A3: label linking is opt-in (None → previous behaviour) and runs
        # after the validated families above.
        if label_to_smiles:
            links = link_labels(text, label_to_smiles)
            if links:
                meta["label_links"] = links
        return meta or None
    except Exception as exc:
        return degrade_return(logger, exc, "chem_extract failed", None)

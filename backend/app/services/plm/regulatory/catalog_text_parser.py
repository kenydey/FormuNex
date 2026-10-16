"""Parse the free-text hazard column of the 《危险化学品目录》 into structured
GB 30000 classifications.

The MSDS-Generator copy of the catalogue writes each entry's classification as
loosely formatted Chinese text — 119 different spellings of hazard classes,
several classes run together without separators, line breaks inside a class
name, an asterisk marking a *minimum* category, the STOT SE 3 effect on its own
line, and conditional classifications that depend on the form of the substance:

    急性毒性-经口,类别 3*急性毒性-吸入,类别 3*致癌性,类别 1A
    特异性靶器官毒性-一次接触,类别 3\\n（呼吸道刺激）
    （1）闪点＜23℃和初沸点≤35℃：易燃液体,类别 1\\n（2）...

The parser never guesses: whatever it cannot attribute to a known class and
category is returned in ``unparsed`` and ``review_reasons`` so the import puts
the entry in front of a human.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .hazard_classes import HAZARD_CLASSES, LEGACY_LABELS, STOT_SE3_EFFECTS, h_code_for

_FULLWIDTH = str.maketrans("０１２３４５６７８９，：；（）［］＊", "0123456789,:;()[]*")

_NAME_TO_CODE: dict[str, str] = {name: hc.code for hc in HAZARD_CLASSES for name in hc.names}
_NAMES_ALT = "|".join(re.escape(n) for n in sorted(_NAME_TO_CODE, key=len, reverse=True))

_CLASS_RE = re.compile(
    rf"(?P<name>{_NAMES_ALT}),?"
    r"(?:类别(?P<cat>\d(?:[A-C])?)|(?P<type>[A-G])型|(?P<div>1\.[1-6])项|(?P<unstable>不稳定爆炸物)"
    r"|(?P<lact>附加类别))?"
    r"(?P<star>\*)?"
)
_EFFECT_RE = re.compile(r"^\((?P<effects>(?:呼吸道刺激|麻醉效应)(?:、(?:呼吸道刺激|麻醉效应))?)\)")
# "(1)" condition markers. Accept "(1)", "(1）" etc. after fullwidth folding.
_COND_RE = re.compile(r"\((\d)\)")
_LEGACY_RE = re.compile("|".join(re.escape(x) for x in LEGACY_LABELS))
_NOISE_RE = re.compile(r"[\s,;:、.*()\[\]\d%≤≥<>＜＞=℃A-Ga-g型项类别和]+")


@dataclass
class ParsedHazard:
    hazard_class: str
    category: str
    h_code: str | None
    is_minimum: bool = False
    qualifier: str = ""
    condition: str = ""


@dataclass
class ParseResult:
    items: list[ParsedHazard] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)
    conditional: bool = False
    review_reasons: list[str] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return bool(self.review_reasons)


def _normalise(text: str) -> str:
    text = (text or "").translate(_FULLWIDTH)
    text = text.replace("－", "-").replace("—", "-")
    return re.sub(r"\s+", "", text)


def _segments(text: str) -> list[tuple[str, str]]:
    """Split on (1)/(2)... markers → [(condition, body)]. Unconditional text has condition ''."""
    parts = _COND_RE.split(text)
    if len(parts) == 1:
        return [("", text)]
    out: list[tuple[str, str]] = []
    if parts[0]:
        out.append(("", parts[0]))
    for i in range(1, len(parts), 2):
        seg = parts[i + 1] if i + 1 < len(parts) else ""
        # The condition is whatever precedes the first class name or the first colon.
        m = _CLASS_RE.search(seg)
        colon = seg.find(":")
        if colon != -1 and (m is None or colon < m.start()):
            cond, body = seg[:colon], seg[colon + 1:]
        elif m is not None:
            cond, body = seg[: m.start()], seg[m.start():]
        else:
            cond, body = seg, ""
        out.append((cond.strip(",;") or f"({parts[i]})", body))
    return out


def parse_hazard_text(text: str) -> ParseResult:
    result = ParseResult()
    norm = _normalise(text)
    if not norm:
        result.review_reasons.append("empty")
        return result
    segments = _segments(norm)
    result.conditional = any(cond for cond, _ in segments)
    seen: set[tuple[str, str, str]] = set()

    for condition, body in segments:
        consumed: list[tuple[int, int]] = []
        for m in _CLASS_RE.finditer(body):
            code = _NAME_TO_CODE[m.group("name")]
            hc = next(h for h in HAZARD_CLASSES if h.code == code)
            end = m.end()
            if m.group("cat"):
                category = m.group("cat")
            elif m.group("type"):
                category = m.group("type")
            elif m.group("div"):
                category = m.group("div")
            elif m.group("unstable"):
                category = "unstable"
            elif m.group("lact"):
                category = "lact"
            else:
                category = hc.default_category or ""
            qualifier = ""
            h_code = h_code_for(code, category) if category else None
            if code == "stot_se" and category == "3":
                em = _EFFECT_RE.match(body[end:])
                if em:
                    effects = em.group("effects").split("、")
                    qualifier = "（" + "、".join(effects) + "）"
                    h_code = ",".join(STOT_SE3_EFFECTS[e] for e in effects)
                    end += em.end()
                else:
                    result.review_reasons.append("stot_se3_effect_missing")
            consumed.append((m.start(), end))
            if not category:
                result.review_reasons.append(f"missing_category:{code}")
                continue
            if category not in hc.h_codes:
                result.review_reasons.append(f"unknown_category:{code}:{category}")
                continue
            key = (code, category, condition)
            if key in seen:
                continue
            seen.add(key)
            result.items.append(
                ParsedHazard(
                    hazard_class=code,
                    category=category,
                    h_code=h_code,
                    is_minimum=bool(m.group("star")),
                    qualifier=qualifier,
                    condition=condition,
                )
            )
        # Everything not consumed by a class match is leftover text.
        leftover, pos = [], 0
        for s, e in consumed:
            leftover.append(body[pos:s])
            pos = e
        leftover.append(body[pos:])
        for frag in leftover:
            # A dangling STOT SE 3 effect belongs to a class consumed earlier; drop it.
            frag = _EFFECT_RE.sub("", frag) if frag.startswith("(") else frag
            frag = re.sub(r"\((?:呼吸道刺激|麻醉效应)(?:、(?:呼吸道刺激|麻醉效应))?\)", "", frag)
            for legacy in _LEGACY_RE.findall(frag):
                result.review_reasons.append(f"legacy_label:{legacy}")
            if _NOISE_RE.sub("", frag):
                result.unparsed.append(frag)

    if result.conditional:
        result.review_reasons.append("conditional")
    if result.unparsed:
        result.review_reasons.append("unparsed_text")
    if not result.items and "empty" not in result.review_reasons:
        result.review_reasons.append("no_classification")
    # Stable, de-duplicated reasons.
    result.review_reasons = list(dict.fromkeys(result.review_reasons))
    return result

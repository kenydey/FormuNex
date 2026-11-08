"""Three-library C1: figure role classification (pure caption rules).

Caption first (a figure's own caption is authoritative for its role),
citing sentences only when the caption is uninformative. Roles:
reaction_scheme / spectra / data_plot / other. spectra and data_plot
are SKIPPED for extraction by callers, but their drawn label structures
are still collected into the label registry — skipping extraction is
not skipping the figure.

The LLM batch classification (batch=25) is deliberately NOT ported:
it needs a live model call, and the rule layer below already separates
the skip cases. A caller may add an LLM pass on top of "other".
"""
from __future__ import annotations

import re

ROLE_REACTION_SCHEME = "reaction_scheme"
ROLE_SPECTRA = "spectra"
ROLE_DATA_PLOT = "data_plot"
ROLE_OTHER = "other"

SKIP_ROLES = frozenset({ROLE_SPECTRA, ROLE_DATA_PLOT})

_SPECTRA_WORDS = (
    "spectrum", "spectra", "spectral", "nmr", "ir spectrum", "mass spectrum",
    "ms spectrum", "chromatogram", "hplc", "gc-ms", "lc-ms", "xrd", "x-ray",
    "uv-vis", "raman", "epr", "esr",
)
_PLOT_WORDS = (
    "plot", "curve", "profile", "dependence", "versus", "vs.", "against",
    "kinetics", "isotherm", "voltammogram", "cv curve", "polarization",
)
_SCHEME_WORDS = (
    "scheme", "reaction", "synthesis", "mechanism", "transformation",
    "route", "preparation",
)


def _has_word(text: str, words: tuple[str, ...]) -> bool:
    """Whole-word (or whole-phrase) match, not substring.

    P1-B17: plain ``in`` matched "curve" inside "curved" and let
    generic plot words ("against") steal reaction schemes. The
    lookarounds (instead of ``\\b``) also handle words ending in
    punctuation ("vs.").
    """
    lowered = text.lower()
    return any(
        re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", lowered) for w in words
    )


def _classify_one(text: str) -> str | None:
    """One text's role, or None when no vocabulary matches.

    P1-B17: explicit scheme vocabulary wins over generic plot/spectra
    words — "Scheme 1: synthesis route, 1H NMR data shown" is a scheme,
    not a spectrum, and "activity against bacteria" under a Scheme
    caption is not a data plot.
    """
    if _has_word(text, _SCHEME_WORDS):
        return ROLE_REACTION_SCHEME
    if _has_word(text, _SPECTRA_WORDS):
        return ROLE_SPECTRA
    if _has_word(text, _PLOT_WORDS):
        return ROLE_DATA_PLOT
    return None


def classify_figure(caption: str = "", citing_sentences: list[str] | None = None) -> str:
    """Caption rules, then citing-sentence rules, then "other"."""
    role = _classify_one(caption or "")
    if role is not None:
        return role
    for sentence in citing_sentences or []:
        role = _classify_one(sentence)
        if role is not None:
            return role
    return ROLE_OTHER


def skip_extraction(role: str) -> bool:
    """Whether callers skip extraction for this role (spectra / plots)."""
    return role in SKIP_ROLES

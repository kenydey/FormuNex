"""Image → structured JSON via the configured vision LLM (multimodal RAG).

The "降维打击"路线: instead of maintaining a local OCR stack (PaddleOCR /
GOT-OCR / Surya), uploaded images go to the *already configured* LLM provider
with a vision-capable model.  The model returns structured JSON — tables as
Markdown, molecule structure drawings as SMILES — and every SMILES claim is
verified locally with RDKit (parse → canonicalize); unverifiable claims are
kept but flagged, never silently trusted.

Providers: every OpenAI-compatible vendor (openai / deepseek / qwen / moonshot
/ xai / groq / custom base_url) via image content parts, plus Anthropic via
base64 image blocks.  Degrades to ``(None, hint)`` without a key / vision
model — ingestion then stores a placeholder instead of failing.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ..config import get_settings, source_checkout_mode
from ..domain.multimodal_schemas import VisionTableExtraction
from .chem_extract import resolve_side_label
from .errors import degrade_return
from .rgroup import apply_rgroup_to_reaction
from .kg.prompts import build_multimodal_table_prompt
from .llm_roles import CUSTOM_PROVIDER, VISION, RoleConfig, is_warming, resolve_role
from .runtime_secrets import effective_setting

logger = logging.getLogger(__name__)

_MIME_BY_EXT = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "bmp": "image/bmp",
}

IMAGE_EXTS = frozenset(_MIME_BY_EXT)

_VISION_SYSTEM = """你是化学文献图片结构化专家。分析图片并只输出一个 JSON 对象（无 markdown 围栏），字段：
- kind: "table" | "structure" | "flowchart" | "equation" | "document" | "other"
- markdown: 图片内容的完整 Markdown 表示——表格用 | 语法逐格转录；流程图用有序列表；
  文档截图逐段转录；反应方程式用 LaTeX（$$…$$）
- molecules: 图中出现的分子结构，[{"smiles": "...", "name": "…", "confidence": 0-1}]；
  没有分子结构图则为空数组；不确定的结构给低 confidence，绝不编造
- notes: 无法转录的内容说明（如模糊区域）
数值、化学式必须忠实转录，绝不允许捏造。"""


class VisionMolecule(BaseModel):
    smiles: str = ""
    name: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    verified: bool = False  # RDKit-parsed + canonicalized


class VisionExtraction(BaseModel):
    kind: Literal["table", "structure", "flowchart", "equation", "document", "other"] = "other"
    markdown: str = ""
    molecules: list[VisionMolecule] = Field(default_factory=list)
    notes: str = ""


class VisionReactionSide(BaseModel):
    """One molecule on one side of a reaction scheme (borrowed shape:
    ChemEAGLE/OpenChemIE reactants/conditions/products JSON)."""

    smiles: str = ""
    label: str = ""
    verified: bool = False  # RDKit-parsed + canonicalized (see _verify_reaction)


class VisionReactionCondition(BaseModel):
    role: str = ""  # reagent / catalyst / solvent / temperature / time / yield / other
    text: str = ""


class VisionReaction(BaseModel):
    reactants: list[VisionReactionSide] = Field(default_factory=list)
    conditions: list[VisionReactionCondition] = Field(default_factory=list)
    products: list[VisionReactionSide] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reaction_smiles: str = ""  # assembled locally from VERIFIED sides only, never from the model
    # A2 deterministic confidence stratification (additive; never raises the
    # gated `confidence` above). None until apply_confidence_features runs.
    confidence_score: float | None = None
    confidence_level: str | None = None  # high / medium / low
    confidence_flags: list[str] = Field(default_factory=list)
    confidence_features: dict = Field(default_factory=dict)
    confidence_checker_note: str | None = None
    # A4: optimisation-table entry id; outcome sentinels (never silently
    # dropped: a no-reaction / other entry stays in the output, distinct
    # from "nothing extracted"); per-reaction notes + applied footnote keys.
    entry: str = ""
    no_reaction: bool = False
    other_entry: bool = False
    notes: str = ""
    applied_footnotes: list[str] = Field(default_factory=list)


class VisionFootnote(BaseModel):
    """One table/footnote entry. ``entry`` scopes it: empty means the note
    is document-level; a non-empty value means it belongs ONLY to the
    reaction with that entry id (never applied globally)."""

    key: str = ""
    text: str = ""
    entry: str = ""


class VisionReactionExtraction(BaseModel):
    reactions: list[VisionReaction] = Field(default_factory=list)
    notes: str = ""
    footnotes: list[VisionFootnote] = Field(default_factory=list)
    confidence_summary: dict = Field(default_factory=dict)


# Reaction prompt lives in a text file (MERMaid borrowing: prompts as plain
# text, editable without touching code); the inline copy is only a fallback
# so a missing file degrades to the same prompt instead of failing.
_REACTION_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "resources" / "prompts" / "vision_reaction.txt"
)
_REACTION_PROMPT_FALLBACK = """你是化学反应图结构化专家。分析图片中的化学反应方案图，只输出一个 JSON 对象（无 markdown 围栏），字段：
- reactions: [{entry, reactants: [{smiles, label}], conditions: [{role, text}], products: [{smiles, label}], confidence: 0-1, no_reaction, other_entry, notes}]
- footnotes: [{key, text, entry}]（表注/脚注原文；entry 为该注所属的条目号，通用注留空）
- notes: 无法识别的内容说明
规则：
- role 只能是 reagent / catalyst / solvent / temperature / time / yield / other；
- 每个分子只给 SMILES，不确定的结构给低 confidence，绝不编造；无法识别的分子用空 smiles 并在 label 里写原文；
- 反应物、产物为空数组时表示图中没有对应方；conditions 为空数组时表示图中没有标出条件；
- 表注处理：条件文字里引用表注时用 [key] 形式标注（如 [a]），并在 footnotes 里给出 key 与原文；缩写定义（如 SiT2 = Si(CH3)2）写进 footnotes，不要直接展开到条件里；
- 防丢规则：无反应的条目不得丢弃，输出 reactants/products 为空、no_reaction=true 的条目，并在 notes 里写 N.R.（只用 N.R. 这一个哨兵，不要写 NR）；“其他”类条目输出 other_entry=true 的条目；
- 只输出 JSON，不要解释。"""


def _reaction_prompt() -> str:
    try:
        text = _REACTION_PROMPT_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    return text or _REACTION_PROMPT_FALLBACK


def _verify_reaction(reaction: VisionReaction) -> VisionReaction:
    """RDKit gate + reaction-SMILES assembly for one VLM reaction claim.

    Mirrors _verify_molecules: a side whose SMILES fails RDKit is kept as a
    claim (label preserved) but marked unverified, excluded from the assembled
    reaction SMILES, and caps the reaction confidence at 0.3. Without RDKit
    installed every side stays unverified and no reaction SMILES is assembled
    — an unverified claim must never look like a verified one.
    """
    try:
        from rdkit import Chem, RDLogger  # type: ignore

        RDLogger.DisableLog("rdApp.*")
        rdkit_ok = True
    except Exception:
        rdkit_ok = False

    def _verify_side(side: VisionReactionSide) -> VisionReactionSide:
        smi = (side.smiles or "").strip()
        if not smi:
            return side
        if rdkit_ok:
            try:
                parsed = Chem.MolFromSmiles(smi)
            except Exception:
                parsed = None
            if parsed is not None:
                side.smiles = Chem.MolToSmiles(parsed)
                side.verified = True
                return side
        return side

    reaction.reactants = [_verify_side(s) for s in reaction.reactants]
    reaction.products = [_verify_side(s) for s in reaction.products]
    if any(not s.verified for s in reaction.reactants + reaction.products if s.smiles):
        reaction.confidence = min(reaction.confidence, 0.3)
    left = " + ".join(s.smiles for s in reaction.reactants if s.verified)
    right = " + ".join(s.smiles for s in reaction.products if s.verified)
    reaction.reaction_smiles = f"{left}>>{right}" if (left or right) else ""
    return reaction


# ── A2: deterministic confidence features (ChemEAGLE §4, LLM checker excluded)
# ─────────────────────────────────────────────────────────────────────────────
# Pure, model-free stratification of a VLM reaction claim. Runs AFTER the
# _verify_reaction RDKit gate: the 0.3 cap is a hard gate and the feature
# score is capped at 0.3 whenever the gate fired — features never raise a
# gated reaction back up. Score weights below are an initial, documented
# heuristic (not a calibrated model): ChemEAGLE's joblib calibrations are
# explicitly NOT used as production thresholds (plan A2); recalibrate on a
# FormuNex eval set before treating the score as calibrated.
_METAL_SYMBOLS = frozenset({
    "Li", "Be", "Na", "Mg", "Al", "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn",
    "Fe", "Co", "Ni", "Cu", "Zn", "Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc",
    "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn", "Cs", "Ba", "La", "Hf", "Ta",
    "W", "Re", "Os", "Ir", "Pt", "Au", "Hg", "Tl", "Pb", "Bi",
})
_HALOGEN_SYMBOLS = frozenset({"F", "Cl", "Br", "I"})
_YIELD_RE = re.compile(r"\d\s*%")
_SELECTIVE_RE = re.compile(r"\b(ee|dr|er|cis|trans|syn|anti)\b", re.IGNORECASE)

# LLM checker questionnaire (text only; the live call is NOT implemented —
# the switch stays off. answers map: yes / no / unsure per key).
CHECKER_QUESTIONS = [
    {"key": "drawn", "question": "Is the reaction scheme drawn completely?"},
    {"key": "complete", "question": "Are all reactants and products present?"},
    {"key": "structures", "question": "Are all structures drawn correctly?"},
    {"key": "conditions", "question": "Are the reaction conditions complete?"},
    {"key": "resolved", "question": "Are all labels resolved to structures?"},
    {"key": "duplicate", "question": "Are there duplicate products?"},
    {"key": "stereo_drawn", "question": "Is stereochemistry drawn where expected?"},
    {"key": "counterion_missing", "question": "Is a counterion missing?"},
]
_CHECKER_PENALTIES = {
    ("drawn", "no"): ("checker_drawn_no", 0.10),
    ("complete", "no"): ("checker_complete_no", 0.10),
    ("structures", "no"): ("checker_structures_no", 0.10),
    ("conditions", "no"): ("checker_conditions_no", 0.05),
    ("resolved", "no"): ("checker_resolved_no", 0.05),
    ("duplicate", "yes"): ("checker_duplicate_yes", 0.10),
    ("stereo_drawn", "no"): ("checker_stereo_no", 0.05),
    ("counterion_missing", "yes"): ("checker_counterion_missing", 0.05),
}


def reaction_count_features(reaction: VisionReaction) -> dict:
    """Counts + text features. No RDKit, no model calls."""
    sides = list(reaction.reactants) + list(reaction.products)
    with_smiles = [s for s in sides if (s.smiles or "").strip()]
    condition_text = " ".join(c.text or "" for c in reaction.conditions)
    return {
        "n_reactants": len(reaction.reactants),
        "n_products": len(reaction.products),
        "n_conditions": len(reaction.conditions),
        "n_missing_smiles": sum(1 for s in sides if not (s.smiles or "").strip()),
        "n_star_sides": sum(1 for s in with_smiles if "*" in s.smiles),
        "n_sides_with_smiles": len(with_smiles),
        "yield_mentioned": bool(
            any(c.role == "yield" for c in reaction.conditions)
            or _YIELD_RE.search(condition_text)
        ),
        "selective_text_mentioned": bool(_SELECTIVE_RE.search(condition_text)),
    }


def _mol_atoms(mol):
    try:
        return list(mol.GetAtoms())
    except Exception:
        return []


def _mol_charge(mol) -> int:
    try:
        return int(mol.GetFormalCharge())
    except Exception:
        return 0


def _mol_heavy(mol) -> int:
    try:
        return int(mol.GetNumHeavyAtoms())
    except Exception:
        return sum(1 for a in _mol_atoms(mol) if a.GetSymbol() != "H")


def reaction_mol_features(reaction: VisionReaction, chem) -> dict:
    """RDKit-gated per-molecule features. ``chem`` is None when RDKit is
    unavailable — the caller then flags rdkit_unavailable and scores from
    counts only. ``chem`` needs MolFromSmiles + FindMolChiralCenters."""
    sides = list(reaction.reactants) + list(reaction.products)
    feats: dict = {
        "n_invalid_smiles": 0,
        "n_multi_fragment_sides": 0,
        "n_nonzero_charge_sides": 0,
        "charged_main_chain": False,
        "n_metal_product_sides": 0,
        "duplicate_products": False,
        "product_reactant_overlap": False,
        "reactant_heavy_atoms": 0,
        "product_heavy_atoms": 0,
        "product_rings": 0,
        "product_heteroatoms": 0,
        "heavy_atom_balance": 0,
        "n_assigned_chiral": 0,
        "n_unassigned_chiral": 0,
        "n_small_free_fragments": 0,
        "cation_adjacent_neutral_halogen": False,
    }
    if chem is None:
        return feats
    parsed: dict[str, object] = {}

    def _parse(smi: str):
        if smi not in parsed:
            try:
                parsed[smi] = chem.MolFromSmiles(smi)
            except Exception:
                parsed[smi] = None
        return parsed[smi]

    def _side_mol_features(side, is_product: bool):
        smi = (side.smiles or "").strip()
        if not smi:
            return
        mol = _parse(smi)
        if mol is None:
            feats["n_invalid_smiles"] += 1
            return
        feats["reactant_heavy_atoms" if not is_product else "product_heavy_atoms"] += _mol_heavy(mol)
        if _mol_charge(mol) != 0:
            feats["n_nonzero_charge_sides"] += 1
        fragments = [f for f in smi.split(".") if f]
        if len(fragments) > 1:
            feats["n_multi_fragment_sides"] += 1
        frag_mols = [(f, _parse(f)) for f in fragments]
        frag_mols = [(f, m) for f, m in frag_mols if m is not None]
        if frag_mols:
            largest_idx = max(range(len(frag_mols)), key=lambda i: _mol_heavy(frag_mols[i][1]))
            if _mol_charge(frag_mols[largest_idx][1]) != 0:
                feats["charged_main_chain"] = True
            for i, (f, m) in enumerate(frag_mols):
                if i == largest_idx:
                    continue
                carbons = sum(1 for a in _mol_atoms(m) if a.GetSymbol() == "C")
                if carbons <= 3 or f.strip() == "*":
                    feats["n_small_free_fragments"] += 1
        atoms = _mol_atoms(mol)
        symbols = [a.GetSymbol() for a in atoms]
        if is_product:
            try:
                feats["product_rings"] += int(mol.GetRingInfo().NumRings())
            except Exception:
                pass
            feats["product_heteroatoms"] += sum(1 for s in symbols if s not in ("C", "H"))
            if any(s in _METAL_SYMBOLS for s in symbols):
                feats["n_metal_product_sides"] += 1
            for a in atoms:
                try:
                    if a.GetFormalCharge() > 0:
                        for nb in a.GetNeighbors():
                            if nb.GetSymbol() in _HALOGEN_SYMBOLS and nb.GetFormalCharge() == 0:
                                feats["cation_adjacent_neutral_halogen"] = True
                except Exception:
                    continue
        try:
            centers = chem.FindMolChiralCenters(mol, includeUnassigned=True)
        except Exception:
            centers = []
        for _, kind in centers or []:
            if kind == "?":
                feats["n_unassigned_chiral"] += 1
            else:
                feats["n_assigned_chiral"] += 1

    for side in reaction.reactants:
        _side_mol_features(side, is_product=False)
    for side in reaction.products:
        _side_mol_features(side, is_product=True)
    reactant_set = {(s.smiles or "").strip() for s in reaction.reactants if (s.smiles or "").strip()}
    product_list = [(s.smiles or "").strip() for s in reaction.products if (s.smiles or "").strip()]
    feats["duplicate_products"] = len(product_list) != len(set(product_list))
    feats["product_reactant_overlap"] = bool(reactant_set & set(product_list))
    feats["heavy_atom_balance"] = feats["product_heavy_atoms"] - feats["reactant_heavy_atoms"]
    return feats


def _level_for(score: float) -> str:
    if score >= 0.85:
        return "high"
    if score >= 0.5:
        return "medium"
    return "low"


def apply_checker_answers(score: float, flags: list[str], answers: dict | None):
    """Convert checker answers (yes/no/unsure per question key) into score
    adjustments + flags. ``None`` means the checker is off: no change.
    A checker failure must degrade to the deterministic score — callers
    achieve that by passing None (or catching and passing None)."""
    if not answers:
        return round(max(0.0, min(1.0, score)), 3), list(flags), None
    notes: list[str] = []
    for (key, value), (flag, penalty) in _CHECKER_PENALTIES.items():
        if answers.get(key) == value:
            flags.append(flag)
            score -= penalty
            notes.append(f"checker: {key}={value}")
    score = round(max(0.0, min(1.0, score)), 3)
    return score, flags, ("; ".join(notes) if notes else None)


def compute_reaction_confidence(
    reaction: VisionReaction,
    *,
    chem=None,
    reference_smiles: set[str] | None = None,
    checker_answers: dict | None = None,
) -> dict:
    """Deterministic confidence stratification for one verified reaction.

    ``chem``: RDKit Chem module or None (features degrade to counts only).
    ``reference_smiles``: SMILES from an INDEPENDENT path (e.g. MolScribe
    structures for the same figure). Agreement with an independent path is a
    bonus here — this deliberately differs from ChemEAGLE's frac_copied
    penalty, which measures copying WITHIN one pipeline (no independent
    evidence). With no reference the agreement feature is absent, not zero.
    """
    counts = reaction_count_features(reaction)
    sides = list(reaction.reactants) + list(reaction.products)
    gate_fired = any((s.smiles or "").strip() and not s.verified for s in sides)
    mol_feats = reaction_mol_features(reaction, chem)
    features = {**counts, **mol_feats}
    flags: list[str] = []
    if chem is None:
        flags.append("rdkit_unavailable")

    score = 1.0
    if not sides:
        flags.append("empty_reaction")
        score = 0.0
    else:
        if counts["n_missing_smiles"]:
            flags.append("missing_smiles")
            score -= 0.10 * min(counts["n_missing_smiles"], 3)
        if mol_feats["n_invalid_smiles"]:
            flags.append("invalid_smiles")
            score -= 0.20 * min(mol_feats["n_invalid_smiles"], 2)
        if counts["n_star_sides"]:
            flags.append("star_sides")
            score -= 0.10 * min(counts["n_star_sides"], 2)
        if mol_feats["n_multi_fragment_sides"]:
            flags.append("multi_fragment_sides")
            score -= 0.05 * min(mol_feats["n_multi_fragment_sides"], 3)
        if mol_feats["n_nonzero_charge_sides"]:
            flags.append("nonzero_charge_sides")
            score -= 0.10
        if mol_feats["charged_main_chain"]:
            flags.append("charged_main_chain")
            score -= 0.10
        if mol_feats["n_metal_product_sides"]:
            flags.append("metal_product_sides")
            score -= 0.05 * min(mol_feats["n_metal_product_sides"], 2)
        if mol_feats["duplicate_products"]:
            flags.append("duplicate_products")
            score -= 0.15
        if mol_feats["product_reactant_overlap"]:
            flags.append("product_reactant_overlap")
            score -= 0.10
        # heavy_atom_balance is recorded as a feature only: substitutions
        # legitimately raise product heavy counts (Cl replaces H), so its
        # sign alone is not a quality signal without stoichiometry.
        if mol_feats["n_unassigned_chiral"]:
            flags.append("unassigned_chiral")
            score -= 0.05 * min(mol_feats["n_unassigned_chiral"], 2)
        if mol_feats["n_small_free_fragments"]:
            flags.append("small_free_fragments")
            score -= 0.05 * min(mol_feats["n_small_free_fragments"], 2)
        if mol_feats["cation_adjacent_neutral_halogen"]:
            flags.append("cation_adjacent_neutral_halogen")
            score -= 0.05
        if counts["yield_mentioned"]:
            flags.append("yield_mentioned")
            score += 0.05
        if counts["selective_text_mentioned"]:
            flags.append("selective_text_mentioned")
        if counts["n_sides_with_smiles"] == 0 and sides:
            flags.append("no_structures")
            score = min(score, 0.3)
        if reference_smiles is not None:
            with_smiles = [(s.smiles or "").strip() for s in sides if (s.smiles or "").strip()]
            if with_smiles:
                matched = sum(1 for smi in with_smiles if smi in reference_smiles)
                frac = matched / len(with_smiles)
                features["reference_agreement"] = round(frac, 3)
                products = [(s.smiles or "").strip() for s in reaction.products if (s.smiles or "").strip()]
                features["reference_all_products_matched"] = bool(products) and all(
                    smi in reference_smiles for smi in products
                )
                features["reference_all_matched"] = matched == len(with_smiles)
                if frac >= 0.99:
                    flags.append("reference_agreement_full")
                    score += 0.10
                elif frac >= 0.5:
                    flags.append("reference_agreement_partial")
                    score += 0.05
                else:
                    flags.append("reference_agreement_low")
                    score -= 0.05
        if gate_fired:
            flags.append("gate_capped_0.3")
            score = min(score, 0.3)
    score = round(max(0.0, min(1.0, score)), 3)
    score, flags, note = apply_checker_answers(score, flags, checker_answers)
    return {
        "score": score,
        "level": _level_for(score),
        "flags": flags,
        "features": features,
        "checker_note": note,
    }


def _resolve_chem():
    try:
        from rdkit import Chem  # type: ignore

        return Chem
    except Exception:
        return None


def apply_confidence_features(
    reaction: VisionReaction,
    *,
    reference_smiles: set[str] | None = None,
    checker_answers: dict | None = None,
) -> VisionReaction:
    """Attach A2 stratification fields to a reaction AFTER _verify_reaction.

    Never raises: on any internal error the fields stay empty and the gated
    confidence is untouched (fail-open; the gate is the source of truth).
    """
    # A4: a no-reaction / other entry is a valid negative result, not a
    # failed extraction — it is not scored, only flagged.
    if reaction.no_reaction or reaction.other_entry:
        reaction.confidence_score = None
        reaction.confidence_level = None
        reaction.confidence_flags = [
            "no_reaction_entry" if reaction.no_reaction else "other_entry"
        ]
        reaction.confidence_features = {}
        reaction.confidence_checker_note = None
        return reaction
    try:
        detail = compute_reaction_confidence(
            reaction,
            chem=_resolve_chem(),
            reference_smiles=reference_smiles,
            checker_answers=checker_answers,
        )
        reaction.confidence_score = detail["score"]
        reaction.confidence_level = detail["level"]
        reaction.confidence_flags = detail["flags"]
        reaction.confidence_features = detail["features"]
        reaction.confidence_checker_note = detail["checker_note"]
    except Exception:
        pass
    return reaction


def build_confidence_summary(reactions: list[VisionReaction]) -> dict:
    """Per-extraction summary: level counts, low-score indices, warnings.
    (Plan's template list is omitted: VisionReaction has no template.)"""
    levels = [r.confidence_level for r in reactions if r.confidence_level]
    warnings: list[str] = []
    if any("rdkit_unavailable" in (r.confidence_flags or []) for r in reactions):
        warnings.append("rdkit_unavailable: some reactions scored without mol features")
    if any("gate_capped_0.3" in (r.confidence_flags or []) for r in reactions):
        warnings.append("gate_capped_0.3: some reactions capped by the RDKit gate")
    return {
        "n_high": levels.count("high"),
        "n_medium": levels.count("medium"),
        "n_low": levels.count("low"),
        "low_indices": [i for i, r in enumerate(reactions) if r.confidence_level == "low"],
        "warnings": warnings,
        "outcomes": {
            "reactions": sum(
                1 for r in reactions if not r.no_reaction and not r.other_entry
            ),
            "no_reaction": sum(1 for r in reactions if r.no_reaction),
            "other": sum(1 for r in reactions if r.other_entry),
        },
    }


# ── A4: two-step conditions + footnote merge + no-drop rules (MERMaid) ───────
# Step 1 reads the main reaction; step 2 is this deterministic merge — no
# second LLM call (A-batch zero-resource constraint). Footnotes never leak
# across entries, and N.R. / other entries are never silently dropped.
NO_REACTION_SENTINEL = "N.R."
_NO_REACTION_TEXT_RE = re.compile(r"\bN\.R\.?\b|\bNR\b|no reaction|无反应", re.IGNORECASE)
_FOOTNOTE_DEF_RE = re.compile(r"^\s*([A-Za-z0-9()\[\]]+)\s*=\s*(.+?)\s*$")
# Explicit "same as above" markers. Bare dashes are deliberately NOT markers:
# a dash cell means "same" in some tables and "none" in others.
_INHERIT_MARKERS = frozenset({"同前", "同上", "same", "same as above", "ditto", "同前所述"})


def normalize_outcome(reaction: VisionReaction) -> VisionReaction:
    """Unify textual no-reaction markers to the single sentinel (boolean).

    MERMaid mixed N.R. / NR sentinels; FormuNex keeps one: no_reaction=True
    plus NO_REACTION_SENTINEL in text. Only fires for side-less reactions
    whose notes actually say so — a reaction with sides is never rewritten.
    """
    if (
        not reaction.reactants
        and not reaction.products
        and not reaction.no_reaction
        and _NO_REACTION_TEXT_RE.search(reaction.notes or "")
    ):
        reaction.no_reaction = True
    return reaction


def _footnote_marker_re(key: str):
    return re.compile(r"\[" + re.escape(key) + r"\]|\(" + re.escape(key) + r"\)")


def merge_footnotes(
    reactions: list[VisionReaction], footnotes: list[VisionFootnote]
) -> list[VisionReaction]:
    """Apply footnote corrections to reaction conditions (step 2).

    Two mechanisms, both entry-scoped (a footnote with entry E applies
    only to the reaction with entry E; document-level footnotes apply
    wherever they are referenced):
    - abbreviation expansion: a footnote of the form ``ABBR = EXPANSION``
      replaces ABBR (word-bounded) in condition text;
    - marker notes: a condition citing ``[key]`` / ``(key)`` records the
      footnote key in applied_footnotes and appends its text to the
      reaction notes (original condition text is preserved).
    """
    if not footnotes:
        return list(reactions)
    out: list[VisionReaction] = []
    for rxn in reactions:
        applied = list(rxn.applied_footnotes)
        note_lines: list[str] = []
        new_conditions: list[VisionReactionCondition] = []
        for cond in rxn.conditions:
            original = cond.text or ""
            text = original
            for fn in footnotes:
                if fn.entry and fn.entry != (rxn.entry or ""):
                    continue
                definition = _FOOTNOTE_DEF_RE.match(fn.text or "")
                if definition:
                    abbr, expansion = definition.group(1), definition.group(2)
                    bounded = re.compile(
                        r"(?<![A-Za-z0-9])" + re.escape(abbr) + r"(?![A-Za-z0-9])"
                    )
                    if bounded.search(text):
                        text = bounded.sub(expansion, text)
                        if fn.key and fn.key not in applied:
                            applied.append(fn.key)
                if fn.key and _footnote_marker_re(fn.key).search(original):
                    if fn.key not in applied:
                        applied.append(fn.key)
                    note_lines.append(f"Footnote {fn.key}: {fn.text}")
            new_conditions.append(
                cond.model_copy(update={"text": text}) if text != original else cond
            )
        notes = rxn.notes
        if note_lines:
            notes = (notes + "\n" if notes else "") + "\n".join(note_lines)
        out.append(rxn.model_copy(update={
            "conditions": new_conditions,
            "applied_footnotes": applied,
            "notes": notes,
        }))
    return out


def apply_condition_inheritance(
    reactions: list[VisionReaction],
) -> list[VisionReaction]:
    """Optimisation-table inherit / replace semantics (rule table, not
    model free play). A condition whose text is an explicit inherit marker
    takes the previous row's same-role condition; any other value replaces;
    an absent role stays absent (absence is not inheritance). First-row
    markers have no previous row and are kept verbatim."""
    out: list[VisionReaction] = []
    prev_by_role: dict[str, VisionReactionCondition] = {}
    for rxn in reactions:
        new_conditions: list[VisionReactionCondition] = []
        for cond in rxn.conditions:
            if (cond.text or "").strip().lower() in _INHERIT_MARKERS and cond.role in prev_by_role:
                new_conditions.append(prev_by_role[cond.role])
            else:
                new_conditions.append(cond)
        for cond in new_conditions:
            prev_by_role[cond.role] = cond
        out.append(
            rxn.model_copy(update={"conditions": new_conditions})
            if new_conditions != rxn.conditions
            else rxn
        )
    return out


# Optimisation-run field table (plan-enumerated fields only; the plan cites
# 22 MERMaid fields but enumerates these — no further fields invented).
# Adding a field = adding a row (B5 field-table prompt assembly preview).
OPTIMIZATION_RUN_FIELDS = [
    {"key": "entry", "label": "Entry"},
    {"key": "reactant_refs", "label": "Reactant refs"},
    {"key": "catalyst", "label": "Catalyst"},
    {"key": "ligand", "label": "Ligand"},
    {"key": "electrode", "label": "Electrode"},
    {"key": "current", "label": "Current"},
    {"key": "electrolyte", "label": "Electrolyte"},
    {"key": "solvent", "label": "Solvent"},
    {"key": "duration", "label": "Duration"},
    {"key": "atmosphere", "label": "Atmosphere"},
    {"key": "temperature", "label": "Temperature"},
    {"key": "yield", "label": "Yield"},
    {"key": "yield_type", "label": "Yield type"},
    {"key": "ee_er", "label": "ee/er"},
    {"key": "footnotes", "label": "Footnotes"},
]


def optimization_run_to_reaction(
    run: dict, *, label_to_smiles: dict | None = None
) -> VisionReaction:
    """One optimisation-table row → VisionReaction.

    Reactants are INDEX REFERENCES (not repeated SMILES): resolved through
    the A3 label map when available, otherwise kept as label-only sides
    (never dropped). Each present scalar field becomes a condition whose
    role is the field key; ``footnotes`` is not a condition — pass it to
    merge_footnotes separately. Products are the run target and are filled
    by the caller, not by the row.
    """
    reactants: list[VisionReactionSide] = []
    for ref in run.get("reactant_refs", []) or []:
        ref_text = str(ref)
        smiles = (
            resolve_side_label(ref_text, label_to_smiles)
            if label_to_smiles
            else None
        )
        reactants.append(VisionReactionSide(smiles=smiles or "", label=ref_text))
    conditions: list[VisionReactionCondition] = []
    for field in OPTIMIZATION_RUN_FIELDS:
        key = field["key"]
        if key in ("entry", "reactant_refs", "footnotes"):
            continue
        value = run.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        conditions.append(VisionReactionCondition(role=key, text=str(value)))
    return VisionReaction(
        entry=str(run.get("entry", "") or ""),
        reactants=reactants,
        conditions=conditions,
        products=[],
        confidence=0.5,
    )


def extract_reaction_image(
    content: bytes,
    filename: str = "reaction.png",
    *,
    label_to_smiles: dict | None = None,
    rgroup_mapping: dict | None = None,
) -> tuple[VisionReactionExtraction | None, str | None]:
    """Reaction scheme → structured reaction JSON via the configured vision LLM.

    Unlike extract_image (molecules + LaTeX), this returns reactions with
    roles: reactants / conditions[{role, text}] / products, plus a locally
    assembled reaction SMILES. Never raises — degrades to (None, hint).
    """
    ok, hint = vision_available()
    if not ok:
        return None, hint
    cfg = resolve_role(VISION)
    prompt = f"{_reaction_prompt()}\n\n文件名：{filename}"
    try:
        raw = _call_vision(cfg, prompt, content, filename)
        data = json.loads(_strip_json_fences(raw))
        extraction = VisionReactionExtraction.model_validate(data)
        # A4: unify textual no-reaction markers to the single sentinel.
        extraction.reactions = [normalize_outcome(r) for r in extraction.reactions]
        # A3 (opt-in): resolve empty-SMILES sides from a known label map
        # BEFORE the RDKit gate, so resolved SMILES are still verified and
        # canonicalised by _verify_reaction rather than trusted blindly.
        if label_to_smiles:
            for r in extraction.reactions:
                for side in list(r.reactants) + list(r.products):
                    if not (side.smiles or "").strip():
                        resolved = resolve_side_label(side.label, label_to_smiles)
                        if resolved:
                            side.smiles = resolved
        # B2 (opt-in): R-group substitution BEFORE the RDKit gate, so
        # substituted SMILES are still verified and canonicalised there.
        # Fallback tracked by INDEX: merge/inherit copy reactions, but all
        # steps preserve order and count, so indices survive.
        rgroup_fallback_indices: set[int] = set()
        if rgroup_mapping:
            substituted: list[VisionReaction] = []
            for idx, r in enumerate(extraction.reactions):
                new_rxn, _report, fallback = apply_rgroup_to_reaction(
                    r, rgroup_mapping, chem=_resolve_chem()
                )
                if fallback:
                    rgroup_fallback_indices.add(idx)
                substituted.append(new_rxn)
            extraction.reactions = substituted
        extraction.reactions = [_verify_reaction(r) for r in extraction.reactions]
        # A4 step 2: footnote merge, then cross-row condition inheritance.
        extraction.reactions = merge_footnotes(extraction.reactions, extraction.footnotes)
        extraction.reactions = apply_condition_inheritance(extraction.reactions)
        # A2 stratification runs after the RDKit gate; the 0.3 cap is never
        # raised back up by feature scores (see compute_reaction_confidence).
        extraction.reactions = [apply_confidence_features(r) for r in extraction.reactions]
        # B2: string-fallback substitutions (no RDKit) are marked low
        # confidence after scoring, so the flag survives the A2 overwrite.
        for idx in rgroup_fallback_indices:
            r = extraction.reactions[idx]
            if "rgroup_string_fallback" not in (r.confidence_flags or []):
                r.confidence_flags = list(r.confidence_flags or []) + [
                    "rgroup_string_fallback"
                ]
        extraction.confidence_summary = build_confidence_summary(extraction.reactions)
        return extraction, None
    except Exception as exc:
        return None, degrade_return(logger, exc, "vision reaction extraction failed", _failure_hint(exc))


# DeepSeek ships a vision model (`deepseek-v4-flash-vision-exp`) but its text
# models (deepseek-v4-pro / v4-flash) still reject an `image_url` content part
# with a 400 ("This model does not support image"). So the guard is model-level
# for deepseek: a vision model passes, a text model gets a hint naming the
# vision model. Every other OpenAI-compatible vendor (qwen / moonshot) is left
# to fail at the call with the provider's own message rather than be guessed at.
_DEEPSEEK_VISION_MARKER = "vision"


def _is_deepseek_vision(model: str) -> bool:
    """DeepSeek models that accept images carry ``vision`` in their id."""
    return _DEEPSEEK_VISION_MARKER in (model or "").lower()


def vision_available() -> tuple[bool, str]:
    """(configured, hint) — key present, provider not known-hostile, SDK installed.

    Note what this does **not** claim: that the configured model can actually
    read a picture. For a self-hosted or rented endpoint that is unknowable from
    here — we have no way to tell which weights the user deployed — and a
    capability table we cannot keep accurate is exactly the lying probe this
    codebase has been bitten by four times. So ``custom`` follows the policy
    already documented for qwen/moonshot below: don't guess, let the call fail
    carrying the provider's own message. ``POST /api/settings/vision/test`` is
    the way to actually *know*.

    The SDK check is not a formality. Without it this returned ``(True, "")``
    on a machine with no ``openai`` package, so callers were told vision was
    available and every image came back as ``No module named 'openai'`` — the
    same "installed but cannot work" gap that made uploads silently index
    nothing. An availability probe that answers yes when the next call cannot
    succeed is worse than no probe, because the caller stops planning for the
    failure.
    """
    from .errors import optional_import

    settings = get_settings()
    if not effective_setting(settings, "vision_extract_enabled"):
        return False, "图片视觉解析已禁用（FORMUNEX_VISION_EXTRACT_ENABLED）"

    cfg = resolve_role(VISION)
    if not cfg.configured:
        where = "文本模型" if cfg.inherited else "视觉模型"
        return False, f"未配置「设置 → 大模型 → {where}」的 API key，无法调用视觉模型"
    if not cfg.model:
        return False, "未指定视觉模型名称（设置 → 大模型 → 视觉模型）"
    if cfg.provider == "gemini":
        return False, "Gemini 原生接口暂不支持图片解析——请切换 OpenAI 兼容供应商或 Anthropic"
    if cfg.provider == "deepseek" and not _is_deepseek_vision(cfg.model):
        hint = (
            "deepseek 文本模型（deepseek-v4-pro / v4-flash）不支持图片输入。"
            "请在「设置 → 大模型 → 视觉模型」指定 deepseek-v4-flash-vision-exp，"
            "或切换其他支持视觉的供应商（Claude / OpenAI / 自定义端点），"
            "文本模型可保持不变。"
        )
        return False, hint

    # Anthropic has its own client; every other supported provider is reached
    # through the OpenAI-compatible one.
    if cfg.provider == "anthropic":
        if not optional_import("anthropic"):
            return False, "未安装 anthropic SDK（pip install anthropic 或安装 llm extra）"
    elif not optional_import("openai"):
        return False, (
            f"未安装 openai SDK，无法调用 {cfg.provider} 的视觉接口"
            "（pip install openai 或安装 llm extra）"
        )
    return True, ""


def _strip_json_fences(raw: str) -> str:
    t = (raw or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    m = re.search(r"\{.*\}", t, re.DOTALL)
    return m.group(0) if m else t


def _call_openai_vision(
    prompt: str, image_b64: str, mime: str, *, api_key: str, model: str,
    base_url: str | None, max_tokens: int, timeout: float,
    extra_headers: dict[str, str] | None = None,
) -> str:
    from openai import OpenAI  # type: ignore

    kwargs: dict = {"api_key": api_key, "timeout": timeout}
    if base_url:
        kwargs["base_url"] = base_url
    if extra_headers:
        kwargs["default_headers"] = dict(extra_headers)
    client = OpenAI(**kwargs)
    resp = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{image_b64}"},
                    },
                ],
            }
        ],
    )
    return resp.choices[0].message.content or ""


def _call_anthropic_vision(
    prompt: str, image_b64: str, mime: str, *, api_key: str, model: str,
    max_tokens: int, timeout: float,
) -> str:
    import anthropic  # type: ignore

    client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": mime, "data": image_b64},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def _call_vision(cfg: RoleConfig, prompt: str, content: bytes, filename: str) -> str:
    """Send one image + prompt to the resolved vision role, return raw text.

    The two public entry points used to each carry their own copy of the
    provider/key/model/base_url/timeout resolution and the anthropic-vs-OpenAI
    branch. Both now share this, so a change to how vision is reached cannot
    apply to one caller and miss the other.
    """
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "png").lower()
    mime = _MIME_BY_EXT.get(ext, "image/png")
    image_b64 = base64.b64encode(content).decode("ascii")

    if cfg.provider == "anthropic":
        return _call_anthropic_vision(
            prompt, image_b64, mime,
            api_key=cfg.api_key, model=cfg.model,
            max_tokens=cfg.max_tokens, timeout=cfg.timeout,
        )
    return _call_openai_vision(
        prompt, image_b64, mime,
        api_key=cfg.api_key, model=cfg.model, base_url=cfg.base_url,
        max_tokens=cfg.max_tokens, timeout=cfg.timeout,
        extra_headers=cfg.extra_headers,
    )


def _failure_hint(exc: Exception) -> str:
    """Turn a provider exception into something worth reading.

    A 503 from a scale-to-zero endpoint means "not running yet", not "broken" —
    saying so is the difference between waiting a minute and going to hunt for a
    bad API key.
    """
    if is_warming(exc):
        return "视觉端点正在冷启动（503），副本就绪后重试即可"
    return str(exc)[:200]


def _is_permanent_vision_failure(error: str | None) -> bool:
    """Whether *error* describes a condition retrying cannot fix.

    The upstream vision endpoint reports "endpoint is paused" as a 400
    (not a 503), and a revoked token returns 401/403.  Retrying either is
    strictly waste — the answer will not change until an operator acts.
    A transient network error, timeout, or 503 cold-start is NOT permanent
    and should not trip the breaker.
    """
    if not error:
        return False
    lower = error.lower()
    permanent = (
        "endpoint is paused",
        "bad request",
        "bad_request",
        "account",
        "suspended",
        "forbidden",
        "unauthorized",
        "insufficient_quota",
        "quota has been exhausted",
    )
    return any(marker in lower for marker in permanent)


def prewarm() -> tuple[bool, float, str]:
    """Wake a scale-to-zero endpoint once, before a batch of figures.

    Returns ``(ok, seconds, hint)``. A no-op returning ``(True, 0.0, "")`` for
    every provider except a custom endpoint, so callers need no knowledge of who
    is serving vision.

    Why bother: with an endpoint scaled to zero the first real call absorbs a
    multi-minute boot. Paying that inside the per-page loop makes the wait look
    like "page 3 is mysteriously slow"; paying it here attributes it correctly in
    the log and keeps the per-page timings meaningful. It costs one tiny request.

    Best-effort by contract — a failure here is not a reason to skip the figures,
    because the per-page calls will each try anyway and may well succeed.
    """
    import time

    ok, hint = vision_available()
    if not ok:
        return False, 0.0, hint
    cfg = resolve_role(VISION)
    if cfg.provider != CUSTOM_PROVIDER:
        return True, 0.0, ""

    started = time.monotonic()
    try:
        from openai import OpenAI  # type: ignore

        kwargs: dict = {"api_key": cfg.api_key, "timeout": cfg.timeout}
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        if cfg.extra_headers:
            kwargs["default_headers"] = dict(cfg.extra_headers)
        OpenAI(**kwargs).chat.completions.create(
            model=cfg.model,
            max_tokens=1,
            messages=[{"role": "user", "content": "ok"}],
        )
    except Exception as exc:
        elapsed = time.monotonic() - started
        if is_warming(exc):
            hint = "视觉端点仍在冷启动中（副本尚未就绪）"
        else:
            hint = str(exc)[:200]
        logger.warning("vision prewarm failed after %.1fs: %s", elapsed, hint)
        return False, elapsed, hint

    elapsed = time.monotonic() - started
    logger.info("vision endpoint warm after %.1fs (%s)", elapsed, cfg.model)
    return True, elapsed, ""


def _tiny_png(side: int = 8) -> bytes:
    """A valid solid-grey PNG, built without Pillow.

    Hand-rolled rather than a hardcoded base64 blob so it stays readable, and
    8×8 rather than 1×1 because some vision stacks reject degenerate images —
    a probe that fails for its own reasons teaches nothing about the endpoint.
    """
    import struct
    import zlib

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    # 8-bit greyscale, no interlace. Each row is prefixed with its filter byte.
    ihdr = struct.pack(">IIBBBBB", side, side, 8, 0, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes([0x80] * side) for _ in range(side))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def probe_vision() -> dict:
    """Actually call the vision model and report what happened.

    ``vision_available()`` answers "is it configured"; this answers "does it
    work", which for a bring-your-own endpoint is the only honest way to know.
    """
    import time

    ok, hint = vision_available()
    if not ok:
        return {"ok": False, "provider": "", "model": "", "message": hint}

    cfg = resolve_role(VISION)
    base = {
        "provider": cfg.provider,
        "model": cfg.model,
        "base_url": cfg.base_url,
        "inherits": cfg.inherited,
    }
    started = time.monotonic()
    try:
        raw = _call_vision(
            cfg,
            "这是一张纯色测试图。只回复两个字符：OK",
            _tiny_png(),
            "probe.png",
        )
    except Exception as exc:
        logger.warning("vision probe failed (%s/%s): %s", cfg.provider, cfg.model, exc)
        return {**base, "ok": False, "message": _failure_hint(exc)}

    elapsed = time.monotonic() - started
    text = (raw or "").strip()
    if not text:
        # Reached the model but got nothing usable — the endpoint is up and the
        # credentials work, so say that rather than implying a config error.
        return {
            **base,
            "ok": False,
            "message": f"端点已响应但未返回文本（{elapsed:.1f}s），请确认模型具备视觉能力",
        }
    return {
        **base,
        "ok": True,
        "message": f"视觉模型可用（{elapsed:.1f}s）：{text[:80]}",
    }


def _verify_molecules(molecules: list[VisionMolecule]) -> list[VisionMolecule]:
    """RDKit validation loop: parse → canonicalize → flag; drop empty claims."""
    try:
        from rdkit import Chem, RDLogger  # type: ignore

        RDLogger.DisableLog("rdApp.*")
        rdkit_ok = True
    except Exception:
        rdkit_ok = False

    out: list[VisionMolecule] = []
    for mol in molecules:
        smi = (mol.smiles or "").strip()
        if not smi and not mol.name:
            continue
        if smi and rdkit_ok:
            try:
                parsed = Chem.MolFromSmiles(smi)
            except Exception:
                parsed = None
            if parsed is not None:
                mol.smiles = Chem.MolToSmiles(parsed)
                mol.verified = True
            else:
                # Keep the claim (name may still be useful) but flag it.
                mol.confidence = min(mol.confidence, 0.3)
                mol.verified = False
        out.append(mol)
    return out


def _molecules_from_smiles(smiles: str) -> VisionExtraction:
    """把单个 SMILES 字符串包成 VisionExtraction（经 RDKit 验证兜底）。"""
    mol = VisionMolecule(smiles=(smiles or "").strip(), confidence=0.8)
    molecules = _verify_molecules([mol])
    return VisionExtraction(kind="structure", molecules=molecules)


def _ocsr_direct(content: bytes, settings) -> VisionExtraction | None:
    """① OCSR 离线直识（免 token，假设图已裁剪）。

    投递独立 molscribe Celery worker 识别，成功即返回；任何失败返回 None
    （由调用方回退视觉 LLM）。
    """
    if not settings.ocsr_enabled:
        return None
    try:
        # 延迟导入：避免主进程模块加载期拉起 Celery 依赖链
        from app.worker.celery_app import celery_app
        import os

        # 写共享卷而非 /tmp：molscribe 是独立容器，看不到 backend 的 /tmp
        # （OpenCV 报 !src.empty()）。/app/data 两容器都挂载；源码模式
        # host 写仓库根 data/（同一挂载源双视角）。用 FORMUNEX_ENV_FILE
        # 标志区分（不能用 /app/data 存在性——host 可能残留孤儿目录）。
        if source_checkout_mode():
            # __file__ = <root>/backend/app/services/vision_extract.py
            _root = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            )
            shared_dir = os.path.join(_root, "data", "_ocsr_tmp")
        else:
            shared_dir = "/app/data/_ocsr_tmp"
        os.makedirs(shared_dir, exist_ok=True)
        # P1-13: 唯一文件名 —— 同进程并发 OCSR 此前按 PID 命名互相覆盖。
        import uuid

        path = os.path.join(shared_dir, f"ocsr_{os.getpid()}_{uuid.uuid4().hex}.png")
        with open(path, "wb") as f:
            f.write(content)
        try:
            # 容器视角路径：worker(molscribe 容器) 读 /app/data；host 源码
            # 模式写的是仓库根 data/（同一挂载双视角），投递前转换。
            worker_path = path
            if source_checkout_mode():
                _root = os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                )
                _host_data = os.path.join(_root, "data")
                if worker_path.startswith(_host_data):
                    worker_path = "/app/data" + worker_path[len(_host_data):]
            res = celery_app.send_task(
                "formunex.molscribe_recognize",
                args=[{"image_path": worker_path}],
                queue=settings.molscribe_queue,
            ).get(timeout=settings.molscribe_timeout_s)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        if res and res.get("ok"):
            return _molecules_from_smiles(res["smiles"])
    except Exception as exc:
        logger.warning("OCSR direct path failed: %s", exc)
    return None


def extract_image(content: bytes, filename: str) -> tuple[VisionExtraction | None, str | None]:
    """Structured extraction for one image; (None, reason) when unavailable.

    结构图→SMILES 优先走 OCSR（MolScribe）离线直识（免 token），失败回退视觉 LLM。
    """
    settings = get_settings()
    # ① OCSR 离线直识（免 token，主路径）
    if settings.ocsr_enabled:
        extraction = _ocsr_direct(content, settings)
        if extraction is not None:
            return extraction, None

    # ④ 兜底：视觉 LLM（原逻辑不动）
    ok, hint = vision_available()
    if not ok:
        return None, hint
    cfg = resolve_role(VISION)
    prompt = f"{_VISION_SYSTEM}\n\n文件名：{filename}"

    try:
        raw = _call_vision(cfg, prompt, content, filename)
        data = json.loads(_strip_json_fences(raw))
        extraction = VisionExtraction.model_validate(data)
        extraction.molecules = _verify_molecules(extraction.molecules)
        return extraction, None
    except Exception as exc:
        return None, degrade_return(logger, exc, "vision extraction failed", _failure_hint(exc))


def extract_structured_table_from_image(
    image_bytes: bytes,
    filename: str = "table.png",
    context_text: str = "",
) -> tuple[VisionTableExtraction | None, str | None]:
    """Patent comparison-table extraction → structured formulation JSON.

    Synchronous API (matches the rest of vision_extract / ingestion). FastAPI
    routes may wrap this in ``run_in_threadpool`` when needed.
    """
    ok, hint = vision_available()
    if not ok:
        return None, hint
    cfg = resolve_role(VISION)
    prompt = build_multimodal_table_prompt(context_text)
    if filename:
        prompt = f"{prompt}\n\n文件名：{filename}"

    try:
        raw = _call_vision(cfg, prompt, image_bytes, filename)
        data = json.loads(_strip_json_fences(raw))
        if not isinstance(data, dict):
            return None, "视觉模型返回非对象 JSON"
        extraction = VisionTableExtraction.model_validate(data)
        if not extraction.formulations:
            return extraction, "未识别到配方行（formulations 为空）"
        return extraction, None
    except json.JSONDecodeError as exc:
        return None, degrade_return(logger, exc, "vision table JSON parse failed", str(exc)[:200])
    except Exception as exc:
        return None, degrade_return(
            logger, exc, "vision table extraction failed", _failure_hint(exc)
        )


def image_markdown(extraction: VisionExtraction, filename: str) -> str:
    """Render an extraction as the Markdown document that enters the KB."""
    kind_labels = {
        "table": "表格", "structure": "分子结构图", "flowchart": "流程图",
        "equation": "反应方程式", "document": "文档截图", "other": "图片",
    }
    parts = [f"# {filename}（{kind_labels.get(extraction.kind, '图片')}·视觉解析）"]
    if extraction.markdown.strip():
        parts.append(extraction.markdown.strip())
    if extraction.molecules:
        rows = ["| 分子 | SMILES | 置信度 | RDKit 验证 |", "|---|---|---|---|"]
        for mol in extraction.molecules:
            rows.append(
                f"| {mol.name or '—'} | `{mol.smiles or '—'}` "
                f"| {mol.confidence:.2f} | {'✓' if mol.verified else '✗'} |"
            )
        parts.append("## 识别的分子结构\n\n" + "\n".join(rows))
    if extraction.notes.strip():
        parts.append(f"> 备注：{extraction.notes.strip()}")
    return "\n\n".join(parts)

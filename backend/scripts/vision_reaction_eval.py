#!/usr/bin/env python3
"""Evaluate vision-LLM reaction extraction on a directory of scheme images.

Usage (from the FormuNex repo root, with a configured vision endpoint):

    backend/.venv/bin/python backend/scripts/vision_reaction_eval.py \
        --image-dir /path/to/reaction_images \
        [--ground-truth /path/to/truth.json] [--out report.json]

Ground truth format (optional), two shapes per file:
- legacy: {"rxn1.png": {"reaction_smiles": ["A>>B"]}} — exact_match below
  is this legacy any-match on reaction SMILES strings, unchanged;
- structured: {"rxn1.png": {"reactions": [{"reactants": ["A"],
  "products": ["B"], "conditions": ["THF"]}]}} — scored with soft/hard
  signatures (see score_figure). A list-shaped ground-truth file
  ([{"file": ..., ...}, ...]) is also accepted; duplicate "file" values
  in it are an error (they would silently mix GT versions).

Method (three-library A5, extending — not replacing — this script):
- canon_smiles: illegal tokens dropped; multi-fragment → largest heavy
  fragment; atomic-number-0 atoms cleared of isotope/mapping; stereo
  removed by default (opt-out flag); charged / metal-containing structures
  go through an InChI round-trip; the result is double-parse validated.
  Without RDKit the fallback is string-level (longest fragment, no
  canonicalisation) and the report says so.
- soft signature = (reactant multiset, product multiset); hard signature
  adds the condition multiset. Hard matching requires every GT-drawn
  condition to appear in the predicted conditions (multiset subset
  after reconciliation); extra predicted conditions are NOT penalised.
- _reconcile_roles: a predicted reactant that GT lists as a condition
  (catalyst read as reactant, solvent read as reactant) is moved to the
  condition side before comparison, and vice versa, so role-boundary
  disputes do not drown real structure errors.
- Predicted ↔ GT reactions are matched by optimal assignment
  (scipy linear_sum_assignment when available, greedy otherwise — the
  report names which one ran). Per figure: soft/hard P/R/F1, tp counts,
  and predictions_without_gt as its own column.
- No only_predicted pilot mode exists: figures without ground truth are
  never silently dropped from the report — they appear as rows with
  has_ground_truth false and count in n_without_ground_truth, while
  P/R/F1 are computed over scored figures only and labelled as such.

Requires a configured vision model + API key (Settings → 大模型 → 视觉模型);
without one it prints the hint and exits 2 instead of fabricating results.
MolScribe comparison is deliberately out of scope here: it needs the separate
molscribe Celery worker running; compare its single-molecule outputs against
the reactants/products sides in a follow-up run.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

ILLEGAL_SMILES_TOKENS = frozenset({"", "none", "null", "n/a", "na", "?", "*"})


def _metal_symbols():
    try:
        from app.services.vision_extract import _METAL_SYMBOLS

        return _METAL_SYMBOLS
    except Exception:
        return frozenset({
            "Li", "Na", "K", "Mg", "Ca", "Fe", "Zn", "Cu", "Pd", "Pt", "Ni", "Co",
            "Al",
        })


def canon_smiles(smi, *, chem=None, remove_stereo=True):
    """Canonicalise one SMILES, or None when it carries no structure.

    Illegal tokens (empty / none / null / n/a / na / ? / *) are dropped
    before any parsing. Multi-fragment input keeps only the largest
    heavy-atom fragment. Atomic-number-0 atoms lose isotope and mapping
    numbers. Stereo is removed by default. Charged or metal-containing
    structures go through an InChI round-trip. The output is parsed a
    second time and must re-canonicalise identically, else None.
    """
    if smi is None:
        return None
    text = str(smi).strip()
    if text.lower() in ILLEGAL_SMILES_TOKENS:
        return None
    if chem is None:
        frags = [f for f in text.split(".") if f]
        return max(frags, key=len) if frags else None
    best = None
    best_heavy = -1
    for frag in text.split("."):
        if not frag:
            continue
        mol = chem.MolFromSmiles(frag)
        if mol is None:
            continue
        try:
            heavy = mol.GetNumHeavyAtoms()
        except Exception:
            heavy = sum(1 for a in mol.GetAtoms() if a.GetAtomicNum() > 0)
        if heavy > best_heavy:
            best, best_heavy = mol, heavy
    if best is None:
        return None
    mol = best
    for atom in mol.GetAtoms():
        if atom.GetAtomicNum() == 0:
            atom.SetIsotope(0)
            atom.SetAtomMapNum(0)
    if remove_stereo:
        chem.RemoveStereochemistry(mol)
    metals = _metal_symbols()
    needs_inchi = any(
        a.GetFormalCharge() != 0 or a.GetSymbol() in metals
        for a in mol.GetAtoms()
    )
    if needs_inchi:
        try:
            inchi = chem.MolToInchi(mol)
        except Exception:
            return None
        mol = chem.MolFromInchi(inchi)
        if mol is None:
            return None
    out = chem.MolToSmiles(mol)
    if not out:
        return None
    mol2 = chem.MolFromSmiles(out)
    if mol2 is None:
        return None
    if chem.MolToSmiles(mol2) != out:
        return None
    return out


def _split_side_smiles(text):
    """Split a side (``A+B``) on '+' outside brackets (charges live inside)."""
    parts, buf, depth = [], [], 0
    for ch in str(text):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(0, depth - 1)
        if ch == "+" and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return [p for p in parts if p]


def _normalize_condition(text):
    return " ".join(str(text).split())


def _side_texts(value):
    """Sides may be plain strings or {smiles} / {text} dicts."""
    out = []
    for item in value or []:
        if isinstance(item, dict):
            out.append(item.get("smiles") or item.get("text") or "")
        else:
            out.append(str(item))
    return out


def gt_entry_to_reactions(entry):
    """One ground-truth entry → [(reactants, products, conditions)]."""
    entry = entry or {}
    if entry.get("reactions") is not None:
        out = []
        for rxn in entry["reactions"]:
            out.append((
                _side_texts(rxn.get("reactants")),
                _side_texts(rxn.get("products")),
                [_normalize_condition(c) for c in _side_texts(rxn.get("conditions"))],
            ))
        return out
    out = []
    for rsmi in entry.get("reaction_smiles", []) or []:
        left, _, right = str(rsmi).partition(">>")
        out.append((_split_side_smiles(left), _split_side_smiles(right), []))
    return out


def _canon_counter(sides, chem):
    counter: Counter = Counter()
    for side in sides:
        canon = canon_smiles(side, chem=chem)
        if canon is not None:
            counter[canon] += 1
    return counter


def _reconcile_roles(pred_r, pred_c, gt_r, gt_c):
    """Move predicted sides across the reactant/condition boundary when GT
    lists them on the other side (catalyst read as reactant, and reverse).
    Only moves as many units as GT lists on the receiving side."""
    pr, pc = Counter(pred_r), Counter(pred_c)
    for side in list(pr):
        excess = pr[side] - gt_r.get(side, 0)
        if excess > 0 and gt_c.get(side, 0) > 0:
            move = min(excess, gt_c[side])
            pr[side] -= move
            pc[side] += move
    for side in list(pc):
        excess = pc[side] - gt_c.get(side, 0)
        if excess > 0 and gt_r.get(side, 0) > 0:
            move = min(excess, gt_r[side])
            pc[side] -= move
            pr[side] += move
    return +pr, +pc


def _prf(tp, fp, fn):
    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "p": round(precision, 4),
            "r": round(recall, 4), "f1": round(f1, 4)}


def _assign_pairs(costs, n_pred, n_gt):
    """Optimal assignment of predicted ↔ GT reactions.

    Returns (matched_pairs_with_cost_0, method). scipy when available,
    greedy otherwise (greedy can undercount on adversarial matrices —
    the report names the method so a greedy score is never mistaken
    for an optimal one)."""
    if n_pred == 0 or n_gt == 0:
        return [], "none"
    try:
        from scipy.optimize import linear_sum_assignment
    except Exception:
        linear_sum_assignment = None
    if linear_sum_assignment is not None:
        rows, cols = linear_sum_assignment(costs)
        return [
            (int(i), int(j)) for i, j in zip(rows, cols) if costs[i][j] == 0
        ], "hungarian"
    order = sorted(
        (costs[i][j], i, j) for i in range(n_pred) for j in range(n_gt)
    )
    used_p, used_g, matched = set(), set(), []
    for cost, i, j in order:
        if i in used_p or j in used_g:
            continue
        used_p.add(i)
        used_g.add(j)
        if cost == 0:
            matched.append((i, j))
    return matched, "greedy"


def score_figure(pred, gt, *, chem=None):
    """Score one figure. pred/gt are [(reactants, products, conditions)].

    Soft = reactants + products multisets (after role reconciliation).
    Hard = soft plus a multiset-subset check on conditions: every GT
    condition must appear in the reconciled predicted conditions; extra
    predicted conditions are NOT penalised. Cost per pair is 0 on match,
    1 otherwise; assignment is optimal per mode (see _assign_pairs)."""
    pred_r = [_canon_counter(r, chem) for r, _, _ in pred]
    pred_p = [_canon_counter(p, chem) for _, p, _ in pred]
    pred_c = [Counter(c) for _, _, c in pred]
    gt_r = [_canon_counter(r, chem) for r, _, _ in gt]
    gt_p = [_canon_counter(p, chem) for _, p, _ in gt]
    gt_c = [Counter(c) for _, _, c in gt]

    def _score(mode):
        costs = []
        for i in range(len(pred)):
            row = []
            for j in range(len(gt)):
                pr, pc = _reconcile_roles(pred_r[i], pred_c[i], gt_r[j], gt_c[j])
                soft_ok = pr == gt_r[j] and pred_p[i] == gt_p[j]
                if mode == "soft":
                    row.append(0 if soft_ok else 1)
                else:
                    cond_ok = all(
                        pc.get(side, 0) >= count
                        for side, count in gt_c[j].items()
                    )
                    row.append(0 if (soft_ok and cond_ok) else 1)
            costs.append(row)
        matched, method = _assign_pairs(costs, len(pred), len(gt))
        tp = len(matched)
        return _prf(tp, len(pred) - tp, len(gt) - tp), matched, method

    soft, soft_matched, soft_method = _score("soft")
    hard, _, hard_method = _score("hard")
    matched_pred = {i for i, _ in soft_matched}
    without_gt = []
    for i, (r, p, _) in enumerate(pred):
        if i not in matched_pred:
            left = "+".join(sorted(_canon_counter(r, chem).elements()))
            right = "+".join(sorted(_canon_counter(p, chem).elements()))
            without_gt.append(f"{left}>>{right}")
    return {
        "soft": soft,
        "hard": hard,
        "predictions_without_gt": without_gt,
        "assignment": {"soft": soft_method, "hard": hard_method},
    }


def _load_ground_truth(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        out = {}
        for entry in data:
            name = entry.get("file") if isinstance(entry, dict) else None
            if not name:
                raise ValueError("ground-truth list entry without a 'file' key")
            if name in out:
                raise ValueError(f"duplicate ground-truth file: {name}")
            out[name] = entry
        return out
    raise ValueError("ground-truth must be an object or a list of entries")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-dir", required=True)
    parser.add_argument("--ground-truth", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    from app.services import vision_extract

    ok, hint = vision_extract.vision_available()
    if not ok:
        print(f"vision unavailable: {hint}")
        return 2

    image_dir = Path(args.image_dir)
    truth: dict = {}
    if args.ground_truth:
        try:
            truth = _load_ground_truth(args.ground_truth)
        except ValueError as exc:
            print(f"ground truth error: {exc}")
            return 2

    try:
        from rdkit import Chem as _Chem
    except Exception:
        _Chem = None

    rows = []
    for path in sorted(image_dir.iterdir()):
        if path.suffix.lower() not in IMAGE_EXTS or not path.is_file():
            continue
        started = time.monotonic()
        extraction, err = vision_extract.extract_reaction_image(
            path.read_bytes(), path.name
        )
        latency = round(time.monotonic() - started, 2)
        predicted = (
            [r.reaction_smiles for r in extraction.reactions if r.reaction_smiles]
            if extraction
            else []
        )
        entry = truth.get(path.name)
        expected = (entry or {}).get("reaction_smiles", [])
        row = {
            "file": path.name,
            "latency_s": latency,
            "n_reactions": len(extraction.reactions) if extraction else 0,
            "reaction_smiles": predicted,
            "error": err,
            "exact_match": bool(expected) and any(p in expected for p in predicted),
            "has_ground_truth": entry is not None,
        }
        if entry is not None:
            pred_tuples = [
                (
                    [s.smiles for s in r.reactants],
                    [s.smiles for s in r.products],
                    [_normalize_condition(c.text) for c in r.conditions],
                )
                for r in (extraction.reactions if extraction else [])
            ]
            scored_figure = score_figure(
                pred_tuples, gt_entry_to_reactions(entry), chem=_Chem
            )
            row["soft"] = scored_figure["soft"]
            row["hard"] = scored_figure["hard"]
            row["predictions_without_gt"] = scored_figure["predictions_without_gt"]
            row["assignment"] = scored_figure["assignment"]
        rows.append(row)
        print(
            f"{path.name}: {len(predicted)} reaction(s) in {latency}s"
            + (f" exact_match={row['exact_match']}" if expected else "")
            + (f" soft_f1={row['soft']['f1']}" if entry is not None else "")
            + (f" hard_f1={row['hard']['f1']}" if entry is not None else "")
            + (f" error={err}" if err else "")
        )

    scored = [r for r in rows if r["has_ground_truth"]]
    summary = {
        "n_images": len(rows),
        "n_parsed": sum(1 for r in rows if r["n_reactions"] > 0),
        "n_scored": len(scored),
        "n_without_ground_truth": len(rows) - len(scored),
        "exact_match_rate": (
            round(sum(1 for r in scored if r["exact_match"]) / len(scored), 4)
            if scored
            else None
        ),
        "soft": _prf(
            sum(r["soft"]["tp"] for r in scored),
            sum(r["soft"]["fp"] for r in scored),
            sum(r["soft"]["fn"] for r in scored),
        ) if scored else None,
        "hard": _prf(
            sum(r["hard"]["tp"] for r in scored),
            sum(r["hard"]["fp"] for r in scored),
            sum(r["hard"]["fn"] for r in scored),
        ) if scored else None,
        "mean_soft_f1": (
            round(sum(r["soft"]["f1"] for r in scored) / len(scored), 4)
            if scored
            else None
        ),
        "mean_hard_f1": (
            round(sum(r["hard"]["f1"] for r in scored) / len(scored), 4)
            if scored
            else None
        ),
        "n_predictions_without_gt": sum(
            len(r.get("predictions_without_gt", [])) for r in scored
        ),
        "assignment": (
            "greedy (scipy unavailable; greedy fallback)"
            if any(
                r["assignment"]["soft"] == "greedy"
                or r["assignment"]["hard"] == "greedy"
                for r in scored
            )
            else "hungarian"
        ) if scored else None,
        "rdkit_available": _Chem is not None,
        "mean_latency_s": round(sum(r["latency_s"] for r in rows) / len(rows), 2)
        if rows
        else None,
        "rows": rows,
    }
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=2))
    if args.out:
        Path(args.out).write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"report written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

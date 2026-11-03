#!/usr/bin/env python3
"""Evaluate vision-LLM reaction extraction on a directory of scheme images.

Usage (from the FormuNex repo root, with a configured vision endpoint):

    backend/.venv/bin/python backend/scripts/vision_reaction_eval.py \
        --image-dir /path/to/reaction_images \
        [--ground-truth /path/to/truth.json] [--out report.json]

Ground truth format (optional): {"rxn1.png": {"reaction_smiles": ["A>>B"]}}.
An image scores exact-match when any predicted reaction SMILES equals any
expected one. Without ground truth the script still reports parse rate,
verification rate, and latency per image.

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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


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
        truth = json.loads(Path(args.ground_truth).read_text(encoding="utf-8"))

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
        expected = (truth.get(path.name) or {}).get("reaction_smiles", [])
        row = {
            "file": path.name,
            "latency_s": latency,
            "n_reactions": len(extraction.reactions) if extraction else 0,
            "reaction_smiles": predicted,
            "error": err,
            "exact_match": bool(expected) and any(p in expected for p in predicted),
            "has_ground_truth": bool(expected),
        }
        rows.append(row)
        print(
            f"{path.name}: {len(predicted)} reaction(s) in {latency}s"
            + (f" exact_match={row['exact_match']}" if expected else "")
            + (f" error={err}" if err else "")
        )

    scored = [r for r in rows if r["has_ground_truth"]]
    summary = {
        "n_images": len(rows),
        "n_parsed": sum(1 for r in rows if r["n_reactions"] > 0),
        "n_scored": len(scored),
        "exact_match_rate": (
            round(sum(1 for r in scored if r["exact_match"]) / len(scored), 4)
            if scored
            else None
        ),
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

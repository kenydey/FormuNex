"""Three-library C1: document label registry (pure data structure).

All labels in a document map to structures; a DRAWN structure always
beats a text name for the same label, and a side that only carries a
label is backfilled from the registry. An SI document merges into the
same registry (labels and figure references cross documents); on a
drawn-vs-drawn conflict the FIRST registration wins, so the main
document keeps priority over a later-merged SI.

No pipeline wiring yet: callers feed vision results in (drawn) and text
names in (text) and call backfill() on label-only sides. A3's
``resolve_side_label`` stays the per-call resolver; this is the
document-level map that builds its input.
"""
from __future__ import annotations


class DocumentLabelRegistry:
    """label -> structure, drawn priority, SI merge, label-only backfill."""

    def __init__(self) -> None:
        self._drawn: dict[str, str] = {}
        self._text: dict[str, str] = {}
        # P3-B20: audit trail for drawn corrections (see correct_drawn).
        self._corrections: list[dict] = []

    def register_drawn(self, label: str, smiles: str) -> None:
        """A structure drawn in a figure. First drawn wins on conflict."""
        label, smiles = (label or "").strip(), (smiles or "").strip()
        if label and smiles and label not in self._drawn:
            self._drawn[label] = smiles

    def correct_drawn(self, label: str, smiles: str, *, reason: str = "") -> bool:
        """Correct a wrongly registered drawn structure (P3-B20).

        First-drawn-wins used to make a mis-drawn first registration
        permanent — there was no update path at all. This is the ONLY
        override path, and it is audited: the previous value, the new
        value and the reason are appended to corrections() instead of
        silently overwriting. Returns False when nothing was registered
        under the label (use register_drawn for that case).
        """
        label, smiles = (label or "").strip(), (smiles or "").strip()
        if not label or not smiles or label not in self._drawn:
            return False
        self._corrections.append({
            "label": label,
            "previous": self._drawn[label],
            "new": smiles,
            "reason": reason,
        })
        self._drawn[label] = smiles
        return True

    def corrections(self) -> list[dict]:
        """Audited drawn corrections, oldest first (copies)."""
        return [dict(c) for c in self._corrections]

    def register_text(self, label: str, smiles: str) -> None:
        """A structure named in text. Never overrides a drawn one."""
        label, smiles = (label or "").strip(), (smiles or "").strip()
        if label and smiles and label not in self._text:
            self._text[label] = smiles

    def resolve(self, label: str) -> str | None:
        """Drawn first, text second, None when unknown. Never invented."""
        label = (label or "").strip()
        return self._drawn.get(label) or self._text.get(label)

    def merge(self, other: "DocumentLabelRegistry") -> None:
        """Merge another document (e.g. SI) into this registry.
        First-drawn-wins applies across the merge too."""
        for label, smiles in other._drawn.items():
            self.register_drawn(label, smiles)
        for label, smiles in other._text.items():
            self.register_text(label, smiles)

    def backfill(self, sides: list[dict]) -> tuple[list[dict], list[str]]:
        """Fill label-only sides (empty smiles, known label) from the
        registry. Returns (new sides, filled labels). Sides are plain
        dicts ``{smiles, label}``; filled sides gain ``source: "registry"``.
        Unknown labels are left untouched, never guessed.

        P2-B35: a filled side is registry-derived, never verified — if
        the side carried a ``verified`` flag it is cleared, matching the
        rgroup convention (substitution clears verified). A filled side
        that still claimed verified=True could otherwise be treated as
        a checked structure downstream.
        """
        out: list[dict] = []
        filled: list[str] = []
        for side in sides or []:
            side = dict(side)
            smiles = (side.get("smiles") or "").strip()
            label = (side.get("label") or "").strip()
            if not smiles and label:
                resolved = self.resolve(label)
                if resolved:
                    side["smiles"] = resolved
                    side["source"] = "registry"
                    if "verified" in side:
                        side["verified"] = False
                    # P3-B20: filled lists each label once, not once per
                    # side — the old list counted sides, not labels.
                    if label not in filled:
                        filled.append(label)
            out.append(side)
        return out, filled

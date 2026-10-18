"""Pre-seeded compliance lists (补齐计划 Phase 1D, 1-16).

Read-only: the lists are baked-in data (see ``services/compliance_lists``).
A red line references an entry by ``{"list_id", "list_entry_id", "max_pct"}``
in the spec contract's ``red_lines`` array; the save / recommend / gate
checks resolve the reference to the entry's CAS numbers.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..services.compliance_lists import ALL_LISTS, get_list, list_to_dict

router = APIRouter(prefix="/api/compliance-lists", tags=["compliance"])


@router.get("")
def list_compliance_lists() -> dict:
    return {"lists": [list_to_dict(lst) for lst in ALL_LISTS]}


@router.get("/{list_id}")
def get_compliance_list(list_id: str) -> dict:
    lst = get_list(list_id)
    if lst is None:
        raise HTTPException(status_code=404, detail=f"未知合规清单：{list_id}")
    return list_to_dict(lst)

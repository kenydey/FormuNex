"""Workbench row block envelope contract — blocktype key.

Salvaged from the Datalab payload-contract tests (batch 2): the sample
payload test went with DatalabCampaignStore, but _blocks_for_row still
builds the envelopes the migration path parses, so its blocktype
contract stays pinned here.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.db.campaign_store import _blocks_for_row
from app.db.datalab_envelope import DATALAB_BLOCK_KIND
from app.db.database import make_engine, make_session_factory
from app.domain.schemas import DOEPlan, DOERun, ProductDomain


def test_datalab_blocks_use_blocktype_not_block_type():
    blocks = _blocks_for_row(
        planned_params={"Zinc phosphate": 8.0},
        actual_params={"Zinc phosphate": 8.0},
        measurements={"salt_spray_hours": None},
        status="Pending",
    )
    for block in blocks.values():
        assert "blocktype" in block
        assert block["blocktype"] == DATALAB_BLOCK_KIND
        assert "block_type" not in block



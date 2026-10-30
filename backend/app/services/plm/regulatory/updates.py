"""Regulatory update packages: stage -> EHS approve -> apply -> rollback (P1-1).

The static regulatory library (§4.1 of the review) becomes updatable without
ever letting unreviewed data take effect:

* A package is staged files + a manifest. Staging touches neither the live
  resource files nor the database, and staged data is invisible to
  ``latest_data_release`` (which only returns approved releases).
* Approval (permission ``regulatory.review`` — held by EHS/regulatory among
  the builtin roles) promotes the files into the live resource directory
  (archiving what they replace) and runs the seed, which creates the new
  approved release. The release gate then fails affected documents until
  they are reclassified — fail-closed, as before.
* Rollback restores the archived files and re-seeds, creating a new release
  with the old data (traceable, never a silent rewrite).

Monthly cadence is a runbook item (see docs/deployment.md), not an
automatic job: fetching runs against production networks and must be
reviewed by a human before approval in any case.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select, update

from ....db.models_regulatory import RegulatoryDataRelease
from .seed import RESOURCES_DIR, seed_regulatory_db

logger = logging.getLogger(__name__)

PACKAGES_DIR = Path(__file__).resolve().parents[4] / "data" / "regulatory_updates"

PACKAGE_FILES = ("ctx_enrichment.json", "clp_atp.json")

_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class UpdateError(Exception):
    """Package staging / approval / rollback failure (mapped to 400)."""


class Forbidden(PermissionError):
    """Caller lacks ``regulatory.review`` (mapped to 403)."""


def _require_review(principal: Any) -> None:
    can = getattr(principal, "can", None)
    if can is None or not can("regulatory.review"):
        raise Forbidden("缺少权限 regulatory.review（EHS/法规专员审批）")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def packages_dir() -> Path:
    PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
    return PACKAGES_DIR


def _package_dir(package_id: str) -> Path:
    if not _ID_RE.match(package_id or ""):
        raise UpdateError(f"非法 package_id {package_id!r}")
    path = packages_dir() / package_id
    if path.parent != packages_dir():
        raise UpdateError(f"非法 package_id {package_id!r}")
    return path


def _write_manifest(package_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    (package_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def _read_manifest(package_dir: Path) -> dict[str, Any]:
    manifest_path = package_dir / "manifest.json"
    if not manifest_path.exists():
        raise UpdateError(f"更新包 {package_dir.name} 不存在")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def create_package(
    *,
    source: str,
    label: str,
    files: dict[str, bytes],
    created_by: str | None,
) -> dict[str, Any]:
    """Stage files as a draft package. Touches no live data."""
    for name in files:
        if Path(name).name != name or name not in PACKAGE_FILES:
            raise UpdateError(f"更新包不接受文件 {name!r}")
    package_id = uuid.uuid4().hex
    package_dir = _package_dir(package_id)
    package_dir.mkdir(parents=True)
    for name, content in files.items():
        (package_dir / name).write_bytes(content)
    manifest = {
        "package_id": package_id,
        "source": source,
        "label": label,
        "files": sorted(files),
        "status": "draft",
        "created_at": _now_iso(),
        "created_by": created_by,
        "approved_by": None,
        "approved_at": None,
        "applied_release_id": None,
        "replaced_files": [],
        "added_files": [],
        "report": {},
    }
    return _write_manifest(package_dir, manifest)


def get_package(package_id: str) -> dict[str, Any]:
    return _read_manifest(_package_dir(package_id))


def list_packages() -> list[dict[str, Any]]:
    out = []
    for manifest_path in sorted(
        packages_dir().glob("*/manifest.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        try:
            out.append(json.loads(manifest_path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def stage_clp_package(
    *,
    atp: str,
    regulation: str,
    applies_from: str,
    entries: list[dict[str, Any]],
    created_by: str | None,
) -> dict[str, Any]:
    """Stage an ECHA ATP table (converted to JSON) as a draft package.

    ECHA distributes Annex VI tables as Excel per ATP with no public bulk
    API, so the ATP file itself is EHS-provided; this step validates every
    row (unmappable rows are reported, never stored) and stages the valid
    ones. A package with zero valid rows is refused outright.
    """
    from .clp_atp import validate_entries

    if not atp or not regulation:
        raise UpdateError("ATP 版本与法规文号不能为空")
    valid, report = validate_entries(entries)
    if not valid:
        raise UpdateError(f"ATP 行全部无效，拒绝建包：{report}")
    payload = {
        "atp": atp,
        "regulation": regulation,
        "applies_from": applies_from,
        "entries": valid,
        "cleared_cas": [],
    }
    manifest = create_package(
        source="echa_clp",
        label=f"CLP {atp}",
        files={
            "clp_atp.json": json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        },
        created_by=created_by,
    )
    manifest["report"] = {"clp_validation": report}
    return _write_manifest(_package_dir(manifest["package_id"]), manifest)


def reject_package(package_id: str, principal: Any) -> dict[str, Any]:
    _require_review(principal)
    package_dir = _package_dir(package_id)
    manifest = _read_manifest(package_dir)
    if manifest["status"] != "draft":
        raise UpdateError(f"更新包 {package_id} 状态为 {manifest['status']}，不可驳回")
    manifest["status"] = "rejected"
    manifest["approved_by"] = getattr(principal, "user_id", None)
    manifest["approved_at"] = _now_iso()
    return _write_manifest(package_dir, manifest)


def approve_package(
    engine: Any,
    package_id: str,
    principal: Any,
    *,
    resources_dir: Path | None = None,
) -> dict[str, Any]:
    """EHS approval: promote files, seed, record the new release.

    File promotion is undone if the seed fails, so a failed approval never
    leaves half-promoted files behind. A seed that reports ``skipped``
    (identical content already released) applies without a new release.
    """
    _require_review(principal)
    package_dir = _package_dir(package_id)
    manifest = _read_manifest(package_dir)
    if manifest["status"] != "draft":
        raise UpdateError(f"更新包 {package_id} 状态为 {manifest['status']}，不可批准")
    resources = resources_dir or RESOURCES_DIR
    archive_dir = package_dir / "archive"
    archive_dir.mkdir(exist_ok=True)
    replaced: list[str] = []
    added: list[str] = []
    promoted: list[str] = []
    try:
        for name in manifest["files"]:
            target = resources / name
            if target.exists():
                shutil.copy2(target, archive_dir / name)
                replaced.append(name)
            else:
                added.append(name)
            shutil.copy2(package_dir / name, target)
            promoted.append(name)
        _snapshot_ctx_substances(engine, package_dir)
        result = seed_regulatory_db(
            engine,
            resources=resources,
            label=f"update-{package_id[:8]}",
            status="approved",
            extra_files=list(manifest["files"]),
        )
    except Exception as exc:
        for name in promoted:
            target = resources / name
            archived = archive_dir / name
            if archived.exists():
                shutil.copy2(archived, target)
            elif name in added:
                target.unlink(missing_ok=True)
        raise UpdateError(f"批准应用失败，已回滚文件：{exc}") from exc
    manifest["status"] = "approved"
    manifest["approved_by"] = getattr(principal, "user_id", None)
    manifest["approved_at"] = _now_iso()
    manifest["applied_release_id"] = result["release_id"]
    manifest["replaced_files"] = replaced
    manifest["added_files"] = added
    manifest["report"] = {"seed": result.get("stats", {})}
    _write_manifest(package_dir, manifest)
    # Tag the release with its package for rollback lookup — but not when
    # the seed skipped (content already released): retagging the prior
    # release would steal the original package's rollback pointer.
    if not result.get("skipped"):
        with engine.begin() as conn:
            row = conn.execute(
                select(RegulatoryDataRelease.id, RegulatoryDataRelease.stats).where(
                    RegulatoryDataRelease.id == result["release_id"]
                )
            ).first()
            if row is not None:
                stats = dict(row.stats or {})
                stats["package_id"] = package_id
                stats["replaced_files"] = replaced
                stats["added_files"] = added
                conn.execute(
                    update(RegulatoryDataRelease)
                    .where(RegulatoryDataRelease.id == row.id)
                    .values(stats=stats)
                )
    return {"manifest": manifest, "seed": result}


def _snapshot_ctx_substances(engine: Any, package_dir: Path) -> None:
    """Snapshot substance fields a CTX package is about to enrich (P1-1).

    The seed only ever fills missing substance fields, so a re-seed cannot
    undo a CTX merge. The snapshot (id, aliases, mf per affected CAS) is
    what rollback restores. Unparsable enrichment files merge nothing, so
    there is nothing to snapshot.
    """
    from sqlalchemy import select as _select

    from ....db.models_regulatory import SubstanceRow as _SubstanceRow

    ctx_path = package_dir / "ctx_enrichment.json"
    if not ctx_path.exists():
        return
    try:
        entries = json.loads(ctx_path.read_text(encoding="utf-8")).get("entries", {})
    except Exception:
        return
    if not isinstance(entries, dict):
        return
    from . import cas as _cas

    snapshot: list[dict[str, Any]] = []
    with engine.connect() as conn:
        for cas_raw in entries:
            cas_list = [c for c, _ in (_cas.repair(x) for x in _cas.split_many(cas_raw)) if c]
            for cas in cas_list:
                row = conn.execute(
                    _select(_SubstanceRow.id, _SubstanceRow.aliases, _SubstanceRow.mf)
                    .where(_SubstanceRow.cas_no == cas)
                ).first()
                if row is not None:
                    snapshot.append({"id": row.id, "aliases": row.aliases or [], "mf": row.mf})
                    break
    (package_dir / "snapshot.json").write_text(
        json.dumps({"substances": snapshot}, ensure_ascii=False), encoding="utf-8"
    )


def rollback_release(
    engine: Any,
    release_id: str,
    principal: Any,
    *,
    resources_dir: Path | None = None,
) -> dict[str, Any]:
    """Roll back an applied release: restore archived files, re-seed.

    Refuses when the live files no longer match the applied package (manual
    edits after approval) or when no archive exists — restoring blindly
    would destroy data. The re-seed creates a *new* approved release with
    the old data, so the rollback itself is traced and gated like any update.
    """
    _require_review(principal)
    with engine.begin() as conn:
        row = conn.execute(
            select(RegulatoryDataRelease.stats).where(RegulatoryDataRelease.id == release_id)
        ).first()
        if row is None:
            raise UpdateError(f"法规数据版本 {release_id} 不存在")
        stats = dict(row.stats or {})
    package_id = stats.get("package_id")
    if not package_id:
        raise UpdateError(f"版本 {release_id} 未记录更新包，无法回退（非更新包种子）")
    package_dir = _package_dir(package_id)
    manifest = _read_manifest(package_dir)
    archive_dir = package_dir / "archive"
    resources = resources_dir or RESOURCES_DIR
    for name in manifest.get("replaced_files", []):
        if not (archive_dir / name).exists():
            raise UpdateError(f"回退缺少归档文件 {name}，拒绝回退")
    # Every package file still on disk must match the approved version:
    # a manual edit after approval (whether the file replaced an older one
    # or was newly added) blocks automatic rollback.
    for name in manifest.get("files", []):
        if (resources / name).exists() and (resources / name).read_bytes() != (
            package_dir / name
        ).read_bytes():
            raise UpdateError(f"线上文件 {name} 在批准后被手动修改，拒绝自动回退")
    for name in manifest.get("replaced_files", []):
        shutil.copy2(archive_dir / name, resources / name)
    for name in manifest.get("added_files", []):
        (resources / name).unlink(missing_ok=True)
    # CLP rows from the rolled-back release are deleted outright (the
    # re-seed below only merges what the restored files contain, and a
    # skipped seed would change nothing at all — hence force=True).
    from sqlalchemy import delete as _delete

    from ....db.models_regulatory import (
        SubstanceClassificationRow as _ClsRow,
        SubstanceRow as _SubstanceRow,
    )

    with engine.begin() as conn:
        conn.execute(
            _delete(_ClsRow).where(
                _ClsRow.release_id == release_id,
                _ClsRow.source == "clp_annex_vi",
            )
        )
    result = seed_regulatory_db(
        engine,
        resources=resources,
        label=f"rollback-{release_id[:8]}",
        status="approved",
        extra_files=list(manifest["files"]),
        force=True,
    )
    # CTX enrichment is fill-only in the seed, so restore the snapshot
    # taken at apply time (after the re-seed, so it wins over re-merges).
    snapshot_path = package_dir / "snapshot.json"
    if snapshot_path.exists():
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8")).get("substances", [])
        except Exception:
            snapshot = []
        with engine.begin() as conn:
            for item in snapshot:
                conn.execute(
                    update(_SubstanceRow)
                    .where(_SubstanceRow.id == item["id"])
                    .values(aliases=item.get("aliases", []), mf=item.get("mf"))
                )
    # A skipped re-seed means the data already equals an older release: the
    # rollback landed there, and there is no new row to tag.
    if not result.get("skipped"):
        with engine.begin() as conn:
            row = conn.execute(
                select(RegulatoryDataRelease.id, RegulatoryDataRelease.stats).where(
                    RegulatoryDataRelease.id == result["release_id"]
                )
            ).first()
            if row is not None:
                new_stats = dict(row.stats or {})
                new_stats["rollback_of"] = release_id
                new_stats["package_id"] = package_id
                conn.execute(
                    update(RegulatoryDataRelease)
                    .where(RegulatoryDataRelease.id == row.id)
                    .values(stats=new_stats)
                )
    return {"release_id": result["release_id"], "seed": result}

"""Project workspace CRUD — NotebookLM-style persistent sessions."""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..db.project_store import get_project_store
from ..domain.project_workspace import (
    MigrateLocalRequest,
    ProjectCreateRequest,
    ProjectDetail,
    ProjectSummary,
    ProjectUpdateRequest,
    diff_spec_contract,
    record_contract_change,
)
from ..services import project_exports as exports_svc
from ._uploads import read_upload_capped

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ExportFileOut(BaseModel):
    name: str
    size: int
    updated_at: str
    content_type: str


class ExportTextBody(BaseModel):
    filename: str = Field(..., min_length=1, max_length=128)
    content: str = Field(..., max_length=5_000_000)
    content_type: str | None = None


@router.get("", response_model=list[ProjectSummary])
def list_projects() -> list[ProjectSummary]:
    return get_project_store().list_summaries()


@router.post("", response_model=ProjectDetail)
def create_project(req: ProjectCreateRequest) -> ProjectDetail:
    return get_project_store().create(title=req.title, requirement=req.requirement)


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project(project_id: str) -> ProjectDetail:
    detail = get_project_store().get(project_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return detail


@router.put("/{project_id}", response_model=ProjectDetail)
def update_project(
    project_id: str,
    req: ProjectUpdateRequest,
    allow_empty_sources: bool = Query(
        False, description="Explicitly allow an empty ``sources`` list to replace a non-empty one (same as clear_sources)"
    ),
) -> ProjectDetail:
    # 补齐计划 2-9: 契约变更通知下游 —— 更新前取旧 spec_contract，与请求中的新
    # spec_contract 对比；有变化则把通知注入本次请求（随合并一起落盘），下游
    # DOE/门禁看到未确认通知应重新评估。无 spec_contract 的请求不记通知。
    ws_req = req.workspace if isinstance(req.workspace, dict) else {}
    new_contract = ws_req.get("spec_contract")
    if isinstance(new_contract, dict):
        old_detail = get_project_store().get(project_id)
        old_contract = None
        if old_detail is not None and old_detail.workspace is not None:
            old_contract = old_detail.workspace.spec_contract or None
        changed = diff_spec_contract(old_contract, new_contract)
        if changed:
            old_notices = list((old_detail.workspace.contract_change_notices if old_detail is not None else []) or [])
            workspace_copy = dict(ws_req)
            workspace_copy["contract_change_notices"] = old_notices
            record_contract_change(workspace_copy, changed)
            req = ProjectUpdateRequest(
                workspace={**ws_req, "contract_change_notices": workspace_copy["contract_change_notices"]},
                title=req.title,
                clear_sources=req.clear_sources,
            )
    detail = get_project_store().update(
        project_id, req.workspace, title=req.title, allow_empty_sources=req.clear_sources or allow_empty_sources
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="Project not found")
    # Best-effort: promote requirement.materials into the global catalog.
    try:
        from ..services.material_promote import safe_propose_from_requirement

        ws = req.workspace if isinstance(req.workspace, dict) else {}
        req_blob = ws.get("requirement")
        if req_blob is None and detail.workspace and detail.workspace.requirement:
            req_blob = detail.workspace.requirement
        if req_blob is not None:
            safe_propose_from_requirement(req_blob, source_ref=f"project:{project_id}")
    except Exception:
        pass
    # P4.2: optional dossier auto-patch (default OFF via wiki_dossier_auto_patch).
    try:
        from ..services.wiki.dossier import notify_dossier_event

        notify_dossier_event(project_id, "project_updated")
    except Exception:
        pass
    return detail


class AcknowledgeContractChangesBody(BaseModel):
    """确认契约变更通知：at 为空 → 确认全部未确认通知；否则只确认该 at 的一条。"""
    at: str | None = None


@router.post("/{project_id}/contract-change-notices/acknowledge")
def acknowledge_contract_changes(project_id: str, body: AcknowledgeContractChangesBody) -> dict:
    """补齐计划 2-9: 下游（DOE/门禁）确认已看到契约变更并重新评估。

    通知本身不删除（审计留痕），只置 acknowledged=true + acknowledged_at。
    """
    from ..clock import utcnow

    detail = get_project_store().get(project_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Project not found")
    notices = [dict(n) for n in (detail.workspace.contract_change_notices or [])]
    acknowledged = 0
    now = utcnow().isoformat()
    for n in notices:
        if n.get("acknowledged"):
            continue
        if body.at is not None and str(n.get("at")) != body.at:
            continue
        n["acknowledged"] = True
        n["acknowledged_at"] = now
        acknowledged += 1
    if acknowledged:
        updated = get_project_store().update(project_id, {"contract_change_notices": notices})
        if updated is None:
            raise HTTPException(status_code=404, detail="Project not found")
    return {"ok": True, "acknowledged": acknowledged, "pending": sum(1 for n in notices if not n.get("acknowledged"))}


@router.delete("/{project_id}")
def delete_project(
    project_id: str,
    knowledge: str = Query(default="delete", pattern="^(delete|global)$"),
) -> dict:
    """Delete a project. ``knowledge=global`` keeps its KB as global; ``delete`` removes all."""
    result = get_project_store().delete(project_id, knowledge=knowledge)
    if result is None:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        n = exports_svc.purge_project_exports(project_id)
        result = {**result, "exports_removed": n}
    except Exception:
        pass
    return {"ok": True, **result}


@router.get("/{project_id}/history")
def project_payload_history(project_id: str, limit: int = Query(default=20, ge=1, le=100)) -> dict:
    """Payload 版本审计(2026-09-05): 每次 update 前快照, 支持回滚。"""
    detail = get_project_store().get(project_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Project not found")
    versions = get_project_store().list_payload_history(project_id, limit=limit)
    return {"project_id": project_id, "versions": versions}


@router.post("/{project_id}/rollback/{version}", response_model=ProjectDetail)
def rollback_project(project_id: str, version: int) -> ProjectDetail:
    """回滚 payload 到指定历史版本(当前值先快照, 再恢复目标)。"""
    try:
        detail = get_project_store().rollback_payload(project_id, version)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"version {version} not found")
    if detail is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return detail


@router.get("/{project_id}/db-stats")
def project_db_stats(project_id: str) -> dict:
    """Real database counts for a project (docs / campaigns / experiments)."""
    return {"project_id": project_id, **get_project_store().db_stats(project_id)}


@router.post("/migrate-local", response_model=list[ProjectSummary])
def migrate_local(req: MigrateLocalRequest) -> list[ProjectSummary]:
    if not req.snapshots:
        return []
    return get_project_store().migrate_legacy(req.snapshots)


# ── Dim-4: project export file shelf ─────────────────────────────────────────


@router.get("/{project_id}/exports", response_model=list[ExportFileOut])
def list_project_exports(project_id: str) -> list[ExportFileOut]:
    return [
        ExportFileOut(
            name=f.name,
            size=f.size,
            updated_at=f.updated_at,
            content_type=f.content_type,
        )
        for f in exports_svc.list_exports(project_id)
    ]


@router.post("/{project_id}/exports", response_model=ExportFileOut)
def save_project_export(project_id: str, body: ExportTextBody) -> ExportFileOut:
    """Save UTF-8 text (CSV / JSON / Markdown) into the project export shelf."""
    info = exports_svc.save_export_bytes(project_id, body.filename, body.content.encode("utf-8"))
    return ExportFileOut(
        name=info.name,
        size=info.size,
        updated_at=info.updated_at,
        content_type=info.content_type,
    )


@router.post("/{project_id}/exports/upload", response_model=ExportFileOut)
async def upload_project_export(
    project_id: str,
    file: UploadFile = File(...),
    filename: str | None = Form(default=None),
) -> ExportFileOut:
    """Multipart binary upload (PDF / XLSX) into the project export shelf."""
    name = filename or file.filename or "export.bin"
    raw = await read_upload_capped(file, name, limit=exports_svc.MAX_EXPORT_BYTES)
    info = await run_in_threadpool(exports_svc.save_export_bytes, project_id, name, raw)
    return ExportFileOut(
        name=info.name,
        size=info.size,
        updated_at=info.updated_at,
        content_type=info.content_type,
    )


@router.get("/{project_id}/exports/{filename}")
def download_project_export(project_id: str, filename: str) -> FileResponse:
    path = exports_svc.resolve_export_path(project_id, filename)
    return FileResponse(
        path,
        filename=path.name,
        media_type=exports_svc.guess_content_type(path.name),
    )


@router.delete("/{project_id}/exports/{filename}")
def delete_project_export(project_id: str, filename: str) -> dict:
    exports_svc.delete_export(project_id, filename)
    return {"ok": True, "filename": filename}

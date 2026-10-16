"""Release state machine and four-step sign-off (方案 §5.5.10).

Revision states::

    draft ──(项目负责人钦定)──► candidate ──(门禁通过 + 提交)──► under_review
      ▲                           ▲                                │ 研发 → EHS/法规 → 质量 → 发布管理员
      │                           └──────────── 任一驳回 ◄──────────┤
      │                                                            ▼
      │                                      released ──(新修订发布)──► superseded
      │                                         └──(撤回)──► withdrawn

Every transition is audited; every sign-off step is an e-signature over the
package hash. A user signs at most one step of a flow (segregation of duties),
must hold the step's role (globally or in the product's project) and the
permission for it, and re-enters the password.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ....clock import utcnow
from ....db.models_identity import ProjectMemberRow, UserRoleRow, UserRow
from ....db.models_plm import (
    ApprovalFlowRow,
    ApprovalStepRow,
    ESignatureRow,
    ProductRevisionRow,
    ProductRow,
    RegulatoryDocumentRow,
    ReleaseGateWaiverRow,
    ReleasePackageRow,
)
from ...audit import hash_chain
from ...connectors.base import DeliveryPackage, configured_connectors
from ...identity.rbac import RELEASE_SIGNOFF_ROLES, Principal
from .. import storage
from . import esign, gate, package

FLOW_TEMPLATE = "release_4_step"
OPEN_PACKAGE_STATES = ("under_review",)


class ReleaseError(ValueError):
    """A request that is not valid in the current state (HTTP 409 / 422)."""


class Forbidden(PermissionError):
    pass


class GateFailed(ReleaseError):
    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__("发布门禁未通过")
        self.result = result


def _require(principal: Principal, permission: str, project_id: str | None) -> None:
    if not principal.can(permission, project_id):
        raise Forbidden(f"缺少权限 {permission}")


def _rev(session: Session, revision_id: str) -> tuple[ProductRevisionRow, ProductRow]:
    rev = session.get(ProductRevisionRow, revision_id)
    if rev is None:
        raise ReleaseError("产品修订版不存在")
    return rev, session.get(ProductRow, rev.product_id)


def _audit(session: Session, actor: str, action: str, entity_type: str, entity_id: str, **detail: Any) -> None:
    hash_chain.append(session, actor=actor, action=action, entity_type=entity_type, entity_id=entity_id, detail=detail)


# --------------------------------------------------------------------------- documents


def approve_document(session: Session, document_id: str, *, user_id: str, principal: Principal, password: str,
                     comment: str = "") -> RegulatoryDocumentRow:
    """Review sign-off of one document (draft → approved); COA approval is its issue."""
    doc = session.get(RegulatoryDocumentRow, document_id)
    if doc is None:
        raise ReleaseError("文档不存在")
    rev, product = _rev(session, doc.revision_id)
    _require(principal, "coa.sign" if doc.doc_type == "coa" else "plm.document.review", product.project_id)
    if doc.status != "draft":
        raise ReleaseError(f"文档状态为 {doc.status}，只能审核草稿")
    newer = session.execute(select(RegulatoryDocumentRow.id).where(
        RegulatoryDocumentRow.revision_id == doc.revision_id, RegulatoryDocumentRow.doc_type == doc.doc_type,
        RegulatoryDocumentRow.lang == doc.lang, RegulatoryDocumentRow.version_no > doc.version_no,
        *([RegulatoryDocumentRow.batch_id == doc.batch_id] if doc.batch_id else []))).first()
    if newer:
        raise ReleaseError("该文档已有更新版本，请审核最新版本")
    missing = (doc.content.get("validation") or {}).get("missing") or []
    if missing:
        raise ReleaseError(f"文档有缺项，不能审核通过：{'、'.join(missing)}")
    if doc.doc_type == "coa" and (doc.content.get("meta") or {}).get("overall") != "PASS":
        raise ReleaseError("COA 结论不是“合格”，不能签发")
    if doc.created_by and doc.created_by == user_id:
        raise Forbidden("编制人不能审核自己生成的文档")
    esign.sign(session, user_id=user_id, password=password, meaning="review", subject_type="regulatory_document",
               subject_id=doc.id, object_hash=doc.content_hash, detail={"comment": comment})
    doc.status = "approved"
    _audit(session, user_id, "document.approve", "regulatory_document", doc.id, doc_type=doc.doc_type,
           lang=doc.lang, version_no=doc.version_no)
    return doc


# --------------------------------------------------------------------------- revision transitions


def mark_candidate(session: Session, revision_id: str, *, user_id: str, principal: Principal) -> ProductRevisionRow:
    rev, product = _rev(session, revision_id)
    _require(principal, "formulation.freeze", product.project_id)
    if rev.state not in ("draft",):
        raise ReleaseError(f"修订版状态为 {rev.state}，只有草稿可以钦定为候选")
    if not rev.formulation_version_id:
        raise ReleaseError("修订版未关联配方版本")
    rev.state = "candidate"
    if product.lifecycle_state in ("concept", "rd"):
        product.lifecycle_state = "candidate"
    _audit(session, user_id, "revision.candidate", "product_revision", rev.id)
    return rev


def add_waiver(session: Session, revision_id: str, *, check_key: str, item: str, reason: str, user_id: str,
               principal: Principal, password: str) -> ReleaseGateWaiverRow:
    rev, product = _rev(session, revision_id)
    if check_key not in gate.WAIVABLE:
        raise ReleaseError(f"门禁项 {check_key} 不可豁免")
    _require(principal, gate.WAIVABLE[check_key], product.project_id)
    if rev.state not in ("draft", "candidate"):
        raise ReleaseError("修订版已提交或发布，不能再添加豁免")
    if not reason.strip():
        raise ReleaseError("豁免必须说明理由")
    result = gate.evaluate(session, rev.id)
    check = next(c for c in result["checks"] if c["key"] == check_key)
    target = next((i for i in check["items"] if i["key"] == item), None)
    if target is None:
        raise ReleaseError("该门禁项当前没有这一条失败记录")
    if not target["waivable"]:
        raise ReleaseError("该条为阻断项，不能豁免")
    if target["waived"]:
        raise ReleaseError("该条已豁免")
    sig = esign.sign(session, user_id=user_id, password=password, meaning="waive", subject_type="product_revision",
                     subject_id=rev.id, object_hash=gate.item_key(check_key, item, reason),
                     detail={"check": check_key, "item": item, "reason": reason})
    row = ReleaseGateWaiverRow(id=str(uuid.uuid4()), revision_id=rev.id, check_code=check_key, item_key=item,
                               reason=reason.strip(), waived_by=user_id, signature_id=sig.id)
    session.add(row)
    session.flush()
    return row


def submit(session: Session, revision_id: str, *, user_id: str, principal: Principal) -> ReleasePackageRow:
    """Run the gate; when it passes freeze the package and open the 4-step sign-off."""
    rev, product = _rev(session, revision_id)
    _require(principal, "release.submit", product.project_id)
    if rev.state != "candidate":
        raise ReleaseError(f"修订版状态为 {rev.state}，只有候选版可以提交发布")
    result = gate.evaluate(session, rev.id)
    if not result["passed"]:
        _audit(session, user_id, "release.gate_failed", "product_revision", rev.id,
               failed=[c["code"] for c in result["checks"] if c["status"] == "fail"])
        raise GateFailed(result)
    docs, _problems = gate.select_package_documents(session, rev)
    manifest = package.build_manifest(session, rev, docs, gate.summary(result))
    phash = package.package_hash(manifest)
    pkg = ReleasePackageRow(id=str(uuid.uuid4()), revision_id=rev.id, gate_result={"result": result, "manifest": manifest},
                            document_ids=[d.id for d in docs], package_hash=phash, status="under_review",
                            created_by=user_id)
    session.add(pkg)
    flow = ApprovalFlowRow(id=str(uuid.uuid4()), subject_type="release_package", subject_id=pkg.id,
                           template=FLOW_TEMPLATE, status="pending", current_step=1, created_by=user_id)
    session.add(flow)
    session.flush()
    for seq, role in enumerate(RELEASE_SIGNOFF_ROLES, start=1):
        session.add(ApprovalStepRow(id=str(uuid.uuid4()), flow_id=flow.id, seq=seq, role_code=role))
    rev.state = "under_review"
    session.flush()
    _audit(session, user_id, "release.submit", "release_package", pkg.id, revision_id=rev.id, package_hash=phash)
    return pkg


def _flow(session: Session, package_id: str) -> tuple[ApprovalFlowRow, list[ApprovalStepRow]]:
    flow = session.execute(select(ApprovalFlowRow).where(
        ApprovalFlowRow.subject_type == "release_package", ApprovalFlowRow.subject_id == package_id)
        .order_by(ApprovalFlowRow.created_at.desc()).limit(1)).scalar_one_or_none()
    if flow is None:
        raise ReleaseError("没有签批流程")
    steps = session.execute(select(ApprovalStepRow).where(ApprovalStepRow.flow_id == flow.id)
                            .order_by(ApprovalStepRow.seq)).scalars().all()
    return flow, list(steps)


def _holds_role(session: Session, user_id: str, role: str, project_id: str | None) -> bool:
    if session.execute(select(UserRoleRow).where(UserRoleRow.user_id == user_id,
                                                 UserRoleRow.role_code == role)).first():
        return True
    return bool(project_id and session.execute(select(ProjectMemberRow).where(
        ProjectMemberRow.user_id == user_id, ProjectMemberRow.project_id == project_id,
        ProjectMemberRow.role_code == role)).first())


def sign_step(session: Session, package_id: str, *, decision: str, user_id: str, principal: Principal, password: str,
              comment: str = "") -> ReleasePackageRow:
    pkg = session.get(ReleasePackageRow, package_id)
    if pkg is None:
        raise ReleaseError("发布包不存在")
    if pkg.status != "under_review":
        raise ReleaseError(f"发布包状态为 {pkg.status}，不在签批中")
    if decision not in ("approve", "reject"):
        raise ReleaseError("decision 只能是 approve / reject")
    rev, product = _rev(session, pkg.revision_id)
    flow, steps = _flow(session, pkg.id)
    step = next((s for s in steps if s.seq == flow.current_step), None)
    if step is None or step.status != "pending":
        raise ReleaseError("没有待签的步骤")
    last = step.seq == len(steps)
    if not _holds_role(session, user_id, step.role_code, product.project_id):
        raise Forbidden(f"第 {step.seq} 步需由角色 {step.role_code} 签批")
    _require(principal, "release.publish" if last and decision == "approve" else "release.sign", product.project_id)
    if any(s.decided_by == user_id for s in steps if s.id != step.id):
        raise Forbidden("同一人不能在一个发布流程中签批两个步骤")
    if decision == "reject" and not comment.strip():
        raise ReleaseError("驳回必须填写意见")
    tampered = package.verify_files(session, pkg.gate_result["manifest"])
    if tampered:
        raise ReleaseError("发布包内容与冻结时不一致：" + "；".join(tampered))
    meaning = "reject" if decision == "reject" else ("release" if last else "approve")
    esign.sign(session, user_id=user_id, password=password, meaning=meaning, subject_type="release_package",
               subject_id=pkg.id, object_hash=pkg.package_hash, step_id=step.id,
               detail={"step": step.seq, "role": step.role_code, "comment": comment})
    now = utcnow()
    step.status = "approved" if decision == "approve" else "rejected"
    step.decided_by = user_id
    step.decided_at = now
    step.comment = comment
    if decision == "reject":
        flow.status = "rejected"
        flow.closed_at = now
        pkg.status = "rejected"
        rev.state = "candidate"
        _audit(session, user_id, "release.reject", "release_package", pkg.id, step=step.seq, comment=comment)
        return pkg
    _audit(session, user_id, "release.sign", "release_package", pkg.id, step=step.seq, role=step.role_code)
    if not last:
        flow.current_step = step.seq + 1
        return pkg
    flow.status = "approved"
    flow.closed_at = now
    _release(session, pkg, rev, product, user_id)
    return pkg


def _release(session: Session, pkg: ReleasePackageRow, rev: ProductRevisionRow, product: ProductRow, user_id: str) -> None:
    now = utcnow()
    # The previously released revision of this product (if any) is superseded.
    for old in session.execute(select(ProductRevisionRow).where(
            ProductRevisionRow.product_id == product.id, ProductRevisionRow.state == "released",
            ProductRevisionRow.id != rev.id)).scalars():
        old.state = "superseded"
        for d in session.execute(select(RegulatoryDocumentRow).where(
                RegulatoryDocumentRow.revision_id == old.id, RegulatoryDocumentRow.status == "released")).scalars():
            d.status = "superseded"
        _audit(session, user_id, "revision.superseded", "product_revision", old.id, by=rev.id)
    rev.state = "released"
    rev.frozen_at = now
    product.lifecycle_state = "released"
    for d in session.execute(select(RegulatoryDocumentRow).where(
            RegulatoryDocumentRow.id.in_(pkg.document_ids))).scalars():
        d.status = "released"
    pkg.status = "released"
    pkg.released_at = now
    zip_bytes = package.build_zip(session, pkg.gate_result["manifest"], pkg.package_hash, signatures(session, pkg.id))
    pkg.delivery_key = storage.put(f"packages/{pkg.id}/{product.code}-{rev.rev}.zip", zip_bytes)
    _audit(session, user_id, "release.publish", "release_package", pkg.id, revision_id=rev.id,
           package_hash=pkg.package_hash)


def withdraw(session: Session, package_id: str, *, reason: str, user_id: str, principal: Principal,
             password: str) -> ReleasePackageRow:
    pkg = session.get(ReleasePackageRow, package_id)
    if pkg is None:
        raise ReleaseError("发布包不存在")
    rev, product = _rev(session, pkg.revision_id)
    _require(principal, "release.withdraw", product.project_id)
    if pkg.status != "released":
        raise ReleaseError("只能撤回已发布的发布包")
    if not reason.strip():
        raise ReleaseError("撤回必须说明理由")
    esign.sign(session, user_id=user_id, password=password, meaning="withdraw", subject_type="release_package",
               subject_id=pkg.id, object_hash=pkg.package_hash, detail={"reason": reason})
    pkg.status = "withdrawn"
    rev.state = "withdrawn"
    for d in session.execute(select(RegulatoryDocumentRow).where(
            RegulatoryDocumentRow.id.in_(pkg.document_ids))).scalars():
        d.status = "withdrawn"
    if not session.execute(select(ProductRevisionRow.id).where(
            ProductRevisionRow.product_id == product.id, ProductRevisionRow.state == "released")).first():
        product.lifecycle_state = "withdrawn"
    _audit(session, user_id, "release.withdraw", "release_package", pkg.id, reason=reason)
    return pkg


# --------------------------------------------------------------------------- read models & connectors


def signatures(session: Session, package_id: str) -> list[dict[str, Any]]:
    rows = session.execute(select(ESignatureRow).where(
        ESignatureRow.subject_type == "release_package", ESignatureRow.subject_id == package_id)
        .order_by(ESignatureRow.signed_at)).scalars().all()
    steps = {s.id: s for s in session.execute(select(ApprovalStepRow).where(
        ApprovalStepRow.id.in_([r.step_id for r in rows if r.step_id]))).scalars()} if rows else {}
    users = {u.id: u for u in session.execute(select(UserRow).where(
        UserRow.id.in_([r.user_id for r in rows]))).scalars()} if rows else {}
    out = []
    for r in rows:
        st = steps.get(r.step_id)
        u = users.get(r.user_id)
        out.append({"id": r.id, "step": st.seq if st else None, "role": st.role_code if st else None,
                    "user_id": r.user_id, "username": u.username if u else None,
                    "display_name": u.display_name if u else None, "meaning": r.meaning,
                    "meaning_zh": esign.MEANINGS.get(r.meaning), "signed_at": r.signed_at.isoformat(),
                    "signed_hash": r.signed_hash})
    return out


def package_dict(session: Session, pkg: ReleasePackageRow) -> dict[str, Any]:
    flow_steps = []
    flow_status = None
    try:
        flow, steps = _flow(session, pkg.id)
        flow_status = flow.status
        flow_steps = [{"seq": s.seq, "role": s.role_code, "status": s.status, "decided_by": s.decided_by,
                       "decided_at": s.decided_at.isoformat() if s.decided_at else None, "comment": s.comment,
                       "current": flow.status == "pending" and s.seq == flow.current_step} for s in steps]
    except ReleaseError:
        pass
    sigs = signatures(session, pkg.id)
    valid = all(esign.verify(session.get(ESignatureRow, s["id"]), pkg.package_hash) for s in sigs)
    return {"id": pkg.id, "revision_id": pkg.revision_id, "status": pkg.status, "package_hash": pkg.package_hash,
            "document_ids": pkg.document_ids, "created_by": pkg.created_by,
            "created_at": pkg.created_at.isoformat() if pkg.created_at else None,
            "released_at": pkg.released_at.isoformat() if pkg.released_at else None,
            "has_delivery": bool(pkg.delivery_key), "publish_results": pkg.publish_results or [],
            "flow": {"status": flow_status, "steps": flow_steps}, "signatures": sigs, "signatures_valid": valid,
            "gate": (pkg.gate_result or {}).get("result")}


def _delivery(session: Session, pkg: ReleasePackageRow) -> DeliveryPackage:
    rev, product = _rev(session, pkg.revision_id)
    return DeliveryPackage(package_id=pkg.id, product_code=product.code, revision=rev.rev,
                           package_hash=pkg.package_hash, manifest=pkg.gate_result["manifest"],
                           zip_bytes=storage.get(pkg.delivery_key) if pkg.delivery_key else None)


def run_connectors(session: Session, package_id: str, *, action: str = "publish", reason: str = "",
                   connectors=None) -> list[dict[str, Any]]:
    """Push a released (or withdrawn) package to every configured connector; call after committing the
    state change. Outcomes are appended to ``publish_results`` (the caller commits)."""
    pkg = session.get(ReleasePackageRow, package_id)
    if pkg is None:
        raise ReleaseError("发布包不存在")
    delivery = _delivery(session, pkg)
    results = []
    for c in configured_connectors() if connectors is None else connectors:
        try:
            r = c.publish(delivery) if action == "publish" else c.withdraw(delivery, reason)
            results.append({**r.to_dict(), "at": utcnow().isoformat()})
        except Exception as exc:  # noqa: BLE001 — a connector must never undo a release
            results.append({"connector": getattr(c, "name", "?"), "action": action, "ok": False,
                            "detail": f"{type(exc).__name__}: {exc}", "at": utcnow().isoformat()})
    if results:
        pkg.publish_results = [*(pkg.publish_results or []), *results]
        _audit(session, "system", f"connector.{action}", "release_package", pkg.id,
               results=[{"connector": r["connector"], "ok": r["ok"]} for r in results])
    return results

"""Research tasks: deep research, recommendation, inverse design."""
from __future__ import annotations

from ...domain.schemas import Requirement
from ..celery_app import celery_app
from ..task_progress import TaskProgressStatus
from ..task_progress import ThinkingTracker
from ..task_progress import persist_result
from ..task_progress import publish_progress
import uuid

from ._common import _OutboxTask, _persist_terminal, _thinking_title, logger
from .kb import dispatch_kb_ingest



@celery_app.task(
    bind=True,
    base=_OutboxTask,
    outbox_operation="research_deep",
    name="formunex.deep_research",
)
def run_deep_research_task(self, payload: dict) -> dict:
    from ...domain.schemas import ComprehensiveReport, Evidence, Requirement
    from ...pipeline.research_graph import run_research_graph

    task_id = self.request.id
    tracker = ThinkingTracker(task_id, kind="deep_research")
    tracker.emit(
        "retrieve",
        "正在检索",
        progress=0.05,
        step_id="retrieve",
        title="正在检索知识库与证据…",
    )
    try:
        req = Requirement(**payload["requirement"]) if payload.get("requirement") else None
        topic = payload.get("topic") or (req.headline() if req else "")
        query = payload.get("query") or topic
        sources = [Evidence.model_validate(s) for s in payload.get("sources") or []]

        def graph_progress(stage: str, message: str, partial: dict | None = None) -> None:
            tracker.emit(
                stage,
                message,
                progress=_stage_progress(stage),
                step_id=stage or None,
                title=_thinking_title(stage, message),
                kind="stage",
                detail=message,
                data=partial,
            )

        state = run_research_graph(
            topic=topic,
            req=req,
            query=query,
            pre_index=sources or None,
            progress_cb=graph_progress,
            mode="deep",
        )
        grounded = state.get("grounded_evidence") or []
        report = ComprehensiveReport(
            topic=topic,
            report_markdown=state.get("report_markdown") or state.get("answer") or "",
            citations=state.get("citations") or grounded,
            candidates=state.get("recommended") or [],
            web_count=0,
            kb_count=len(grounded),
            engine=state.get("recommend_engine") or "offline",
            verified_claims=state.get("verified_claims") or [],
            claim_check_engine=state.get("claim_check_engine") or "offline",
            claim_check_pass_rate=float(state.get("claim_check_pass_rate") or 1.0),
        )
        result = {
            "report": report.model_dump(),
            "grounded_evidence": [e.model_dump() for e in grounded],
            # P1-1: deep 模式 generate_node 已 register_round 并写
            # state["recommend_id"]，透传出来否则前端采不到。
            "recommend_id": state.get("recommend_id"),
        }
        kb_task_id = dispatch_kb_ingest(
            result["grounded_evidence"],
            project_id=(req.project_id if req else None),
            query=query,
            domain=getattr(req, "domain", None) if req else None,
        )
        if kb_task_id:
            result["kb_ingest_task_id"] = kb_task_id
        tracker.finish()
        persist_result(task_id, result, failed=False)
        _persist_terminal(task_id, "deep_research", result)
        return result
    except Exception as exc:
        logger.exception("deep_research task failed")
        tracker.finish(error=True)
        err = {"error": str(exc)}
        persist_result(task_id, err, failed=True)
        _persist_terminal(task_id, "deep_research", err, failed=True, message=str(exc))
        raise



def _stage_progress(stage: str) -> float:
    return {
        "retrieve": 0.2,
        "grade": 0.45,
        "fallback": 0.55,
        "generate": 0.7,
        "claim_check": 0.82,
        "regenerate": 0.86,
        "recommend": 0.9,
    }.get(stage, 0.5)



def _convert_formulas(formulas: list, scored: list) -> list[dict]:
    """Convert RecommendedFormula (components) → Formulation (ingredients)."""
    from ...domain.schemas import Formulation as _F, Ingredient, ProductDomain

    result = []
    for raw in formulas:
        domain = raw.get("domain", "anticorrosion_coating")
        if isinstance(domain, str):
            try:
                domain = ProductDomain(domain)
            except ValueError:
                domain = ProductDomain.anticorrosion_coating
        result.append(_F(
            name=raw.get("name", ""),
            domain=domain,
            ingredients=[
                Ingredient(
                    name=c.get("name") or "",
                    zh_name=c.get("zh_name"),
                    role=c.get("component_type") or "",
                    weight_pct=c.get("weight_pct") or 0,
                    cas_no=c.get("cas_no"),
                    formula=c.get("mf"),
                    smiles=c.get("smiles"),
                    molar_mass=c.get("molar_mass"),
                    equivalents=c.get("equivalents"),
                    mmol=c.get("mmol"),
                    notes=c.get("notes"),
                    evidence_refs=c.get("evidence_refs") or [],
                    grounding_confidence=c.get("grounding_confidence"),
                )
                for c in raw.get("components") or []
            ],
            rationale=raw.get("rationale", ""),
            source="recommend",
            predicted=raw.get("predicted") or {},
            predicted_std=raw.get("predicted_std") or {},
            score=raw.get("score"),
            warnings=raw.get("warnings") or [],
        ).model_dump())
    return result



@celery_app.task(
    bind=True,
    base=_OutboxTask,
    outbox_operation="research_recommend",
    name="formunex.recommend",
)
def run_recommend_task(self, payload: dict) -> dict:
    from ...api.formulations import recommend_formulations as _sync_recommend
    from ...api.formulations import RecommendFormulationsRequest as SyncRequest

    task_id = self.request.id
    tracker = ThinkingTracker(task_id, kind="recommend")
    tracker.emit(
        "retrieve",
        "正在检索配方证据…",
        progress=0.15,
        step_id="retrieve",
        title="正在检索表面活性剂库与知识库…",
        detail="BM25 / 向量混合召回候选片段",
    )
    try:
        req_data = payload.get("requirement") or {}
        if not req_data:
            raise ValueError("requirement is required")
        n = payload.get("n") or 3
        sources = payload.get("sources") or []

        tracker.emit(
            "grade",
            "正在评估证据相关性…",
            progress=0.45,
            step_id="grade",
            title="正在评估证据相关性（CRAG）…",
            detail="过滤低相关片段后再进入推荐",
        )

        body = SyncRequest(
            requirement=req_data,
            n=n,
            sources=sources,
            prefer_materials_catalog=bool(payload.get("prefer_materials_catalog")),
            relation_insight=bool(payload.get("relation_insight")),
        )
        tracker.emit(
            "recommend",
            "正在生成推荐配方…",
            progress=0.75,
            step_id="recommend",
            title="正在生成推荐配方…",
            detail="LLM / 规则引擎产出 Top-N 配方",
        )
        resp = _sync_recommend(body)
        result_raw = resp.model_dump()

        # Convert RecommendedFormula → Formulation for frontend compat
        recommended = _convert_formulas(result_raw.get("formulas") or [],
                                         result_raw.get("scored") or [])

        # Build ResearchResult-format response expected by frontend.
        # The headline is derived from the requirement, not produced by the
        # engine, so the lightweight path has no reason to return it empty —
        # doing so left the UI showing a blank title for a completed run.
        research = {
            "requirement_headline": body.requirement.headline(),
            "evidence": result_raw.get("grounded_evidence") or [],
            "mechanism": "",
            "recommended": recommended,
            "chat_markdown": "",
            "recommend_engine": result_raw.get("engine", "llm"),
            "tradeoff": result_raw.get("tradeoff"),
            "recommend_meta": {"warnings": result_raw.get("warnings") or []},
            # P1-1: 异步路径透传 recommend_id —— 否则前端 lastRecommendId
            # 恒为 null，采纳按钮静默禁用，C-8 分子永远采不到。
            "recommend_id": result_raw.get("recommend_id"),
            # KG relation insights (only computed when the payload asked for relation_insight).
            "relation_insights": result_raw.get("relation_insights") or [],
        }
        result = {"research": research}

        # KB ingest
        grounded = result_raw.get("grounded_evidence") or []
        if grounded:
            dispatch_kb_ingest(grounded)

        tracker.thought(
            f"已生成 {len(recommended)} 条推荐配方",
            stage="recommend",
            message="推荐完成",
            progress=0.95,
            detail=f"engine={result_raw.get('engine', 'llm')}",
        )
        tracker.finish()
        persist_result(task_id, result, failed=False)
        _persist_terminal(task_id, "recommend", result)
        return result
    except Exception as exc:
        logger.exception("recommend task failed")
        tracker.finish(error=True)
        err = {"error": str(exc)}
        persist_result(task_id, err, failed=True)
        _persist_terminal(task_id, "recommend", err, failed=True, message=str(exc))
        raise



@celery_app.task(
    bind=True,
    base=_OutboxTask,
    outbox_operation="inverse_design",
    name="formunex.inverse_design",
)
def run_inverse_design_task(self, payload: dict) -> dict:
    return run_inverse_design_impl(self.request.id, payload)



def _register_inverse_design_round(payload: dict) -> str | None:
    """v9: 逆向设计注册推荐轮次（C-8 分母），fail-open。

    返回 recommend_id 字符串（可 JSON 序列化），失败返回 None。
    抽成独立函数以便单测直接断言调用签名 —— v8 曾因签名错误导致
    整个修复是 no-op（TypeError 被静默吞掉）。
    """
    # v8 曾用错签名：register_round(session, *, recommend_id, project_id=None)
    try:
        from ...db import recommend_outcome_store
        from ...db.database import default_session_factory
        from ...db.session_utils import commit_session

        rec_id = f"inv-{uuid.uuid4().hex[:12]}"
        req = payload.get("requirement") or {}
        pid = (req.get("project_id") or "").strip() or None
        with commit_session(default_session_factory()) as session:
            recommend_outcome_store.register_round(
                session,
                recommend_id=rec_id,
                project_id=pid,
            )
        return rec_id
    except Exception as exc:  # noqa: BLE001 - fail-open，不阻断主流程
        logger.warning("inverse design round registration failed (fail-open): %s", exc)
        return None



def run_inverse_design_impl(task_id: str, payload: dict) -> dict:
    from ...domain.schemas import TargetSpec
    from ...services import inverse_design

    publish_progress(task_id, TaskProgressStatus.RUNNING, message="starting inverse design")

    try:
        # req/targets 构造在 try 内：payload 校验失败也要走失败持久化。
        req = Requirement(**payload["requirement"])
        targets = TargetSpec.model_validate(payload.get("targets") or {})

        def progress(p: float, msg: str) -> None:
            publish_progress(
                task_id,
                TaskProgressStatus.RUNNING,
                message=msg,
                progress=round(p, 3),
            )

        result = inverse_design.design(
            req,
            targets,
            population=payload.get("population") or 48,
            generations=payload.get("generations") or 30,
            seed_with_llm=payload.get("seed_with_llm", True),
            seed=payload.get("seed", 42),
            progress_cb=progress,
        )
        data = result.model_dump()
        # v9: 逆向设计同样注册推荐轮次，否则采纳遥测漏掉整条路径。
        _rec_id = _register_inverse_design_round(payload)
        if _rec_id:
            data["recommend_id"] = _rec_id  # 字符串，json 可序列化
        persist_result(task_id, data, failed=False)
        _persist_terminal(task_id, "inverse_design", data)
        return data
    except Exception as exc:
        err = {"error": str(exc)}
        persist_result(task_id, err, failed=True)
        _persist_terminal(task_id, "inverse_design", err, failed=True, message=str(exc))
        raise

deep_research_task = run_deep_research_task

recommend_task = run_recommend_task

ingest_patents_task = run_deep_research_task  # unused alias

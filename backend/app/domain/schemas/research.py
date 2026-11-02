"""Research DTOs: results, reports, IP, agent findings, review verdicts."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel
from pydantic import Field

from .chat import Evidence
from .doe import DOEPlan, Formulation, Requirement
from ..tradeoff_schemas import TradeOffAnalysis, VerificationDoe


class ResearchResult(BaseModel):
    requirement_headline: str
    evidence: list[Evidence]
    mechanism: str
    recommended: list[Formulation]
    chat_markdown: str
    recommend_engine: Literal["llm", "offline"] = "offline"
    tradeoff: TradeOffAnalysis | None = None
    recommend_meta: dict | None = None
    # F-4: C-8 round id — lets the /api/research entry path report adopt signals.
    recommend_id: str | None = None
    # KG relation insights of the round (component → substitutes / relations), produced by the recommend path
    # with ``relation_insight``; kept in the project workspace's research block for the leaderboard banner.
    relation_insights: list[dict] = Field(default_factory=list)

class ComprehensiveReport(BaseModel):
    """Output of the DeepResearchEngine deep-research orchestrator.

    A citation-grounded research report synthesised by cross-validating web and
    knowledge-base evidence, plus candidate formulations for the topic.
    """

    topic: str
    report_markdown: str
    citations: list[Evidence] = Field(default_factory=list)
    candidates: list[Formulation] = Field(default_factory=list)
    web_count: int = 0
    kb_count: int = 0
    engine: str = "offline"  # "llm" | "offline"
    verified_claims: list[dict] = Field(default_factory=list)
    claim_check_engine: str = "offline"
    claim_check_pass_rate: float = 1.0

class DeepResearchRequest(Requirement):
    """Requirement plus a free-text research topic for /api/research/deep.

    ``topic`` drives multi-source retrieval and synthesis; when empty the
    requirement headline is used instead.
    """

    topic: str = ""
    source_types: list[str] = Field(
        default_factory=lambda: ["patents", "literature", "internet"],
        description="与 /api/search 相同的信息源类别",
    )

class PatentRisk(BaseModel):
    patent_id: str
    title: str
    risk: str  # "high" | "medium" | "low" | "unknown"
    claim_overlap: str
    recommendation: str

class MoleculePatentCheck(BaseModel):
    """molbloom molecular patent pre-screen result for one ingredient."""

    name: str
    smiles: str
    patented: bool | None = None

class IPReport(BaseModel):
    formulation_name: str
    novelty_score: float  # 0=likely infringes … 1=highly novel
    risks: list[PatentRisk]
    whitespace_hints: list[str]
    raw_patents_searched: int
    engine: str  # "llm" | "offline-keyword"
    molecule_checks: list[MoleculePatentCheck] = Field(default_factory=list)

class IPAnalysisRequest(BaseModel):
    formulation: Formulation
    limit_patents: int = 10

class Recommendation(BaseModel):
    """A concrete remediation suggested by an expert agent."""

    kind: str  # "substitute_crosslinker" | "substitute_resin" | "swap_catalyst" | "remove" | "review"
    target: str | None = None  # the offending ingredient, if any
    suggestion: str  # the recommended material or action
    rationale: str

class AgentIssue(BaseModel):
    """One problem detected by an expert agent."""

    code: str  # machine-readable, e.g. "isocyanate_water_incompatibility", "svhc"
    severity: str  # "high" | "medium" | "low"
    ingredient: str | None = None
    message: str
    recommendations: list[Recommendation] = Field(default_factory=list)

class AgentFinding(BaseModel):
    """The verdict from a single expert agent."""

    agent: str  # "chemist" | "inspector"
    status: str  # "pass" | "warn" | "intercept"
    issues: list[AgentIssue] = Field(default_factory=list)
    engine: str

class ReviewVerdict(BaseModel):
    """The aggregated verdict returned by the supervisor agent."""

    formulation_name: str
    overall_status: str  # "pass" | "warn" | "intercept" (worst of all findings)
    findings: list[AgentFinding] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)  # merged + deduped
    engine: str

# Complete the tradeoff models' forward refs: VerificationDoe.doe_plan
# points at DOEPlan, which lives in .doe; it is passed explicitly
# so the rebuild does not depend on caller-frame namespace luck.
TradeOffAnalysis.model_rebuild(_types_namespace={'DOEPlan': DOEPlan})
VerificationDoe.model_rebuild(_types_namespace={'DOEPlan': DOEPlan})

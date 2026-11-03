"""Stage 4 / SR-055: the env-flag registry is the single source for UI
flags, and Settings-field writes go through one function.

- ``validate_flag_registry`` (import-time on the real registry): every
  entry names a real boolean Settings field, no attribute twice. Tests
  feed it synthetic mismatches (bogus attr, non-bool default, duplicate).
- Registry snapshot: the 129 flag attrs are pinned. Adding or removing
  an entry fails here until the snapshot is deliberately updated
  alongside it. This is the second direction the validator cannot cover:
  54 boolean Settings fields have no registry entry, and no marker
  distinguishes "should be a flag" from "internal", so full set equality
  against all boolean fields would fail on the real code and is NOT
  asserted.
- Write-path convergence: the Settings-field write sites
  (``set_formulation_mode``, ``set_wiki_chat_mode``, ``apply_profile``,
  ``update_env_flags``) must call ``persist_settings_updates`` and must
  not call ``write_env_updates`` / ``get_settings.cache_clear`` directly.
  ``persist_settings_updates`` itself is pinned to do live env + cache
  invalidation + ``.env`` upsert. Overlay-backed writes (secrets, LLM
  runtime, api token, optimizer rules) are a different read path — they
  persist via ``write_env_updates`` + the runtime overlay and are
  deliberately NOT routed through here.
"""

import ast
from pathlib import Path
from types import SimpleNamespace

from app.config import Settings
from app.services.env_flags import FLAG_REGISTRY, EnvFlag, validate_flag_registry

APP = Path(__file__).resolve().parent.parent / "app"

# Frozen 2026-11-02; extended 2026-11-03 with the five WeKnora batch-1
# flags (kb_image_inherit_enabled, kb_merge_enabled,
# kb_protected_spans_enabled, kb_rerank_enabled,
# kg_relation_weight_enabled): update deliberately, together with FLAG_REGISTRY.
REGISTRY_SNAPSHOT = frozenset([
    "agent_bus_enabled", "agent_memory_enabled", "auto_adopt_next_doe_on_loop",
    "auto_audit_enabled", "auto_loop_on_sync", "auto_retrain", "celery_eager",
    "chat_chem_tools_enabled", "chat_claim_check_enabled",
    "chat_clarification_enabled", "chat_composer_plus_enabled",
    "chat_cross_encoder_enabled", "chat_multi_turn_enabled",
    "chat_skills_runtime_enabled", "chat_structured_enabled",
    "chem_extract_enabled", "chemtools_descriptor_features", "chemtools_enabled",
    "citation_expand_enabled", "citation_locator_preflight_blocking",
    "citation_locator_preflight_enabled", "connectors_builtin_enabled",
    "content_filter_enabled", "content_filter_llm_judge",
    "cross_encoder_rerank_enabled", "deep_hyde_enabled",
    "domain_profile_search", "enrich_compounds", "evidence_doi_verify_enabled",
    "evidence_provenance_enabled", "evidence_reviewer_enabled",
    "evidence_reviewer_fix_loop_enabled", "evidence_reviewer_llm_enabled",
    "external_substitutes", "frozen_corpus_required_for_export",
    "fulltext_enrich", "gpu_enabled", "kb_ann_enabled",
    "kb_children_retrieval_enabled", "kb_dedup_exact_enabled",
    "kb_hybrid_entity_boost", "kb_image_inherit_enabled",
    "kb_ingest_auto", "kb_ingest_patent_exempt", "kb_merge_enabled",
    "kb_near_dedup_enabled", "kb_patent_stitch_enabled",
    "kb_protected_spans_enabled", "kb_recommend_rerank_enabled",
    "kb_recommend_use_hybrid", "kb_relevance_shadow", "kb_rerank_enabled",
    "kb_search_deny_enabled", "kb_v2_enabled",
    "kg_chat_entity_refs_on_evidence", "kg_contradiction_demote", "kg_enabled",
    "kg_entities_on_ingest", "kg_llm_product_hint", "kg_llm_relation_extract",
    "kg_measured_feedback_enabled", "kg_multimodal_fusion_enabled",
    "kg_relation_extract_enabled", "kg_relation_weight_enabled",
    "kg_relations_on_ingest", "langfuse_enabled",
    "literature_library_enabled", "literature_manifest_enabled",
    "literature_oa_enrich_enabled", "literature_screening_enabled",
    "literature_screening_required_for_export", "loop_convergence_enabled",
    "material_store_enabled", "materials_suppliers_json_dual_write",
    "mcp_approval_enabled", "mcp_client_enabled", "mineru_batch_enabled",
    "mineru_enabled", "model_persist_enabled", "neo4j_enabled", "ocsr_enabled",
    "openalex_concept_filter", "openalex_enabled", "paperqa_enabled",
    "patent_cpc_filter", "patent_prefer_html", "pdf_formula_enrichment",
    "pdf_layout_analysis", "pdf_local_ocr", "prediction_bias_soft_correct",
    "product_extract_enabled", "publication_preflight_enabled",
    "query_compress_enabled", "query_compress_llm_enabled", "rapidocr_enabled",
    "recommend_diversity_enabled", "recommend_tradeoff_enabled",
    "recommend_uncertainty_flag", "rerank_plugin_enabled",
    "ro_crate_export_enabled", "screening_auto_freeze", "search_mmr_enabled",
    "search_rerank_enabled", "source_fts_enabled", "source_guide_enabled",
    "sources_audit_enabled", "substitute_llm", "surechembl",
    "table_extract_enabled", "verification_doe_enabled",
    "vision_extract_enabled", "web_search_allow_ddgs",
    "wiki_catalog_inject_themes", "wiki_chat_blend", "wiki_chat_save_draft",
    "wiki_compile_on_ingest", "wiki_doe_constraints", "wiki_dossier_auto_patch",
    "wiki_dossier_llm_narrative", "wiki_dossier_report_enabled",
    "wiki_embed_enabled", "wiki_enabled", "wiki_fts_enabled",
    "wiki_lint_on_compile", "wiki_llm_themes_enabled", "wiki_neo4j_project",
    "wiki_page_graph_enabled", "wiki_project_dossier_enabled",
    "wiki_storm_claim_regenerate", "wiki_storm_parallel",
    "wiki_storm_report_enabled", "wiki_storm_section_rerank",
    "workbench_auto_train",
])

WRITE_SITES = [
    ("api/settings.py", "set_formulation_mode"),
    ("api/settings.py", "set_wiki_chat_mode"),
    ("services/parse_profiles.py", "apply_profile"),
    ("services/env_flags.py", "update_env_flags"),
]


def _function_body(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name)


def _called_names(fn: ast.FunctionDef) -> list[str]:
    names = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.append(func.id)
            elif isinstance(func, ast.Attribute):
                names.append(func.attr)
    return names


def test_registry_validation_passes_on_real_code():
    validate_flag_registry()
    validate_flag_registry(FLAG_REGISTRY, Settings.model_fields)


def test_registry_validation_rejects_synthetic_mismatches():
    import pytest

    fields = {"ok_flag": SimpleNamespace(default=False)}
    good = [EnvFlag("ok_flag", "x", "y", "kb")]
    with pytest.raises(RuntimeError, match="not a Settings field"):
        validate_flag_registry([EnvFlag("no_such_attr", "x", "y", "kb")], fields)
    with pytest.raises(RuntimeError, match="not a boolean"):
        validate_flag_registry(good, {"ok_flag": SimpleNamespace(default="yes")})
    with pytest.raises(RuntimeError, match="twice"):
        validate_flag_registry(good + good, fields)


def test_registry_snapshot():
    assert len(REGISTRY_SNAPSHOT) == 133
    assert {f.attr for f in FLAG_REGISTRY} == REGISTRY_SNAPSHOT


def test_persist_settings_updates_does_all_three_layers():
    fn = _function_body(APP / "services/secrets_store.py", "persist_settings_updates")
    called = _called_names(fn)
    assert "write_env_updates" in called
    assert "cache_clear" in called
    assert "update" in called  # os.environ.update


def test_write_sites_route_through_persist_settings_updates():
    for rel, fn_name in WRITE_SITES:
        fn = _function_body(APP / rel, fn_name)
        called = _called_names(fn)
        assert "persist_settings_updates" in called, f"{rel}:{fn_name} must use persist_settings_updates"
        assert "write_env_updates" not in called, f"{rel}:{fn_name} must not write .env directly"
        assert "cache_clear" not in called, f"{rel}:{fn_name} must not invalidate the cache directly"


def test_checker_flags_synthetic_direct_write():
    fn_src = (
        "def set_x(body):\n"
        "    write_env_updates({'K': 'v'})\n"
        "    get_settings.cache_clear()\n"
    )
    fn = ast.parse(fn_src).body[0]
    called = _called_names(fn)
    assert "persist_settings_updates" not in called
    assert "write_env_updates" in called and "cache_clear" in called

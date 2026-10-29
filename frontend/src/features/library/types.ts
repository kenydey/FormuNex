/** Response shapes of the knowledge-library endpoints (free-form in the OpenAPI, so typed here). */

export interface KbSource {
  id: string;
  title: string;
  filename: string;
  source_kind: string;
  origin_url?: string | null;
  project_id: string | null;
  raw_text_chars: number;
  extraction_status: string;
  archived: boolean;
}

export type SearchSourceType = "patents" | "surechembl" | "literature" | "internet" | "local";

export interface SourceStatus {
  available: boolean;
  offline_fallback?: boolean;
  reason?: string | null;
  hint?: string | null;
  deprecated?: boolean;
}

export interface Evidence {
  source: string;
  identifier: string;
  title: string;
  snippet: string;
  relevance: number;
  is_seed_corpus?: boolean;
  url?: string | null;
  url_alt?: string | null;
  assignee?: string | null;
  pub_date?: string | null;
  oa_pdf_url?: string | null;
  is_oa?: boolean | null;
  page?: number | null;
}

export interface IngestResponse {
  filename: string;
  evidence: Evidence[];
  total: number;
  source_id?: string | null;
  extraction_status?: string;
  warnings?: string[];
}

export interface IngestEvidenceResult {
  ok: boolean;
  status: string;
  source_id?: string | null;
  task_id?: string | null;
  canonical_id?: string | null;
  reason?: string | null;
}

export interface TablePropertyView {
  name: string;
  name_normalized?: string;
  value?: number | string | null;
  unit?: string;
  unit_normalized?: string;
  raw_text?: string;
}

export interface TableAssetView {
  table_id: string;
  page_no?: number;
  caption?: string;
  kind?: string;
  headers?: string[];
  rows?: (string | number | null)[][];
  property_set?: { properties?: TablePropertyView[]; warnings?: string[] } | null;
}

export interface ExtractionTable {
  id: string;
  page_no: number | null;
  bbox: unknown[] | null;
  caption: string | null;
  markdown_text: string;
  n_rows: number | null;
  n_cols: number | null;
}

export interface ExtractionFormula {
  id: string;
  page_no: number | null;
  bbox: unknown[] | null;
  latex: string;
  formula_no: string | null;
}

export interface SourceDetail {
  id: string;
  filename: string;
  title: string;
  source_kind: string;
  raw_text_chars: number;
  extraction_status: string;
  created_at: string;
  has_fulltext: boolean;
  total_chunks: number;
  chunks_truncated: boolean;
  provenance_upstream: Record<string, unknown>[];
}

export interface EmbodimentIngredient {
  name: string;
  role: string;
  weight_pct: number;
  unit_raw?: string | null;
  confidence?: number;
  smiles?: string | null;
  cas_no?: string | null;
}

export interface EmbodimentDraft {
  status: string;
  needs_review: boolean;
  origin: string;
  doc_id: string;
  source_id?: string | null;
  title?: string | null;
  assignee?: string | null;
  pub_date?: string | null;
  url?: string | null;
  amount_source?: string;
  embodiments?: { label: string; page_hint?: number | null; ingredients: EmbodimentIngredient[]; amount_source?: string; warnings?: string[] }[];
  formulation: { name: string; domain: string; ingredients: EmbodimentIngredient[]; warnings?: string[]; source?: string };
  [key: string]: unknown;
}

export interface ManifestItem {
  id: string;
  title?: string;
  doi?: string | null;
  screening?: string;
  snippet?: string;
  source?: string;
  evidence_class?: string;
  has_fulltext?: boolean;
  enrich_status?: string;
  oa_pdf_url?: string;
  locator?: { page?: number; figure?: string; table?: string };
}

export interface Frozen { at: number; actor: string; item_ids: string[]; digest: string }

export interface Manifest {
  project_id: string;
  items: ManifestItem[];
  frozen: Frozen | null;
  coverage: { candidate_count: number; frozen_count: number };
}

export interface LibraryItem {
  id: string;
  title?: string;
  doi?: string | null;
  chemrxiv_id?: string | null;
  authors?: string[];
  year?: number | null;
  tags?: string[];
  notes?: string;
  screening?: string;
  snippet?: string;
  url?: string | null;
  collection_ids?: string[];
  has_fulltext?: boolean;
}

export interface LibraryCollection { id: string; name: string; item_ids: string[] }

export interface Library {
  project_id: string;
  items: LibraryItem[];
  collections: LibraryCollection[];
  frozen: Frozen | null;
  coverage: { candidate_count: number; frozen_count: number };
}

export interface ImportResult { added: string[]; skipped: string[]; failures: { token?: string; reason?: string; detail?: string }[] }

export interface DupGroup { id: string; title: string; item_ids: string[]; match: "identifier" | "metadata" }

export interface RuleVersion { name: string; version: string; criteria: Record<string, unknown>; created_by: string; created_at: number; changelog: string }
export interface RuleHistory { at: number; actor: string; action: string; name: string; version: string; changelog: string }

export interface ScreeningEval {
  evaluated: boolean;
  reason?: string;
  labeled_count?: number;
  confusion?: { tp: number; fp: number; tn: number; fn: number };
  metrics?: { precision: number; recall: number; f1: number };
  include_ablation?: { keyword: string; recall_without: number; recall_delta: number }[];
}

export interface CollectionFilters { date_from?: string | number; date_to?: string | number; domain_allowlist?: string[] }
export interface CollectionSchedule { enabled: boolean; interval_hours: number; last_run?: number | null }

export interface CollectionSummary {
  collection_id: string;
  name: string;
  query: string;
  filters: CollectionFilters;
  screening_preset?: string | null;
  schedule: CollectionSchedule;
  snapshot_count?: number;
  last_snapshot?: { snapshot_id: string; at: number; total: number; added: number; removed: number } | null;
}

export interface CollectionSnapshot {
  snapshot_id: string;
  at: number;
  actor?: string;
  item_ids: string[];
  added: string[];
  removed: string[];
  total: number;
}

export interface CollectionDetail extends CollectionSummary { snapshots: CollectionSnapshot[] }

export interface KbQualityOps {
  kb_quality_score: number | null;
  kb_quality_components?: Record<string, number>;
  sources_active?: number;
  sources_archived?: number;
  chunks_active?: number;
  embedded_chunks?: number;
  scan_pressure?: number;
  scan_near_cap?: boolean;
  vector_mode?: string;
  embedding_model?: string;
  topicality_would_reject_pct?: number | null;
  relevance_shadow?: Record<string, unknown>;
  hybrid_search_latency?: { p95_ms?: number | null; ann_last?: boolean; ann_matrix_last?: boolean; ann_streak?: number };
  notes?: string[];
}

export type ProbeMode = "keyword" | "hybrid" | "hybrid_rerank";

export interface ProbeHit {
  rank: number;
  chunk_id?: string | null;
  source_id?: string | null;
  title: string;
  snippet: string;
  bm25_score?: number | null;
  cosine_score?: number | null;
  hybrid_score?: number | null;
  relevance?: number | null;
  rerank_score?: number | null;
  rank_before_rerank?: number | null;
}

export interface GateDrops { blocked_domain?: number; garbage_snippet?: number; wiki_track?: number }

export interface ProbeResponse {
  query: string;
  mode: string;
  params: { top_k: number; alpha: number; rerank_applied?: boolean };
  vector_mode: string;
  elapsed_ms: number;
  hits: ProbeHit[];
  warning?: string | null;
  gate_drops?: { retrieval?: GateDrops };
}

export interface GoldenResponse {
  mode: string;
  top_k: number;
  total: number;
  passed: number;
  failed: number;
  mrr?: number;
  recall_at_k?: number;
  results: { question: string; passed: boolean; matched_keyword?: string | null }[];
}

export interface MemoryItem {
  id: number;
  scope: "global" | "project" | "user";
  scope_id: string;
  key: string;
  value: string;
  created_at: string;
  updated_at: string;
}

export interface ParseAvailability { gpu_available: boolean; mineru_key_present: boolean; vision_available: boolean; active_rag_backend: string }

export interface OcsrStatus { enabled: boolean; molscribe_installed: boolean; molscribe_queue: string; molscribe_timeout_s: number }

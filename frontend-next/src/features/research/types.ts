/** Wire types of the research area (federated search, grounded Q&A, deep research, artifacts, IP, embodiment drafts). */

export type SourceType = "patents" | "surechembl" | "literature" | "internet" | "notebooklm";

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
  domain_tags?: string[];
  domain_match?: "strong" | "weak" | "none" | null;
  page?: number | null;
  page_end?: number | null;
  paragraph?: number | null;
}

export interface SourceStatus {
  available: boolean;
  offline_fallback?: boolean;
  reason?: string | null;
  hint?: string | null;
  deprecated?: boolean;
}

export interface FilterReport {
  kept: number;
  dropped: number;
  dropped_by_reason: Record<string, number>;
  dropped_examples: string[];
  source_counts?: Record<string, number>;
}

export interface SearchResponse {
  evidence: Evidence[];
  total: number;
  source_status?: Record<string, SourceStatus>;
  used_seed_fallback?: boolean;
  filter_report?: FilterReport | null;
}

export interface SearchProgress {
  message: string;
  total: number;
  source: string | null;
  newCount: number;
  sourcesDone: string[];
  sourcesPending: string[];
}

export interface KbIngestDoc {
  identifier?: string;
  title?: string;
  status?: string;
  source_id?: string | null;
  error?: string | null;
}

export interface KbIngestProgress {
  docs: KbIngestDoc[];
  done: number;
  total: number;
  indexed: number;
  failed: number;
}

export interface IngestEvidenceResponse {
  ok: boolean;
  status: string;
  source_id?: string | null;
  task_id?: string | null;
  canonical_id?: string | null;
  reason?: string | null;
}

export interface FormulationIngredient {
  name: string;
  zh_name?: string;
  role: string;
  weight_pct: number;
  cas_no?: string | null;
  smiles?: string | null;
}

export interface Formulation {
  name: string;
  domain: string;
  ingredients: FormulationIngredient[];
  rationale?: string;
  predicted?: Record<string, unknown>;
  warnings?: string[];
  score?: number | null;
  source?: string;
}

export interface ComprehensiveReport {
  topic: string;
  report_markdown: string;
  citations: Evidence[];
  candidates: Formulation[];
  web_count: number;
  kb_count: number;
  engine: string;
}

export interface ChatNotice { code: string; message: string }
export interface Clarification {
  question: string;
  ambiguous_term?: string;
  possible_meanings?: string[];
  candidate_entity_ids?: string[];
}
export interface EvidenceProvenance { evidence_availability?: string; notes?: string[] }

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
  citations?: Evidence[];
  streaming?: boolean;
  phase?: string;
  toolStatus?: string | null;
  kbChunksUsed?: number;
  notices?: ChatNotice[] | null;
  clarification?: Clarification | null;
  provenance?: EvidenceProvenance | null;
}

export type ChatStreamEvent =
  | { type: "phase"; phase: string }
  | { type: "meta"; kb_used?: number }
  | { type: "tool_start"; name: string; label?: string }
  | { type: "tool_result"; name: string; ok: boolean }
  | { type: "token"; delta: string }
  | {
      type: "done"; answer: string; citations?: Evidence[]; kb_chunks_used?: number; notices?: ChatNotice[] | null;
      clarification?: Clarification | null; evidence_provenance?: EvidenceProvenance | null;
    }
  | { type: "error"; message: string };

// ── Artifacts: versions / preflight / export shelf ──

export type VersionStatus = "staging" | "pending" | "finalized";

export interface ArtifactLineage { lineage_id: string; project_id: string; name: string; kind: string; created_at: number; version_ids: string[] }
export interface ArtifactVersion {
  version_id: string;
  lineage_id: string;
  based_on_version_id: string | null;
  status: VersionStatus;
  sha256: string;
  content_bytes: number;
  created_at: number;
  actor?: string | null;
  finalized_at?: number | null;
  evidence_frozen?: boolean;
}
export interface ArtifactVersionList {
  lineage: ArtifactLineage;
  versions: ArtifactVersion[];
  graph: { version_id: string; based_on_version_id: string | null }[];
}
export interface ArtifactDiffOp { type: "equal" | "insert" | "delete"; old_text: string; new_text: string }
export interface ArtifactDiff { version_id: string; against_id: string; truncated: boolean; ops: ArtifactDiffOp[] }

export interface PreflightFinding {
  id: string;
  check: string;
  severity: "blocking" | "major" | "minor" | "info" | string;
  status: "open" | "resolved" | "overridden" | string;
  title: string;
  detail: string;
  evidence?: string[];
  resolution?: { kind?: string; actor?: string; reason?: string; note?: string; at?: number } | null;
  stale?: boolean;
}
export interface PreflightState {
  project_id: string;
  kind: string;
  content_hash: string;
  findings: PreflightFinding[];
  finalization: { actor?: string; at?: number } | null;
  open_blocking: number;
  open_major: number;
  ready: boolean;
}

export interface ExportFile { name: string; size: number; updated_at: string; content_type: string }

// ── IP / embodiment ──

export interface PatentRisk { patent_id: string; title: string; risk: "high" | "medium" | "low" | "unknown"; claim_overlap: string; recommendation: string }
export interface MoleculeCheck { name: string; smiles: string; patented: boolean | null }
export interface IPReport {
  formulation_name: string;
  novelty_score: number;
  risks: PatentRisk[];
  whitespace_hints: string[];
  raw_patents_searched: number;
  engine: string;
  molecule_checks?: MoleculeCheck[];
}

export interface EmbodimentIngredient { name: string; role: string; weight_pct: number; unit_raw?: string | null; confidence?: number }
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
  url_alt?: string | null;
  amount_source?: string;
  embodiments?: { label: string; page_hint?: number | null; ingredients: EmbodimentIngredient[]; amount_source?: string; warnings?: string[] }[];
  formulation: { name: string; domain: string; ingredients: FormulationIngredient[]; warnings?: string[]; [k: string]: unknown };
  ingredients_detail?: FormulationIngredient[];
  [k: string]: unknown;
}
export interface DraftConfirmResult {
  ok: boolean;
  pending_materials: { action: string; name?: string; reason?: string }[];
  formulation_entity_id: string;
  promoted_to_pool: boolean;
  note?: string;
}
export interface EligibilityItem { source_id: string; eligible: boolean; reason?: string | null; has_table_signal?: boolean; title?: string | null }

export interface KbSource {
  id: string;
  title?: string | null;
  filename?: string | null;
  source_kind?: string | null;
  raw_text_chars?: number | null;
  extraction_status?: string | null;
  [k: string]: unknown;
}

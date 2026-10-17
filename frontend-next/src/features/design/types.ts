/** Backend shapes used by the design area (legacy frontend/src/api/types.ts, trimmed to what the pages read). */

export interface LeverSpec {
  name: string;
  low: number;
  high: number;
  unit?: string;
  kind?: string;
  levels?: Array<number | string> | null;
  material_map?: Record<string, string> | null;
}

export interface FactorCandidate {
  name: string;
  low: number;
  high: number;
  unit: string;
  kind?: string;
  levels?: Array<number | string> | null;
  rationale: string;
  evidence_ids: string[];
  source: string;
}

export interface Ingredient {
  name: string;
  zh_name?: string | null;
  role: string;
  weight_pct: number;
  cas_no?: string | null;
  smiles?: string | null;
  [k: string]: unknown;
}

export interface Formulation {
  name: string;
  domain: string;
  ingredients: Ingredient[];
  rationale?: string;
  predicted?: Record<string, number>;
  predicted_std?: Record<string, number>;
  factors?: Record<string, number>;
  score?: number | null;
  warnings?: string[];
  [k: string]: unknown;
}

export interface DOEFactor { name: string; low: number; high: number; unit: string }
export interface DOERun {
  run_id: number;
  coded: Record<string, number>;
  natural: Record<string, number | string>;
  ai_suggested?: boolean;
  infeasible?: boolean;
  infeasible_reason?: string | null;
}
export interface DOEPlan {
  design: string;
  factors: DOEFactor[];
  runs: DOERun[];
  notes: string;
  plan_id: string;
  domain: string | null;
}

export interface RunExplanation {
  run_id: number;
  strategy: string;
  summary: string;
  nearest_experiment_ids: string[];
  predicted_delta_pct?: number | null;
  acquisition_score?: number | null;
  constraint_warnings?: string[];
}
export interface AnomalyFlag { experiment_id: string; type: string; severity: "info" | "warning" | "critical"; note: string }
export interface ActiveDoeResult {
  plan: DOEPlan;
  campaign_state: string | null;
  engine: string;
  strategy_label?: string;
  strategy_rationale?: string;
  run_explanations?: RunExplanation[];
  anomalies?: AnomalyFlag[];
  recommended_next_action?: string;
  chemical_feasibility?: { feasible: boolean; status: string; reasons: string[] } | null;
  physical_constraints?: { feasible: boolean; status: string; reasons: string[] } | null;
  /** id under which /api/doe/active stored the plan ("" = not persisted → cannot be dispatched). */
  plan_id?: string;
}

export interface ModelInfo {
  domain: string;
  project_id?: string;
  metric: string;
  backend: string;
  n_samples: number;
  r2: number;
  cv_r2: number | null;
  rmse: number;
  trained_at?: string | null;
  pinned?: boolean;
}

export interface TrainingReport { trained: ModelInfo[]; total_records: number; message: string }

export interface DoeHistoryItem {
  plan_id?: string;
  design?: string;
  domain?: string;
  notes?: string;
  campaign_id?: number | null;
  round?: number | null;
  status?: string;
  created_at?: string | null;
  runs?: DOERun[];
  factors?: DOEFactor[];
}

export interface WorkbenchCampaignSummary {
  id: number;
  name: string;
  status: string;
  strategy: string;
  row_count: number;
  project_id: string | null;
}
export interface WorkbenchRow {
  id: number;
  campaign_id: number;
  item_id: string;
  status: string;
  planned_params: Record<string, unknown>;
  measurements: Record<string, unknown>;
  refcode?: string | null;
}
export interface WorkbenchCampaign {
  campaign_id: number;
  name: string;
  strategy: string;
  status: string;
  project_id?: string | null;
  rows: WorkbenchRow[];
}

// ── Inverse design ──
export interface HardConstraint { metric: string; op: "le" | "ge" | "between"; value: number; value_max?: number | null; label?: string }
export interface ObjectiveSpec { metric: string; weight: number; direction: "maximize" | "minimize" | "match_target" }
export interface DesignCandidate { formulation: Formulation; pareto_rank: number | null; feasible: boolean; violation: number; materials: string[] }
export interface VerificationDoe { candidate_id: string; candidate_name: string; note: string; doe_plan: DOEPlan }
export interface InverseDesignResult {
  topic: string;
  domain: string;
  candidates: DesignCandidate[];
  pareto_frontier_ids: string[];
  tradeoff?: { verification_does?: VerificationDoe[] } | null;
  generations: number;
  evaluations: number;
  rejected_infeasible: number;
  seeded_from: Record<string, number>;
  engine: string;
  warnings: string[];
  recommend_id?: string | null;
  error?: string;
}

// ── Substitution ──
export interface MetricDelta { before: number | null; after: number | null; delta: number | null; pct: number | null }
export interface SubstituteCandidate {
  material: string;
  zh_name?: string | null;
  role?: string | null;
  functional_class?: string | null;
  availability: string;
  structural_score: number;
  deltas: Record<string, MetricDelta>;
  delta_confidence: "high" | "low" | "cost_only";
  feasible: boolean;
  blocking_reasons: string[];
  supply_badges?: string[];
  stale_price?: boolean;
  missing_price?: boolean;
  long_lead_time?: boolean;
}
interface Evidence { source_id?: string; chunk_id?: string | null; sentence?: string; confidence?: number | null }
export interface ExternalCandidate {
  name: string; iupac_name?: string | null; cas_no?: string | null; smiles?: string | null; cid?: number | null;
  similarity: number; source: string; in_catalog: boolean; catalog_name?: string | null; role_hint?: string | null; note?: string | null;
}
export interface LiteratureCandidate {
  name: string; source: string; confidence?: number | null; entity_id?: string | null; cas_no?: string | null; smiles?: string | null;
  role_hint?: string | null; in_catalog: boolean; catalog_name?: string | null; evidence?: Evidence[]; note?: string | null;
}
export interface LlmCandidate {
  name: string; kind?: string | null; rationale?: string | null; source: string; cas_no?: string | null; smiles?: string | null;
  role_hint?: string | null; in_catalog: boolean; catalog_name?: string | null; note?: string | null;
}
export interface SurechemblPatent { doc_id: string; title?: string | null; url?: string | null }
export interface SurechemblCandidate {
  name: string; chemical_id?: string | null; smiles?: string | null; similarity: number; source: string; in_catalog: boolean;
  catalog_name?: string | null; role_hint?: string | null; patents?: SurechemblPatent[]; note?: string | null;
}
export interface LayerMeta { enabled?: boolean; queried?: boolean; count: number; skipped_reason?: string | null; mode?: string }
export interface SubstitutionReport {
  original: string;
  original_in_catalog?: boolean;
  slot_index: number;
  role: string;
  substitute_group: string | null;
  candidates: SubstituteCandidate[];
  total_considered: number;
  identity?: { query: string; cas_no?: string; smiles?: string | null; source: string; resolved: boolean };
  external?: ExternalCandidate[];
  external_meta?: LayerMeta;
  literature?: LiteratureCandidate[];
  literature_meta?: LayerMeta;
  surechembl?: SurechemblCandidate[];
  surechembl_meta?: LayerMeta;
  llm?: LlmCandidate[];
  llm_meta?: LayerMeta;
  layers_used?: string[];
}
export interface SupplyRiskReport { at_risk: Record<string, string>; affected: { formulation: string }[] }

// ── Similar formulations ──
export interface SimilarMatch {
  experiment_id: number;
  project_id: string;
  project_title: string | null;
  similarity: number;
  factors: Record<string, number>;
  measured: Record<string, number>;
  shared_ingredients: string[];
  differing_ingredients: string[];
}

// ── QC ──
export interface QCMeasurementView {
  metric: string; value: number; unit: string; test_method: string; spec_min: number | null; spec_max: number | null; passed: boolean | null;
}
export interface QCReportResult {
  experiment_id: number;
  source_id: string;
  measurements: QCMeasurementView[];
  measurement_count: number;
  attached: boolean;
  already_attached: boolean;
  synced_measured: Record<string, number>;
  parser: string;
  extraction_error: string | null;
  message: string;
}

// ── Formulation versions ──
export interface VersionView { id: string; lineage_id: string; version: number; name: string; domain: string; change_summary: string; created_at: string | null }
export interface Lineage { lineage_id: string; versions: VersionView[] }

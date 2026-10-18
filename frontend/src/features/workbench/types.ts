import type { Objective } from "@/features/spec/types";

/** Objective as frozen on a campaign (``objectives_snapshot``) or taken from the project requirement. */
export type ObjectiveSpec = Objective & { value_type?: string | null };

export type RowStatus = "Pending" | "In Progress" | "Completed" | "Blocked";

export interface WorkbenchRow {
  id: number;
  campaign_id: number;
  item_id?: string | null;
  status: string;
  planned_params: Record<string, number>;
  actual_params: Record<string, number | null>;
  measurements: Record<string, number | string | null>;
  predicted?: Record<string, number | string>;
  note?: string | null;
  tags?: string[];
  parent_sample_id?: string | null;
  parent_campaign_id?: number | null;
  refcode?: string | null;
  ingested?: boolean;
}

export interface CampaignSummary {
  id: number;
  name: string;
  status: string;
  strategy: string;
  row_count: number;
  project_id: string | null;
  datalab_collection_id?: string | null;
}

export interface LoopStatus {
  status?: string;
  rounds?: number;
  converged?: boolean;
  message?: string;
}

export interface CampaignDetail {
  campaign_id: number;
  name: string;
  strategy: string;
  status: string;
  project_id: string | null;
  primary_metric?: string | null;
  objectives_snapshot: ObjectiveSpec[];
  loop_history: Record<string, unknown>[];
  loop_status: LoopStatus | null;
  rows: WorkbenchRow[];
}

export interface BiasStat { n: number; mean_error: number; rmse: number; mae: number; max_abs?: number }
export interface PredictionBias { n_rows: number; by_metric: Record<string, BiasStat> }

export interface SyncResponse {
  updated: number;
  rows: WorkbenchRow[];
  training_ingested: number;
  training_message: string;
  prediction_bias: PredictionBias | null;
  kg_written: number | null;
  kg_error: string | null;
  loop_task_id: string | null;
  loop_message: string;
  loop_status: LoopStatus | null;
  quality: { dropped_values?: number } | null;
}

export interface Quality { stale_count: number; stale_refs: unknown[]; errors_count: number; dropped_total: number }

export interface BiasTrendPoint { at: string | null; n_rows: number; by_metric: Record<string, BiasStat> }
export interface BiasTrend { campaign_id: number; trend: BiasTrendPoint[]; alerts: string[]; threshold_rmse: number }

export interface DoeFactor { name: string; low: number; high: number; unit?: string; kind?: string; levels?: unknown[] | null }
export interface DoeRun {
  run_id: number;
  coded?: Record<string, number>;
  natural: Record<string, number | string>;
  ai_suggested?: boolean;
  infeasible?: boolean;
  infeasible_reason?: string | null;
}
export interface DoePlan { design: string; factors: DoeFactor[]; runs: DoeRun[]; notes?: string; plan_id?: string | null; domain?: string; status?: string }

export interface RunExplanation { run_id: number; strategy?: string; summary: string; constraint_warnings?: string[] }
export interface AnomalyFlag { experiment_id: string; type: string; severity?: string; note: string }
export interface AdaptiveMeta {
  strategy_label?: string;
  strategy_rationale?: string;
  run_explanations?: RunExplanation[];
  anomalies?: AnomalyFlag[];
  recommended_next_action?: string;
  budget_remaining?: number | null;
}

export interface ModelInfo {
  domain: string;
  project_id?: string;
  metric: string;
  backend: string;
  n_samples: number;
  r2: number;
  cv_r2?: number | null;
  rmse: number;
  trained_at?: string | null;
  version_id?: string | null;
  conformal_q90?: number | null;
  uncertainty_calibrated?: boolean;
  pinned?: boolean;
  newer_version_id?: string | null;
}

export interface ModelVersion {
  version_id: string;
  trained_at?: string | null;
  backend?: string | null;
  n_samples?: number | null;
  r2?: number | null;
  rmse?: number | null;
  is_current?: boolean;
  pinned?: boolean;
}

export interface TrainingStatus {
  total_records: number;
  min_samples: number;
  sufficient: boolean;
  models_trained: number;
  by_domain?: Record<string, number>;
  message?: string;
}

export interface Verdict { feasible?: boolean; status?: string; reasons: string[] }

export interface LoopReport extends AdaptiveMeta {
  domain: string;
  total_records: number;
  model_info: ModelInfo[];
  rmse_by_metric: Record<string, number>;
  optimization: { history: number[]; top_formulations: unknown[]; engine?: string; measurement_source?: string; lab_points_used?: number };
  next_doe: DoePlan;
  engine: string;
  campaign_state?: string | null;
  converged: boolean;
  loop_message: string;
  chemical_feasibility?: Verdict | null;
  physical_constraints?: Verdict | null;
  cost_summary?: { cost_cny_per_kg?: number | null; voc_gpl?: number | null } | null;
}

export interface Attachment { id: string; experiment_id: number; source_document_id: string; kind: string; filename: string; note: string; created_at: string | null }
export interface VersionEntry { id: string; version: number; action?: string; timestamp?: string; creator?: string | null }
export interface QcMeasurement { metric: string; value: number; unit: string; test_method: string; spec_min: number | null; spec_max: number | null; passed: boolean | null }

/** Loop-related keys of the project workspace (PUT /api/projects/{id} merges them). */
export interface LoopWorkspace {
  requirement?: import("@/features/spec/types").Requirement | null;
  doe_plan?: DoePlan | null;
  adaptive_doe?: AdaptiveMeta | null;
  workbench_campaign_id?: number | null;
  workbench_adopted_plan_id?: string | null;
  loop_report?: LoopReport | null;
  rmse_history?: Record<string, number>[];
  campaign_state?: string | null;
  optimize_engine?: string;
  loop_doe_engine?: string;
  auto_loop_on_sync?: boolean;
  auto_loop_max_rounds?: number;
  auto_loop_round?: number;
  auto_adopt_next_doe_on_loop?: boolean;
  wiki_dossier_auto_patch?: boolean;
  prediction_bias_soft_correct?: boolean;
}

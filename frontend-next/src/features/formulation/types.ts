/** Wire types of the formulation / experiment endpoints used by this feature area. */

export interface Ingredient {
  name: string;
  role: string;
  smiles?: string | null;
  formula?: string | null;
  mf_structure?: string | null;
  cas_no?: string | null;
  zh_name?: string | null;
  molar_mass?: number | null;
  weight_pct: number;
  component_type?: string;
  amount_display?: string;
  notes?: string;
  evidence_refs?: string[];
  grounding_confidence?: "high" | "medium" | "low";
}

export interface EffectTrace { field: string; label: string; status: "wired" | "unwired" | string; detail?: string; consumers?: string[] }

export interface FormulationExplain {
  objectives_hit?: string[];
  constraints_miss?: string[];
  evidence_refs?: (Record<string, string> | string)[];
  kg_signals?: { feasible?: boolean; measured_materials?: string[]; synergizes?: string[]; inhibits?: string[] } & Record<string, unknown>;
  supply_flags?: string[];
  uncertainty?: string[];
  bias_corrected?: boolean;
  bias_corrected_metrics?: string[];
  notes?: string[];
  effect_trace?: EffectTrace[];
}

export interface Formulation {
  name: string;
  domain: string;
  ingredients: Ingredient[];
  rationale?: string;
  source?: string;
  predicted?: Record<string, number>;
  predicted_std?: Record<string, number>;
  prediction_tiers?: Record<string, string>;
  score?: number | null;
  warnings?: string[];
  kg_compat?: { feasible?: boolean; incompatible_pairs?: { a?: string; b?: string }[]; measured_materials?: string[] } & Record<string, unknown> | null;
  bias_corrected_metrics?: string[];
  explain?: FormulationExplain | null;
  client_uid?: string | null;
}

export interface ObjectiveSpec {
  metric: string;
  display_name?: string;
  direction?: "maximize" | "minimize" | "match_target" | string;
  target_value?: number | null;
  unit?: string;
  ref_min?: number | null;
  ref_max?: number | null;
  weight?: number;
}

export interface ResearchResult {
  requirement_headline?: string;
  recommended: Formulation[];
  recommend_engine?: "llm" | "offline";
  recommend_id?: string | null;
  chat_markdown?: string;
  [k: string]: unknown;
}

export interface ExperimentSummary {
  id: number;
  domain: string;
  label: string;
  source: string;
  project_id: string;
  measured: Record<string, number>;
  measurement_count: number;
  created_at: string | null;
}

export interface ExperimentSearchHit {
  row_id: number;
  campaign_id: number;
  campaign_name: string;
  item_id: string;
  status: string;
  planned_params: Record<string, unknown>;
  measurements: Record<string, unknown>;
}

export interface Attachment {
  id: string;
  experiment_id: number;
  source_document_id: string;
  kind: string;
  filename: string;
  note: string;
  created_at?: string | null;
}

export interface QCMeasurement {
  metric: string;
  value: number;
  unit: string;
  test_method: string;
  spec_min?: number | null;
  spec_max?: number | null;
  passed?: boolean | null;
}

export interface QCMeasurementList {
  experiment_id: number;
  measurements?: QCMeasurement[];
  attachments?: { id: string; source_document_id: string; kind: string; created_at?: string | null }[];
}

export interface WorkbenchRow {
  id: number;
  campaign_id: number;
  item_id: string;
  status: string;
  planned_params: Record<string, unknown>;
  actual_params: Record<string, number>;
  measurements: Record<string, unknown>;
  note?: string | null;
  tags?: string[];
  parent_sample_id?: string | null;
  parent_campaign_id?: number | null;
  refcode?: string | null;
  ingested: boolean;
}

export interface WorkbenchCampaign {
  campaign_id: number;
  name: string;
  strategy: string;
  status: string;
  project_id?: string | null;
  primary_metric?: string | null;
  objectives_snapshot?: ObjectiveSpec[];
  rows: WorkbenchRow[];
}

export interface RowVersion { id: string; version: number; action?: string; timestamp?: string; creator?: string | null }

export interface FormulationVersion {
  id: string;
  lineage_id: string;
  version: number;
  parent_version_id: string | null;
  name: string;
  domain: string;
  change_summary: string;
  created_by: string;
  created_at: string | null;
  project_id?: string | null;
}

export interface Lineage { lineage_id: string; versions: FormulationVersion[] }

export interface IngredientChange {
  name: string;
  change: "added" | "removed" | "adjusted" | string;
  role?: string;
  before_pct: number | null;
  after_pct: number | null;
  delta_pct: number | null;
}

export interface MetricDelta { before: number | null; after: number | null; delta: number | null; pct: number | null }

export interface VersionDiff {
  from_version: number;
  to_version: number;
  change_summary: string;
  topology_changed: boolean;
  renamed: string[] | null;
  ingredient_changes: IngredientChange[];
  metric_deltas: Record<string, MetricDelta>;
}

export interface ProvenanceEdge { from_type: string; from_id: string; to_type: string; to_id: string; relation: string }

export interface ChemicalProfile {
  query: string;
  cas: string;
  iupac_name: string;
  zh_name: string;
  formula: string;
  smiles?: string | null;
  molar_mass?: number | null;
  found: boolean;
  func_groups?: string[];
  patented?: boolean | null;
  safety?: { controlled?: boolean | null; explosive?: boolean | null };
}

export interface ModeChoice { value: string; label: string; desc: string }

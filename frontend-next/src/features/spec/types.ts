export const DOMAINS = [
  ["anticorrosion_coating", "防腐蚀涂料", "Anti-corrosion coating"],
  ["degreaser", "脱脂剂", "Degreaser"],
  ["surface_treatment", "表面处理剂", "Surface treatment"],
  ["autodeposition_coating", "自沉积涂料", "Autodeposition coating"],
] as const;

export const SUBSTRATES = [
  ["carbon_steel", "碳钢", "Carbon steel"], ["galvanized_steel", "镀锌钢", "Galvanized steel"], ["aluminum", "铝合金", "Aluminium"],
  ["stainless_steel", "不锈钢", "Stainless steel"], ["magnesium_alloy", "镁合金", "Magnesium alloy"],
] as const;

export interface Objective {
  id?: string;
  metric: string;
  display_name?: string;
  direction: "maximize" | "minimize" | "match_target";
  ref_min?: number | null;
  ref_max?: number | null;
  target_value?: number | null;
  unit?: string;
  weight?: number;
}

export interface RedLineSubstance { cas: string; name?: string; max_pct: number; reason?: string }
export interface ReleaseSpec { metric: string; label?: string; unit?: string; method?: string; min?: number | null; max?: number | null; text?: string; comparison?: "full" | "rounded" }
export interface Acceptance { metric: string; label?: string; min?: number | null; max?: number | null }

export interface SpecContract {
  process_window?: { cure_temp_c?: [number, number]; cure_time_min?: [number, number]; film_thickness_um?: [number, number]; application_method?: string[]; notes?: string };
  red_lines?: RedLineSubstance[];
  voc_limit_g_l?: number | null;
  forbidden_pictograms?: string[];
  forbidden_signal_word?: "危险" | "警告" | null;
  release_specs?: ReleaseSpec[];
  acceptance?: Acceptance[];
}

export interface Requirement {
  project_id?: string;
  product_type?: string;
  application?: string;
  domain: string;
  substrate?: string;
  notes?: string;
  voc_limit_gpl?: number | null;
  cure_temperature_c?: number | null;
  ph_target?: number | null;
  salt_spray_hours?: number;
  film_weight_gsm?: number;
  cleaning_efficiency?: number;
  objectives?: Objective[];
  levers?: { name: string; low: number; high: number; unit?: string }[];
  /** 工艺约束 SSOT {名称: 值}: catalogue labels and free-form entries. */
  constraint_values?: Record<string, number>;
  [k: string]: unknown;
}

export interface ProjectDetail {
  id: string;
  title: string;
  headline: string;
  domain: string;
  created_at: string;
  updated_at: string;
  workspace: {
    requirement: Requirement | null;
    spec_contract?: SpecContract;
    /** Catalogue constraints shown in the constraints editor (requirements/constraints.ts). */
    active_constraints?: string[];
    /** Free text injected into this project's agent prompts. */
    agent_context?: string;
    /** Session evidence list and the identifiers ticked for Q&A. */
    sources?: Record<string, unknown>[];
    selected_sources?: string[];
    [k: string]: unknown;
  };
}

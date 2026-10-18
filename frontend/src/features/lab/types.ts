export interface RequestedMetric { metric: string; predicted?: number; sigma?: number; method_code?: string; unit?: string }
export interface LabTask {
  id: string; task_no: string; status: string; project_id: string | null; doe_plan_id: string | null; run_index: number | null;
  factors: Record<string, unknown>; requested_metrics: RequestedMetric[]; assignee_id: string | null; notes: string;
  due_at: string | null; sample_no: string | null; experiment_id: number | null;
  measurements?: Measurement[]; measured?: Record<string, number>;
}
export interface Measurement {
  id: string; experiment_id: number; metric: string; value: number; unit: string; test_method: string; method_id: string | null;
  instrument: string; instrument_id: string | null; operator: string; operator_id: string | null; sample_id: string | null;
  lab_task_id: string | null; review_status: string; reviewed_by: string | null; reviewed_at: string | null;
  raw_file_key: string | null; external_ref: string | null; note: string; measured_at: string | null; created_at: string | null;
  task_no?: string; sample_no?: string;
}
export interface Method { id: string; code: string; name_zh: string; name_en: string; metric: string; unit: string; rounding: { decimals?: number }; valid_min: number | null; valid_max: number | null; active: boolean }
export interface Instrument { id: string; asset_no: string; name: string; model: string; location: string; calibrated_at: string | null; calibration_due: string | null; calibration_state: string; parser: string; active: boolean }

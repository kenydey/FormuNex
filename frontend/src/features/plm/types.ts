export interface Product { id: string; code: string; name_zh: string; name_en: string; project_id: string | null; lifecycle_state: string; revisions: number; updated_at: string }
export interface ProductDetail {
  id: string; code: string; name_zh: string; name_en: string; lifecycle_state: string; project_id: string | null;
  category_identifier: string | null; legacy_code: string | null;
  revisions: { id: string; rev: string; state: string; formulation_version_id: string | null; frozen_at: string | null }[];
  batches: { id: string; batch_no: string; status: string }[];
}
export interface Revision {
  id: string; rev: string; state: string; frozen_at: string | null; formulation_version_id: string | null;
  winning_experiment_id: number | null; spec_snapshot: Record<string, unknown>; created_at: string;
  product: { id: string; code: string; name_zh: string; name_en: string; project_id: string | null; lifecycle_state: string };
}
export interface DocSummary {
  id: string; revision_id: string; batch_id: string | null; doc_type: string; lang: string; version_no: number; status: string;
  content_hash: string; supersedes_id: string | null; created_at: string; created_by: string | null; has_pdf: boolean; has_docx: boolean; missing: string[]; warnings: string[];
  regulation: string;
}
export interface DocField { key: string; label: string; value: unknown; source: string }
export interface DocSection { no: number; key: string; title: string; fields: DocField[]; tables: { key: string; columns: string[]; rows: unknown[][]; source: string }[]; paragraphs: string[] }
export interface DocContent { doc_type: string; lang: string; title: string; header: Record<string, unknown>; sections: DocSection[]; validation: { missing: string[]; warnings: string[] }; meta: Record<string, unknown> }
export interface DocDetail extends DocSummary { content: DocContent }
export interface GateItem { key: string; message: string; waivable: boolean; waived: boolean; waiver: null | { id: string; reason: string; waived_by: string; at: string } }
export interface GateCheck { key: string; code: string; title: string; status: string; waivable: boolean; items: GateItem[]; notes: string[]; evidence: Record<string, unknown> }
export interface Gate { revision_id: string; passed: boolean; evaluated_at: string; checks: GateCheck[]; documents: { id: string; doc_type: string; lang: string; version_no: number; status: string }[] }
export interface PackageView {
  id: string; revision_id: string; status: string; package_hash: string; document_ids: string[]; created_at: string; released_at: string | null;
  has_delivery: boolean; publish_results: { connector: string; action: string; ok: boolean; detail: string; at: string }[];
  flow: { status: string | null; steps: { seq: number; role: string; status: string; decided_by: string | null; decided_at: string | null; comment: string; current: boolean }[] };
  signatures: { id: string; step: number | null; role: string | null; username: string | null; display_name: string | null; meaning: string; meaning_zh: string; signed_at: string; signed_hash: string }[];
  signatures_valid: boolean; gate: Gate | null;
}
export const DOC_TYPES = ["sds", "tds", "label", "coa", "ts"] as const;
export const DOC_NAMES: Record<string, string> = { sds: "SDS", tds: "TDS", label: "标签", coa: "COA", ts: "TS" };

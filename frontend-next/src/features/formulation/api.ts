/** Endpoint wrappers of the formulation area (all through ``call`` → auth + token refresh). */
import { ApiError, call } from "@/api/client";
import { authFetch } from "@/api/auth";
import type {
  Attachment, ChemicalProfile, ExperimentSearchHit, ExperimentSummary, Formulation, Lineage, ModeChoice, ProvenanceEdge,
  QCMeasurementList, RowVersion, VersionDiff, WorkbenchCampaign, WorkbenchRow,
} from "./types";

const enc = encodeURIComponent;
const row = (c: number | string, r: number | string) => `/api/experiments/workbench/${c}/rows/${r}`;

export const fx = {
  experiments: (o: { domain?: string; project_id?: string; limit?: number }) => {
    const qs = new URLSearchParams();
    if (o.domain) qs.set("domain", o.domain);
    if (o.project_id) qs.set("project_id", o.project_id);
    qs.set("limit", String(o.limit ?? 100));
    return call<ExperimentSummary[]>("GET", `/api/experiments?${qs}`);
  },
  searchExperiments: (q: string) => call<ExperimentSearchHit[]>("GET", `/api/experiments/search${q ? `?q=${enc(q)}` : ""}`),
  experimentMeasurements: (id: number | string) => call<QCMeasurementList>("GET", `/api/qc/experiments/${id}/measurements`),
  experimentAttachments: (id: number | string) => call<Attachment[]>("GET", `/api/experiments/${id}/attachments`),
  uploadExperimentAttachment: (id: number | string, file: File, kind = "qc_report") => {
    const fd = new FormData();
    fd.append("file", file);
    return call<Attachment>("POST", `/api/experiments/${id}/attachments?kind=${enc(kind)}`, fd);
  },
  syncDatalab: (id: number | string) => call<{ synced?: number; errors?: unknown[] }>("POST", `/api/experiments/${id}/sync-datalab`, {}),

  experiment: (id: number | string) => call<ExperimentSummary>("GET", `/api/experiments/${enc(String(id))}`),
  campaign: (c: number | string) => call<WorkbenchCampaign>("GET", `/api/experiments/workbench/${c}`),
  rowLineage: (c: number | string, r: number | string) => call<WorkbenchRow[]>("GET", `${row(c, r)}/lineage`),
  rowMeasurements: (c: number | string, r: number | string) => call<QCMeasurementList>("GET", `/api/qc/workbench/${c}/rows/${r}/measurements`),
  rowAttachments: (c: number | string, r: number | string) => call<Attachment[]>("GET", `${row(c, r)}/attachments`),
  uploadRowAttachment: (c: number | string, r: number | string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return call<Attachment>("POST", `${row(c, r)}/attachments`, fd);
  },
  deleteRowAttachment: (c: number | string, r: number | string, id: string) => call<{ deleted: boolean }>("DELETE", `${row(c, r)}/attachments/${enc(id)}`),
  rowAttachmentDownload: (c: number | string, r: number | string, id: string) => `${row(c, r)}/attachments/${enc(id)}/download`,
  rowVersions: (c: number | string, r: number | string) => call<{ refcode: string; versions: RowVersion[] }>("GET", `${row(c, r)}/versions`),
  compareRowVersions: (c: number | string, r: number | string, v1: string, v2: string) =>
    call<{ refcode: string; diff: Record<string, unknown> }>("GET", `${row(c, r)}/versions?compare_v1=${enc(v1)}&compare_v2=${enc(v2)}`),
  restoreRowVersion: (c: number | string, r: number | string, v: string) => call<{ restored: boolean }>("POST", `${row(c, r)}/versions/${enc(v)}/restore`, {}),
  setRowTags: (c: number | string, r: number | string, tags: string[]) => call<WorkbenchRow>("PUT", `${row(c, r)}/tags`, { tags }),
  setRowNote: (c: number | string, r: number | string, note: string | null) => call<WorkbenchRow>("PUT", `${row(c, r)}/note`, { note }),

  findLineages: (name = "", domain = "", limit = 50) =>
    call<Lineage[]>("GET", `/api/formulations/versions?${new URLSearchParams({ name, domain, limit: String(limit) })}`),
  lineage: (id: string) => call<Lineage>("GET", `/api/formulations/versions/${enc(id)}`),
  versionDetail: (id: string) => call<{ id: string; lineage_id: string; name: string; version: number; snapshot: Partial<Formulation> }>(
    "GET", `/api/formulations/versions/detail/${enc(id)}`),
  diffVersions: (from: string, to: string) => call<VersionDiff>("GET", `/api/formulations/versions/${enc(from)}/diff/${enc(to)}`),
  saveVersion: (body: { formulation: Formulation; lineage_id?: string | null; parent_version_id?: string | null; change_summary?: string; created_by?: string; project_id?: string | null }) =>
    call<{ id: string; lineage_id: string; version: number }>("POST", "/api/formulations/versions", body),

  provenance: (type: string, id: string, depth = 3) =>
    call<{ edges: ProvenanceEdge[] }>("GET", `/api/provenance/lineage?${new URLSearchParams({ node_type: type, node_id: id, depth: String(depth) })}`),

  mode: () => call<{ current: string; choices: ModeChoice[] }>("GET", "/api/settings/formulation-mode"),
  setMode: (mode: string) => call<{ mode: string }>("POST", "/api/settings/formulation-mode", { mode }),

  chemicalProfile: (q: string) => call<ChemicalProfile>("GET", `/api/chemical/profile?q=${enc(q)}`),
  validate: (formulations: Formulation[], requirement: unknown) =>
    call<{ formulations: Formulation[]; warnings: string[] }>("POST", "/api/formulations/validate", { formulations, requirement: requirement ?? null }),
  proposeMaterials: (materials: { name: string; role?: string; cas_no?: string; smiles?: string }[]) =>
    call<Record<string, number>>("POST", "/api/materials/propose-many", { materials, source: "formula" }),
  manual: (formulation: Formulation, requirement: unknown) =>
    call<{ formulation: Formulation; warnings: string[] }>("POST", "/api/formulations/manual", { formulation, requirement: requirement ?? null }),
  adopt: (recommendId: string, body: { adopt_signal: string; formula_index?: number; formula_snapshot: Record<string, unknown>; project_id?: string | null }) =>
    call<{ adopted: boolean; experiment_validated?: boolean }>("POST", `/api/formulations/recommend/${enc(recommendId)}/adopt`, body),
  outcome: (recommendId: string) =>
    call<{ adopted: boolean; experiment_validated: boolean; formula_name: string | null }>("GET", `/api/formulations/recommend/${enc(recommendId)}/outcome`),
};

/** Save a text file in the browser. */
export function saveText(text: string, filename: string, type = "text/csv;charset=utf-8") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export const slug = (name: string) => name.replace(/[^a-z0-9]+/gi, "_").replace(/^_+|_+$/g, "").toLowerCase() || "formula";

/** POST /api/formulations/export/pdf → the card as a PDF blob (the endpoint answers binary, so not ``call``). */
export async function exportFormulationPdf(formulation: Formulation, lang: "zh-CN" | "en-US"): Promise<Blob> {
  const res = await authFetch("/api/formulations/export/pdf", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ formulation, lang }),
  });
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try { detail = ((await res.json()) as { detail?: unknown }).detail ?? detail; } catch { /* not JSON */ }
    throw new ApiError(res.status, detail);
  }
  return res.blob();
}

/** Save a blob in the browser. */
export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

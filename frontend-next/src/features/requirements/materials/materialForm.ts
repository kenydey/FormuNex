/** Pure helpers of the raw-material catalogue (legacy MaterialsPanel + utils/chemicalLookup). */

export interface Supplier {
  name: string;
  url?: string | null;
  product_url?: string | null;
  country?: string | null;
  currency?: string | null;
  price_cny_per_kg?: number | null;
  price_source?: string | null;
  price_observed_at?: string | null;
  moq?: string | null;
  pack_size?: string | null;
  lead_time_days?: number | null;
  stale_price?: boolean;
}

export interface MaterialView { name: string; role: string; origin?: string; availability?: string; archived?: boolean; spec: Record<string, unknown> }

export interface MaterialDraft {
  name: string;
  role: string;
  zh_name?: string | null;
  cas_no?: string | null;
  formula?: string | null;
  smiles?: string | null;
  molar_mass?: number | null;
  price_cny_per_kg?: number | null;
  voc_contrib?: number | null;
  density_gcm3?: number | null;
  suppliers_json: Supplier[];
}

export interface ChemicalLookup {
  query: string;
  cas: string;
  iupac_name: string;
  zh_name: string;
  formula: string;
  smiles?: string | null;
  molar_mass?: number | null;
  found: boolean;
  source: string;
  surechembl?: { chemical_id?: string | null; global_frequency?: number | null } | null;
  suppliers?: { name: string; url?: string | null; product_url?: string | null }[];
}

export const ROLES = ["resin", "additive", "inhibitor", "solvent", "crosslinker", "surfactant", "catalyst", "pigment", "filler"];
export const AVAILABILITY = ["in_stock", "restricted", "discontinued"] as const;

export const EMPTY_DRAFT: MaterialDraft = { name: "", role: "additive", suppliers_json: [] };

const str = (v: unknown) => (typeof v === "string" ? v : v == null ? "" : String(v));
const num = (v: unknown): number | null => {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};
const blank = (v: unknown) => {
  const s = str(v).trim();
  return s ? s : null;
};

export function draftFromView(m: MaterialView): MaterialDraft {
  const s = m.spec ?? {};
  return {
    name: m.name,
    role: m.role || str(s.role) || "additive",
    zh_name: str(s.zh_name),
    cas_no: str(s.cas_no),
    formula: str(s.formula),
    smiles: str(s.smiles),
    molar_mass: num(s.molar_mass),
    price_cny_per_kg: num(s.price_cny_per_kg),
    voc_contrib: num(s.voc_contrib),
    density_gcm3: num(s.density_gcm3),
    suppliers_json: Array.isArray(s.suppliers_json)
      ? (s.suppliers_json as Supplier[]).filter((x) => x && typeof x === "object").map((x) => ({ ...x, name: x.name ?? "", price_source: x.price_source ?? "manual" }))
      : [],
  };
}

/** POST /api/materials body (upsert; empty strings become null, nameless suppliers dropped). */
export function payloadFromDraft(d: MaterialDraft) {
  return {
    name: d.name.trim(),
    role: d.role.trim(),
    zh_name: blank(d.zh_name),
    cas_no: blank(d.cas_no),
    formula: blank(d.formula),
    smiles: blank(d.smiles),
    molar_mass: num(d.molar_mass),
    price_cny_per_kg: num(d.price_cny_per_kg),
    voc_contrib: num(d.voc_contrib),
    density_gcm3: num(d.density_gcm3),
    suppliers_json: d.suppliers_json
      .map((s) => ({
        name: str(s.name).trim(),
        url: blank(s.url),
        product_url: blank(s.product_url),
        country: blank(s.country),
        currency: blank(s.currency),
        price_cny_per_kg: num(s.price_cny_per_kg),
        price_source: blank(s.price_source) ?? "manual",
        price_observed_at: blank(s.price_observed_at),
        moq: blank(s.moq),
        pack_size: blank(s.pack_size),
        lead_time_days: num(s.lead_time_days),
      }))
      .filter((s) => s.name.length > 0),
  };
}

/** Lookup query: CAS > name > SMILES > Chinese name. */
export function lookupQuery(d: Partial<MaterialDraft>): string {
  return (str(d.cas_no).trim() || str(d.name).trim() || str(d.smiles).trim() || str(d.zh_name).trim());
}

/** Merge a /api/chemical/lookup hit into the draft: found values win, the name is only filled when empty. */
export function applyLookup(d: MaterialDraft, r: ChemicalLookup): MaterialDraft {
  return {
    ...d,
    name: d.name.trim() ? d.name : r.iupac_name || r.zh_name || d.name,
    zh_name: r.zh_name || d.zh_name,
    cas_no: r.cas || d.cas_no,
    formula: r.formula || d.formula,
    smiles: r.smiles || d.smiles,
    molar_mass: r.molar_mass ?? d.molar_mass,
    // PubChem vendors expose no commercial terms: keep name / homepage / product page only.
    suppliers_json: r.suppliers?.length
      ? r.suppliers.map((s) => ({ name: s.name, url: s.url ?? null, product_url: s.product_url ?? null, price_source: "manual" }))
      : d.suppliers_json,
  };
}

/** Which lookup provider answered (for the status line). */
export function lookupSource(r: Pick<ChemicalLookup, "source" | "surechembl">): { kind: "surechembl" | "catalog" | "pubchem" | "other"; label: string; id?: string | null; freq?: number | null } {
  if (r.source === "surechembl") return { kind: "surechembl", label: "SureChEMBL", id: r.surechembl?.chemical_id, freq: r.surechembl?.global_frequency };
  if (r.source === "catalog") return { kind: "catalog", label: "catalog" };
  if (r.source?.startsWith("pubchem")) return { kind: "pubchem", label: "PubChem" };
  return { kind: "other", label: r.source || "lookup" };
}

export function nextAvailability(cur: string | undefined | null): string {
  const c = cur ?? "in_stock";
  return c === "in_stock" ? "restricted" : c === "restricted" ? "discontinued" : "in_stock";
}

/** Structure search routing: bracket SMARTS → substructure; something with branches → scaffold; else substructure. */
export function structureMode(query: string): "substructure" | "scaffold" {
  const q = query.trim();
  if (q.startsWith("[")) return "substructure";
  if (q.includes("(")) return "scaffold";
  return "substructure";
}

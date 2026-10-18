/** Wiki (/api/wiki/*) and materials knowledge-graph (/api/kg/*) payloads used by this feature. */

export interface WikiPageItem {
  id: string;
  path: string;
  kind: string;
  title: string;
  norm_key?: string;
  entity_id?: string | null;
  source_ids?: string[];
  flags?: string[];
  revision?: number;
  updated_at?: string | null;
}

export interface WikiPageDetail extends WikiPageItem { markdown: string }

export interface WikiSearchHit extends WikiPageItem { snippet?: string; rank?: number }

export interface WikiFlagAction {
  id: string;
  label: string;
  hint?: string;
  /** Path to open (S1 action target). */
  target?: string;
  /** S5: broken [[target]] for apply-broken chips. */
  broken?: string;
  /** S5: existing wiki path to rewrite / append toward. */
  replacement_path?: string;
  /** S5: rewrite | append_related */
  mode?: string;
}

export interface WikiFlagItem extends WikiPageItem { actions?: WikiFlagAction[] }

/** List row: a page, optionally with flag actions (flags mode) or a search snippet. */
export interface WikiListRow extends WikiPageItem { actions?: WikiFlagAction[]; snippet?: string }

export interface DossierMeta {
  section_revisions?: Record<string, number>;
  flags?: Record<string, boolean>;
  project_id?: string;
  template?: string;
}

export interface WikiDossier {
  page_id?: string;
  path: string;
  data_path?: string;
  title?: string;
  flags?: string[];
  revision?: number;
  markdown: string;
  data?: (DossierMeta & Record<string, unknown>) | null;
}

export interface WikiLintResult { ok: boolean; scanned?: number; flagged?: number; orphan_count?: number; broken_count?: number }
export interface WikiSweepResult { ok: boolean; scanned?: number; cleared?: number; still_flagged?: number }

export interface WikiPageGraphNode {
  id: string;
  path: string;
  label: string;
  kind: string;
  flags?: string[];
  degree?: number;
  degree_in?: number;
  degree_out?: number;
  /** Weak-component community id (stable, size-ranked). */
  community?: number;
}

export interface WikiPageGraphEdge { source: string; target: string; weight?: number; relation_type?: string; extraction_method?: string }

export interface WikiPageGraphMeta {
  node_count: number;
  edge_count: number;
  broken_links?: number;
  truncated?: boolean;
  elapsed_ms?: number;
  orphan_count?: number;
  community_count?: number;
  weighting?: string;
}

export interface WikiPageGraphInsightPage { path: string; label: string; kind: string; degree?: number }
export interface WikiPageGraphBrokenLink { source: string; source_label: string; target: string; reason?: string }

export interface WikiPageGraphInsights {
  orphans: WikiPageGraphInsightPage[];
  isolates: WikiPageGraphInsightPage[];
  broken: WikiPageGraphBrokenLink[];
  components?: { count: number; largest: number };
}

export interface WikiPageGraphResponse {
  ok: boolean;
  nodes: WikiPageGraphNode[];
  edges: WikiPageGraphEdge[];
  meta: WikiPageGraphMeta;
  insights?: WikiPageGraphInsights;
}

export interface EnvFlag { attr: string; label?: string; value: boolean }

// ---- materials knowledge graph ----

export interface KgGraphNode { id: string; label: string; kind: string; degree?: number; degree_in?: number; degree_out?: number }

export interface KgGraphResponse {
  ok: boolean;
  nodes: KgGraphNode[];
  edges: WikiPageGraphEdge[];
  meta: { node_count: number; edge_count: number; truncated?: boolean; elapsed_ms?: number; relation_types?: string[]; scanned_links?: number; backend?: string };
}

export interface KgStats {
  enabled: boolean;
  entities: number;
  mentions: number;
  links: number;
  entities_by_kind?: Record<string, number>;
  links_by_type?: Record<string, number>;
  relation_layer_empty?: boolean;
  warnings?: string[];
}

export interface Neo4jStats { enabled?: boolean; reachable?: boolean; stats?: Record<string, number> }
export interface Neo4jCompound { uid: string; name?: string | null; cas_number?: string | null; smiles?: string | null }

export interface KgRelationEvidence { source_id: string; sentence: string; confidence: number; extraction_method: string }

export interface KgRelation {
  id: string;
  source_entity_id: string;
  target_entity_id: string;
  relation_type: string;
  confidence: number;
  evidence: KgRelationEvidence[];
  extraction_method: string;
}

export interface KgResolve {
  query: string;
  chemicals: { id: string; canonical_name: string; cas_no?: string | null }[];
  trade_products: { id: string; trade_name: string }[];
  top_relations: KgRelation[];
}

export interface KgSubstitute {
  entity_id: string;
  entity_name: string;
  relation_type: string;
  confidence: number;
  hops: number;
  contradiction_flag?: boolean;
  contradiction_detail?: string;
}

export interface KgContradiction {
  target_entity_id: string;
  target_entity_name?: string;
  literature_relation: string;
  measured_property?: string;
  measured_value?: number | null;
  recommended_action?: string;
}

export interface KgPath { found: boolean; hops: number; steps: { relation?: KgRelation | null; entity_id: string; entity_name?: string }[] }

export interface KgFeedbackStats { measured_total: number; measured_performance: number; measured_material?: number; measured_domain?: number }
export interface KgFeedbackReport { alert: string | null }

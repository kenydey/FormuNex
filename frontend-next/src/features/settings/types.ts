/** Wire types of the settings-centre endpoints (backend/app/api/settings.py, skills.py, connectors.py, …). */

export interface LlmModelOption { id: string; label: string; recommended?: boolean }

export interface LlmProviderInfo {
  id: string;
  label: string;
  /** null for a bring-your-own endpoint, which has no default to offer. */
  base_url?: string | null;
  models: LlmModelOption[];
}

export interface VisionSettings {
  provider: string;
  model: string;
  base_url?: string | null;
  key_set: boolean;
  inherits: boolean;
  configured: boolean;
  hint: string;
}

export interface LlmSettings {
  provider: string;
  model: string;
  key_set: boolean;
  base_url?: string | null;
  providers: LlmProviderInfo[];
  vision: VisionSettings;
}

export interface ConnectionTest { ok: boolean; provider?: string; model?: string; message: string }

export interface ModelsRefresh {
  ok: boolean;
  provider: string;
  base_url?: string | null;
  source: "remote" | "static";
  models: LlmModelOption[];
  message: string;
}

export interface SecretStatus {
  id: string;
  env_key: string;
  label: string;
  group: string;
  set: boolean;
  /** A key comes back masked; an address or e-mail (`secret: false`) comes back as it is. */
  masked: string;
  secret?: boolean;
}

export interface EmbeddingModel {
  id: string;
  label: string;
  langs: string;
  note: string;
  dim: number;
  size_estimate: string;
  cached: boolean;
  current: boolean;
}

export interface EmbeddingDownloadTask {
  task_id: string;
  model_id: string;
  status: "pending" | "downloading" | "done" | "failed";
  progress: number;
  error?: string | null;
}

export interface EmbeddingSwitchStatus { status: "idle" | "running" | "done" | "failed"; progress: number; error?: string | null }

export interface EnvFlag {
  attr: string;
  env_key: string;
  label: string;
  description: string;
  category: string;
  category_label: string;
  hint: string;
  value: boolean;
  default: boolean;
  maturity?: string;
}

export interface EnvVar {
  attr: string;
  env_key: string;
  label: string;
  description: string;
  category: string;
  category_label: string;
  value: number | null;
  default: number | null;
}

export interface EnvFlagsResponse { flags: EnvFlag[]; vars?: EnvVar[] }

export interface UnifiedSkill {
  id: string;
  kind: string;
  title: string;
  summary: string;
  icon: string;
  activation_policy: string;
  origin?: string;
  category?: string;
  enabled: boolean;
  pinned: boolean;
}

export interface SkillsCatalog { skills: UnifiedSkill[]; prefs: Record<string, unknown> }

export interface SkillInstallPreview {
  name: string;
  description: string;
  summary: string;
  allowed_tools: string[];
  rejected_tools: string[];
  origin: string;
  source_url: string;
  pinned_sha: string;
  file_count: number;
  warnings: string[];
  errors: string[];
}

export interface SkillInstallResponse {
  ok: boolean;
  dry_run: boolean;
  install_id?: string | null;
  skill_id?: string | null;
  installed: boolean;
  detail: string;
  preview?: SkillInstallPreview;
  catalog?: SkillsCatalog;
}

export interface SkillPack {
  id: string;
  pack_dir: string;
  title: string;
  summary: string;
  description: string;
  category: string;
  installed: boolean;
  origin: string;
}

export interface SkillUpdateCheck {
  skill_id: string;
  update_available: boolean;
  checkable: boolean;
  reason: string;
  local_sha: string;
  remote_sha: string;
  source_url: string;
}

export interface DependencyInfo {
  pip_name: string;
  import_name: string;
  extra: string;
  enables: string;
  installed: boolean;
  version: string | null;
}

export interface DependencyList { dependencies: DependencyInfo[]; online_core_missing: string[]; install_enabled?: boolean }

export interface DependencyInstallResult { ok: boolean; returncode?: number; summary: string; stdout?: string; stderr?: string }

export interface ChemToolsStatus {
  enabled: boolean;
  capabilities: Record<string, { available: boolean; hint?: string | null }>;
}

export interface KbStats {
  enabled: boolean;
  sources: number;
  sources_by_kind: Record<string, number>;
  chunks: number;
  embedded_chunks: number;
  vector_mode?: "semantic" | "degraded" | "keyword" | "empty";
  vector_hint?: string;
  rag_backend?: string;
  products?: number;
  quality_gate_drops?: {
    retrieval?: { blocked_domain?: number; garbage_snippet?: number; wiki_track?: number };
    ingest?: { blocked_domain?: number; garbage_snippet?: number; wiki_track?: number };
  };
  sources_active?: number;
  sources_archived?: number;
  chunks_active?: number;
  scan_limit?: number;
  scan_pressure?: number;
  scan_near_cap?: boolean;
  suppliers_json_dual_write?: boolean;
  embedding_model?: string;
  reindex_hint?: string;
  embedding_catalog?: { id: string; label: string }[];
}

export interface KbReindexResult { reindexed_sources: number; reindexed_chunks: number; total_chunks: number; embedded_chunks: number }

export interface RetentionPurgeResult {
  ok: boolean;
  dry_run: boolean;
  days: number;
  candidates: { source_id: string; title?: string | null; archived_at?: string | null }[];
  candidate_count: number;
  purged: string[];
  purged_count: number;
  errors: { source_id?: string; error?: string }[];
}

export interface KgStats {
  enabled: boolean;
  entities: number;
  mentions: number;
  links: number;
  relation_layer_empty?: boolean;
  warnings?: string[];
}

export interface KbIntegrity { healthy: boolean; total_orphans: number; external_backend: boolean }

export interface KgCalibration {
  kg_enabled: boolean;
  kg_inhibits_penalty: number;
  kg_synergizes_bonus: number;
  kg_measured_bonus: number;
  kg_measured_metric_bonus?: number;
  kg_measured_metric_penalty?: number;
  counts: { inhibits: number; substitutes: number; synergizes: number };
}

export interface KgRebuildReport {
  linked_sources: number;
  entities_upserted: number;
  mentions_upserted: number;
  links_created: number;
  failed_sources?: number;
}

export interface Evidence { source: string; identifier: string; title: string; snippet: string; relevance: number }

export interface KbChunk { id: string; source_id: string; text: string; heading_path?: string }

export interface KgRetrieve {
  plan: { mode?: string };
  evidence: Evidence[];
  stats: { semantic_hits?: number; mention_hits?: number; chunks_after_dedupe?: number };
}

export interface KbProduct {
  trade_name: string;
  grade?: string;
  supplier?: string;
  generic_name?: string;
  cas?: string;
  role?: string;
  mention_count?: number;
}

export interface Neo4jRawStats { enabled?: boolean; reachable?: boolean; stats?: Record<string, number> }

export interface Neo4jCompound {
  uid: string;
  name?: string | null;
  smiles?: string | null;
  cas_number?: string | null;
  molecular_weight?: number | null;
}

export interface Neo4jFormulation { uid: string; name?: string | null; target_property?: string | null }

export interface Neo4jHit { uid?: string; name?: string; co_count?: number }

export interface McpApprovalItem {
  request_id: number;
  server_id: string;
  tool_name: string;
  session_id?: string | null;
  project_id?: string | null;
  age_s: number;
  /** Tool-call arguments (secret-looking keys redacted to ``***``); null when the call recorded none. */
  arguments?: Record<string, unknown> | null;
}

export interface BuiltinConnector {
  id: string;
  display_name: string;
  kind: string;
  description: string;
  use_when: string;
  sources: string[];
  readonly: boolean;
  enabled: boolean;
}

export interface McpServerConfig {
  id: string;
  command: string;
  args?: string[];
  enabled?: boolean;
  /** Values may be secrets: only the keys are ever shown. */
  env?: Record<string, string>;
  transport?: string;
}

export interface ConnectorsResponse {
  builtin: BuiltinConnector[];
  mcp: McpServerConfig[];
  mcp_client_enabled: boolean;
  connectors_builtin_enabled: boolean;
}

export interface McpImportPreview {
  servers: { id: string; command: string; args: string[]; env_keys: string[]; transport: string; warnings: string[]; errors: string[] }[];
  source: string;
  source_url: string;
  warnings: string[];
  errors: string[];
}

export interface McpImportResponse {
  ok: boolean;
  dry_run: boolean;
  import_id?: string | null;
  imported: boolean;
  detail: string;
  preview?: McpImportPreview;
  mcp?: McpServerConfig[];
}

export interface OrgDashboardStats {
  total_experiments: number;
  total_campaigns: number;
  total_projects: number;
  active_projects: number;
  by_domain: Record<string, number>;
  top_performers: {
    metric: string;
    value: number;
    experiment_id: number;
    project_title: string;
    formulation_preview: string;
    measured_at: string;
  }[];
  ingredient_frequency: { ingredient_name: string; experiment_count: number; avg_weight_pct: number; best_result_metric: string | null }[];
  convergence_rate: number;
  avg_rounds_to_converge: number;
  recent_activity: { experiments_added: number; campaigns_created: number };
}

import type { ThinkingStep } from "@/api/stream";

/** One retrieved snippet with its citation (backend ``domain.schemas.Evidence``). */
export interface Evidence {
  source: string;
  identifier: string;
  title: string;
  snippet: string;
  relevance: number;
  url?: string | null;
  url_alt?: string | null;
  page?: number | null;
  page_end?: number | null;
  is_seed_corpus?: boolean;
}

export interface ChatNotice { code: string; message: string }

export interface SourcedClaim {
  text: string;
  chunk_ids: string[];
  confidence: number;
  status: "supported" | "weak" | "unsupported";
  raw_verdict?: string | null;
}

export interface ClarificationOption {
  ambiguous_term: string;
  possible_meanings: string[];
  question: string;
  candidate_entity_ids?: string[];
}

export interface EvidenceProvenance { evidence_availability?: string; notes?: string[] }

export interface McpPermission { server_id: string; tool_name: string; session_id?: string | null }

export interface ChatDone {
  answer: string;
  citations?: Evidence[] | null;
  kb_chunks_used?: number | null;
  clarification?: ClarificationOption | null;
  rewritten_query?: string | null;
  sourced_claims?: SourcedClaim[] | null;
  evidence_provenance?: EvidenceProvenance | null;
  reviewer_fix?: { run_id?: string | null } | null;
  notices?: ChatNotice[] | null;
  mcp_permission_required?: McpPermission | null;
  data_sources?: string[] | null;
}

/** Frames of POST /api/chat/stream, plus ``step`` frames the page emits itself (deep-mode PageIndex pre-pass). */
export type ChatStreamEvent =
  | { type: "phase"; phase: string }
  | { type: "meta"; kb_used: number; rewritten_query?: string | null; source_count?: number; mode?: string }
  | { type: "tool_start"; name: string; args?: Record<string, unknown>; label?: string }
  | { type: "tool_result"; name: string; ok: boolean; summary?: string }
  | { type: "token"; delta: string }
  | ({ type: "done" } & ChatDone)
  | { type: "error"; message: string; code?: string }
  | { type: "step"; id: string; title: string; status: NonNullable<ThinkingStep["status"]>; detail?: string };

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  streaming?: boolean;
  phase?: string;
  toolStatus?: string | null;
  steps?: ThinkingStep[];
  citations?: Evidence[];
  kbChunksUsed?: number;
  sourceCount?: number;
  rewrittenQuery?: string | null;
  reviewRunId?: string | null;
  sourcedClaims?: SourcedClaim[] | null;
  clarification?: ClarificationOption | null;
  evidenceProvenance?: EvidenceProvenance | null;
  notices?: ChatNotice[] | null;
  mcpPermission?: McpPermission | null;
  error?: string | null;
}

export interface SessionInfo {
  session_id: string;
  history_count: number;
  has_context: boolean;
  updated_at?: string | null;
  project_id?: string | null;
  title?: string | null;
}

export interface UnifiedSkill {
  id: string;
  kind: string;
  title: string;
  summary: string;
  icon: string;
  enabled: boolean;
}

export interface BuiltinConnector { id: string; display_name: string; description: string; enabled: boolean }
export interface McpServerConfig { id: string; command: string; enabled?: boolean }

export interface StructureHit { name: string; role: string; smiles?: string | null; similarity: number }
export interface StructureResult {
  recognized: boolean;
  smiles: string | null;
  hits: StructureHit[];
  confidence?: number | null;
  warnings: string[];
  error: string | null;
}

export interface PlatformHealth {
  status: "ok" | "degraded";
  database?: { ok: boolean; scheme: string };
  task_broker?: { required: boolean; reachable: boolean };
  parsers?: Record<string, boolean>;
  datalab?: { required: boolean; reachable: boolean; hint?: string; ledger_mode?: string };
}

export interface SessionPlan {
  plan_id: string;
  session_id: string;
  phases: { name: string; steps: { desc: string; status: string }[] }[];
  status: "pending" | "approved" | "rejected";
  created_at?: string;
  decided_at?: string | null;
  decided_by?: string | null;
}

export interface SessionPlanPendingItem { plan_id: string; session_id: string; created_at: number; phase_names: string[]; step_count: number }

export interface McpApprovalItem {
  request_id: number;
  server_id: string;
  tool_name: string;
  session_id?: string | null;
  project_id?: string | null;
  age_s?: number;
  arguments?: Record<string, unknown> | null;
}

export interface ReviewRunSummary {
  run_id: string;
  session_key?: string | null;
  project_id?: string | null;
  status?: string | null;
  outcome?: string | null;
  stale?: boolean | "unverified" | null;
  stale_reason?: string | null;
  warn_count?: number;
  fail_count?: number;
  unaddressed_count?: number;
  started_at?: number | null;
  finished_at?: number | null;
}

export interface ReviewRunDetail extends ReviewRunSummary {
  dispositions?: Record<string, { status?: string | null; reflag_count?: number; disposition?: string | null }> | null;
}

export interface ReviewChecklist {
  run_id: string;
  outcome: string | null;
  items: { id: string; category: string; statement: string; verdict: "pass" | "flagged" | "n_a"; reviewer_note: string }[];
  summary: { total: number; pass: number; flagged: number; n_a: number };
}

export interface RerunResult { review?: Record<string, unknown> | null; fix?: { run_id?: string | null } | null; final_answer?: string | null }

/** Q&A context needed to re-run an evidence audit. */
export interface ReviewContext { question: string; answer: string; citations?: unknown[]; projectId?: string | null }

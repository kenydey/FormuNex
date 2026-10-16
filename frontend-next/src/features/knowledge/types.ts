export interface Chunk {
  id: string;
  source_id: string;
  ord: number;
  text: string;
  heading_path: string;
  page: number | null;
  meta?: Record<string, unknown> | null;
}

export interface TocNode {
  node_id: string;
  title: string;
  kind: string;
  label: string | null;
  summary: string | null;
  page_range: [number, number] | null;
  chunk_ids: string[];
  table_ids: string[];
  refs: { target?: string; kind?: string }[];
  children: TocNode[];
}

export interface TraceStep { step: number; action: string; node_id: string; title: string; label: string | null; page_range: [number, number] | null; reason: string }
export interface Evidence { node_id: string; title: string; label: string | null; page_range: [number, number] | null; text: string }
export interface DocDepth {
  source_id: string;
  mode: "pageindex" | "stage1";
  reason: string;
  stage1_chunk_ids: string[];
  navigation: null | { mode: string; node_path: string[]; reasoning_trace: TraceStep[]; evidence: Evidence[]; hops: number; chars_read: number; stopped: string };
}

export const pages = (r: [number, number] | null | undefined) =>
  !r ? "" : r[0] === r[1] ? `p.${r[0]}` : `p.${r[0]}–${r[1]}`;

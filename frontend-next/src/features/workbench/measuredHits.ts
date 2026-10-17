/** Metric-aware "measured hits" summary for recommendation cards (ported from legacy ``measuredMetricHits.ts``). */

export interface MeasuredMetricHit {
  material: string;
  metric: string;
  quality: "good" | "poor" | "presence" | string;
  value?: number | null;
  confidence?: number;
  prop_id?: string;
}

export type HitQuality = "good" | "poor" | "presence";

export interface MeasuredHitsSummary { quality: HitQuality; materials: string[]; metrics: string[] }

const RANK: Record<HitQuality, number> = { good: 3, presence: 2, poor: 1 };
const norm = (q: string | undefined): HitQuality => (q === "good" || q === "poor" || q === "presence" ? q : "presence");

/** Strongest quality among the hits (good > presence > poor), or null when there are none. */
export function bestQuality(hits: MeasuredMetricHit[] | null | undefined): HitQuality | null {
  let best: HitQuality | null = null;
  for (const h of hits ?? []) {
    const q = norm(h.quality);
    if (!best || RANK[q] > RANK[best]) best = q;
  }
  return best;
}

export function summarizeHits(hits: MeasuredMetricHit[] | null | undefined): MeasuredHitsSummary | null {
  const quality = bestQuality(hits);
  if (!quality || !hits?.length) return null;
  return {
    quality,
    materials: [...new Set(hits.map((h) => h.material).filter(Boolean))].sort(),
    metrics: [...new Set(hits.map((h) => h.metric).filter(Boolean))].sort(),
  };
}

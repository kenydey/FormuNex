import type { StackStatus } from "@/desktop/bridge";

export const DOCKER_DOWNLOAD = "https://www.docker.com/products/docker-desktop/";
/** Plan §2.2: the full desktop stack needs about 12 GB (to be confirmed by measurement). */
export const MIN_MEMORY_GB = 12;
/** Services that must be up for the app to work (beat / migrate are not required to be "running"). */
export const REQUIRED_SERVICES = ["postgres", "redis", "kg", "api", "worker"];

export function formatBytes(n: number): string {
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(1)} GB`;
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${n} B`;
}

/** Docker's memory in GB (one decimal) when below the recommendation, else null. */
export function memoryWarning(memBytes: number | null): string | null {
  if (!memBytes) return null;
  const gb = memBytes / 1024 ** 3;
  return gb + 0.25 < MIN_MEMORY_GB ? gb.toFixed(1) : null;
}

/** The stack is usable when the API answers /health and the required services run. */
export function stackReady(status: StackStatus | null): boolean {
  if (!status?.health.reachable) return false;
  const running = new Set(status.services.filter((s) => s.state === "running").map((s) => s.service));
  return REQUIRED_SERVICES.every((s) => running.has(s));
}

export interface SeedSummary {
  /** The data set was already imported (unchanged digest): nothing was rewritten. */
  skipped: boolean;
  substances: number | null;
  transport: number | null;
  iecsc: number | null;
  oel: number | null;
}

/** The regulatory importer prints its result as indented JSON after any log lines; read the counts. */
export function parseSeedResult(lines: string[]): SeedSummary | null {
  const start = lines.lastIndexOf("{");
  if (start < 0) return null;
  let doc: { skipped?: boolean; stats?: Record<string, unknown> };
  try {
    doc = JSON.parse(lines.slice(start).join("\n"));
  } catch {
    return null;
  }
  const stats = doc.stats ?? {};
  const num = (k: string) => (typeof stats[k] === "number" ? (stats[k] as number) : null);
  return {
    skipped: !!doc.skipped,
    substances: num("substances_total"),
    transport: num("transport_rows"),
    iecsc: num("iecsc_marked"),
    oel: num("oel_rows"),
  };
}

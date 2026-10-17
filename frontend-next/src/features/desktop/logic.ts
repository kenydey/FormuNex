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

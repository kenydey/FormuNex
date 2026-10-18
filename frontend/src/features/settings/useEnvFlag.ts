import { useQuery } from "@tanstack/react-query";
import { call } from "@/api/client";
import type { EnvFlagsResponse } from "./types";

export const ENV_FLAGS_KEY = ["settings", "env-flags"] as const;

/**
 * Read one boolean FORMUNEX_* toggle (GET /api/settings/env-flags). Shares the settings page's query, so a save there
 * updates every reader. ``fallback`` is used until the first response and whenever the request fails, so UI behind a
 * toggle never vanishes just because the API is slow or down.
 */
export function useEnvFlag(attr: string, fallback: boolean): boolean {
  const q = useQuery({ queryKey: ENV_FLAGS_KEY, queryFn: () => call<EnvFlagsResponse>("GET", "/api/settings/env-flags"), staleTime: 60_000 });
  const flag = q.data?.flags?.find((f) => f.attr === attr);
  return flag ? Boolean(flag.value) : fallback;
}

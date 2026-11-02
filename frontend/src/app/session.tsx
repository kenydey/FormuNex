import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getAccessToken, logoutBody, refreshAccessToken, rememberRefreshToken, setAccessToken } from "@/api/auth";
import { ApiError, call } from "@/api/client";
import { useUi } from "./store";

export interface Me {
  id: string;
  username: string;
  display_name: string;
  email: string | null;
  roles: string[];
  permissions: string[];
  project_permissions: Record<string, string[]>;
}

interface Session {
  me: Me | null;
  ready: boolean;
  /** Stage 5 / SR-075: a non-401 /me failure lands here instead of
   * becoming an unhandled rejection that bounces a valid session to
   * the login page. Null on success; RequireAuth renders it. */
  error: unknown | null;
  can: (permission: string, projectId?: string | null) => boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  reload: () => Promise<void>;
}

const Ctx = createContext<Session | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const setProject = useUi((s) => s.setProject);
  const [me, setMe] = useState<Me | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<unknown | null>(null);

  const reload = useCallback(async () => {
    setError(null);
    try {
      if (!getAccessToken()) await refreshAccessToken();
      if (!getAccessToken()) {
        setMe(null);
        return;
      }
      setMe(await call<Me>("GET", "/api/identity/me"));
    } catch (e) {
      setMe(null);
      if (e instanceof ApiError && e.status === 401) return;
      // Recorded, not swallowed: explicit callers (login) still see the
      // throw, while the mount effect below only needs to avoid an
      // unhandled rejection — the error page reads this state.
      setError(e);
      throw e;
    }
  }, []);

  useEffect(() => {
    reload().catch(() => undefined).finally(() => setReady(true));
  }, [reload]);

  const login = useCallback(async (username: string, password: string) => {
    const out = await call<{ access_token: string; refresh_token?: string }>("POST", "/api/identity/login", { username, password });
    await rememberRefreshToken(out.refresh_token);
    setAccessToken(out.access_token);
    await reload();
  }, [reload]);

  const logout = useCallback(async () => {
    try {
      await call("POST", "/api/identity/logout", await logoutBody());
    } finally {
      setAccessToken(null);
      setMe(null);
      // SR-075: the persisted project selection belongs to the outgoing
      // user; leaving it would hand the next login the previous user's
      // project context.
      setProject(null);
      qc.clear();
    }
  }, [qc, setProject]);

  const can = useCallback(
    (permission: string, projectId?: string | null) =>
      !!me && (me.permissions.includes(permission) ||
        (!!projectId && (me.project_permissions[projectId] || []).includes(permission))),
    [me],
  );

  const value = useMemo(() => ({ me, ready, error, can, login, logout, reload }), [me, ready, error, can, login, logout, reload]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession(): Session {
  const s = useContext(Ctx);
  if (!s) throw new Error("useSession outside SessionProvider");
  return s;
}

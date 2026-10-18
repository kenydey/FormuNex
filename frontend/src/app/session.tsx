import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getAccessToken, logoutBody, refreshAccessToken, rememberRefreshToken, setAccessToken } from "@/api/auth";
import { ApiError, call } from "@/api/client";

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
  can: (permission: string, projectId?: string | null) => boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  reload: () => Promise<void>;
}

const Ctx = createContext<Session | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [me, setMe] = useState<Me | null>(null);
  const [ready, setReady] = useState(false);

  const reload = useCallback(async () => {
    if (!getAccessToken()) await refreshAccessToken();
    if (!getAccessToken()) {
      setMe(null);
      return;
    }
    try {
      setMe(await call<Me>("GET", "/api/identity/me"));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setMe(null);
      else throw e;
    }
  }, []);

  useEffect(() => {
    reload().finally(() => setReady(true));
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
      qc.clear();
    }
  }, [qc]);

  const can = useCallback(
    (permission: string, projectId?: string | null) =>
      !!me && (me.permissions.includes(permission) ||
        (!!projectId && (me.project_permissions[projectId] || []).includes(permission))),
    [me],
  );

  const value = useMemo(() => ({ me, ready, can, login, logout, reload }), [me, ready, can, login, logout, reload]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession(): Session {
  const s = useContext(Ctx);
  if (!s) throw new Error("useSession outside SessionProvider");
  return s;
}

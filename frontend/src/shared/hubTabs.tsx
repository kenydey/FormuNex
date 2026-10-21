import { Navigate, useSearchParams } from "react-router-dom";

/**
 * Hub tab state carried in one query key (default ``tab``). Switching tabs keeps every
 * other query param (``?campaign=``, ``?row=``, ``?plan=``, ``?q=``, ``?view=`` …), because
 * the pages merged into a hub still read their own params.
 */
export function useHubTab(param: string, keys: string[], fallback: string): [string, (key: string) => void] {
  const [params, setParams] = useSearchParams();
  const raw = params.get(param);
  const active = raw !== null && keys.includes(raw) ? raw : fallback;
  const onChange = (key: string) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (key === fallback) next.delete(param);
      else next.set(param, key);
      return next;
    });
  };
  return [active, onChange];
}

/** Legacy route → hub tab, keeping the rest of the query string (e.g. ``?campaign=&row=``). */
export function TabRedirect({ to, tab, param = "tab" }: { to: string; tab: string; param?: string }) {
  const [params] = useSearchParams();
  const next = new URLSearchParams(params);
  next.set(param, tab);
  return <Navigate to={`${to}?${next.toString()}`} replace />;
}

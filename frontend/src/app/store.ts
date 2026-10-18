import { create } from "zustand";
import { persist } from "zustand/middleware";

export type ThemeMode = "light" | "dark";
export type Locale = "zh-CN" | "en-US";

interface UiState {
  theme: ThemeMode;
  locale: Locale;
  projectId: string | null;
  setTheme: (t: ThemeMode) => void;
  setLocale: (l: Locale) => void;
  setProject: (id: string | null) => void;
}

/** UI-only state (方案 §3.1: Zustand for UI state; server state lives in TanStack Query). */
export const useUi = create<UiState>()(
  persist(
    (set) => ({
      theme: "light",
      locale: "zh-CN",
      projectId: null,
      setTheme: (theme) => set({ theme }),
      setLocale: (locale) => set({ locale }),
      setProject: (projectId) => set({ projectId }),
    }),
    { name: "formunex-ui" },
  ),
);

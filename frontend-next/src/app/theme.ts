import { theme as antdTheme, type ThemeConfig } from "antd";
import type { ThemeMode } from "./store";

export function themeConfig(mode: ThemeMode): ThemeConfig {
  return {
    algorithm: mode === "dark" ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
    token: { colorPrimary: "#1f5f8b", borderRadius: 6, fontFamily:
      "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', sans-serif" },
  };
}

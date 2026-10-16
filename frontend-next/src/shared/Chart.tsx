import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { useUi } from "@/app/store";

/** ECharts with the app theme (方案 §3.1). */
export function Chart({ option, height = 360, onEvents }: { option: EChartsOption; height?: number; onEvents?: Record<string, (p: unknown) => void> }) {
  const theme = useUi((s) => s.theme);
  return <ReactECharts option={{ backgroundColor: "transparent", ...option }} theme={theme === "dark" ? "dark" : undefined}
    style={{ height }} notMerge onEvents={onEvents} />;
}

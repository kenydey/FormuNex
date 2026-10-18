import { Empty, theme } from "antd";
import type { EChartsOption } from "echarts";
import { useEffect, useMemo, useRef } from "react";
import { Chart } from "@/shared/Chart";
import { toGraphSeries, type GraphColorMode, type GraphNodeLike } from "./graph";
import type { WikiPageGraphEdge } from "./types";

type Props = {
  nodes: GraphNodeLike[];
  edges: WikiPageGraphEdge[];
  colorMode?: GraphColorMode;
  height?: number;
  /** Directed arrows (wiki links) or plain lines. */
  directed?: boolean;
  onSelect?: (id: string) => void;
  emptyText?: string;
};

/** Force-directed ECharts graph (wiki [[wikilink]] graph and materials KG): drag, zoom / pan, hover highlights neighbours. */
export function GraphCanvas({ nodes, edges, colorMode = "kind", height = 560, directed = true, onSelect, emptyText }: Props) {
  const { token } = theme.useToken();
  const selectRef = useRef(onSelect);
  useEffect(() => {
    selectRef.current = onSelect;
  }, [onSelect]);
  const option = useMemo<EChartsOption>(() => {
    const { categories, data, links } = toGraphSeries(nodes, edges, colorMode);
    return {
      tooltip: {
        formatter: (p: unknown) => {
          const x = p as { dataType?: string; data: { name?: string; path?: string; kind?: string; value?: number; community?: number; relation?: string; source?: string; target?: string } };
          if (x.dataType === "edge") return `${x.data.source} → ${x.data.target}${x.data.relation ? ` · ${x.data.relation}` : ""}`;
          const d = x.data;
          return [d.name, d.path && d.path !== d.name ? d.path : null, `${d.kind ?? ""} · deg ${d.value ?? 0}${d.community != null ? ` · #${d.community}` : ""}`]
            .filter(Boolean).map((s) => String(s).replace(/</g, "&lt;")).join("<br/>");
        },
      },
      legend: { type: "scroll", bottom: 0, textStyle: { color: token.colorTextSecondary } },
      series: [{
        type: "graph", layout: "force", roam: true, draggable: true, categories, data, links,
        force: { repulsion: 160, edgeLength: [50, 120], gravity: 0.08 },
        edgeSymbol: directed ? ["none", "arrow"] : ["none", "none"], edgeSymbolSize: 6,
        label: { show: data.length <= 120, position: "bottom", fontSize: 10, color: token.colorText, formatter: (p: { name: string }) => p.name.slice(0, 18) },
        lineStyle: { color: token.colorTextQuaternary, opacity: 0.8, curveness: 0.05 },
        itemStyle: { borderColor: token.colorBgContainer, borderWidth: 1 },
        emphasis: { focus: "adjacency", lineStyle: { width: 3 } },
      }],
    };
  }, [nodes, edges, colorMode, directed, token.colorText, token.colorTextSecondary, token.colorTextQuaternary, token.colorBgContainer]);
  const events = useMemo(() => ({
    click: (p: unknown) => {
      const x = p as { dataType?: string; data?: { id?: string } };
      if (x.dataType === "node" && x.data?.id) selectRef.current?.(x.data.id);
    },
  }), []);
  if (nodes.length === 0) return <Empty style={{ paddingBlock: 48 }} description={emptyText} />;
  return <div data-testid="graph-canvas"><Chart option={option} height={height} onEvents={events} /></div>;
}

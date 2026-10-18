/** Product-level full-chain traceability (Phase 2D 2-23): product → revisions → documents / release packages,
 * plus each revision's upstream chain (experiment / formulation version / formulation / project).
 * Rendered as an edge timeline; the formulation-level view lives in formulation/Provenance.tsx. */
import { ProCard } from "@ant-design/pro-components";
import { Empty, Space, Spin, Tag, Timeline, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { ErrorAlert } from "@/shared/feedback";

interface ChainNode { node_type: string; node_id: string; stage: string; label: string }
interface ChainEdge { from_type: string; from_id: string; to_type: string; to_id: string; relation: string }
interface ProductChain { product_id: string; nodes: ChainNode[]; edges: ChainEdge[] }

const STAGE_COLORS: Record<string, string> = { research: "blue", doe: "purple", winning: "green", plm: "gold" };
const shortId = (id: string) => (id.length > 16 ? `${id.slice(0, 12)}…` : id);

export function ProductLineageChain({ productId }: { productId: string }) {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["plm", "product-chain", productId],
    queryFn: () => call<ProductChain>("GET", `/api/provenance/product-chain?product_id=${encodeURIComponent(productId)}`) });
  if (q.isLoading) return <Spin />;
  if (q.error) return <ErrorAlert error={q.error} />;
  const { nodes = [], edges = [] } = q.data ?? {};
  if (!edges.length) return <Empty description={t("plm.chain.empty", "暂无溯源记录（该产品尚无修订版）")} />;
  const byKey = new Map(nodes.map((n) => [`${n.node_type}:${n.node_id}`, n]));
  const stageLabel = (stage: string) => ({
    research: t("plm.chain.stage.research", "研发"),
    doe: t("plm.chain.stage.doe", "DOE"),
    winning: t("plm.chain.stage.winning", "定型"),
    plm: t("plm.chain.stage.plm", "PLM"),
  })[stage] ?? stage;
  const nodeView = (type: string, id: string) => {
    const n = byKey.get(`${type}:${id}`);
    const label = n?.label ?? id;
    const stage = n?.stage ?? "research";
    return (
      <Space size={4}>
        <Tag color={STAGE_COLORS[stage] ?? "default"}>{stageLabel(stage)}</Tag>
        <span title={`${type}:${id}`}>{label.length > 40 ? `${label.slice(0, 40)}…` : label}</span>
        <Typography.Text type="secondary"><code>{shortId(id)}</code></Typography.Text>
      </Space>
    );
  };
  return (
    <Timeline items={edges.map((e, i) => ({
      key: `${e.from_type}:${e.from_id}:${e.to_type}:${e.to_id}:${e.relation}:${i}`,
      color: STAGE_COLORS[byKey.get(`${e.to_type}:${e.to_id}`)?.stage ?? ""] === "default" ? "gray"
        : STAGE_COLORS[byKey.get(`${e.to_type}:${e.to_id}`)?.stage ?? ""] ?? "gray",
      children: (
        <Space wrap>
          {nodeView(e.from_type, e.from_id)}
          <Typography.Text type="secondary" code>—{e.relation}→</Typography.Text>
          {nodeView(e.to_type, e.to_id)}
        </Space>
      ),
    }))} />
  );
}

export function ProductLineageChainCard({ productId }: { productId: string }) {
  const { t } = useTranslation();
  return (
    <ProCard bordered title={t("plm.chain.title", "全链路溯源")} style={{ marginTop: 16 }}>
      <ProductLineageChain productId={productId} />
    </ProCard>
  );
}

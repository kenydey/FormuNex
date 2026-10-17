/** Evidence lineage (provenance) of a node: claim ← source / formulation ← claim / run ← formulation, upstream BFS
 * (legacy ProvenanceLineage). A Drawer for the editor / leaderboard and a routed page lab/provenance/:nodeType/:nodeId. */
import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Drawer, Empty, InputNumber, Space, Spin, Tag, Timeline, Tooltip, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ErrorAlert } from "@/shared/feedback";
import { fx } from "./api";

const COLORS: Record<string, string> = { formulation: "green", claim: "blue", source: "gold", run: "purple", artifact: "default" };
const shortId = (id: string) => (id.length > 16 ? `${id.slice(0, 12)}…` : id);

function NodeBadge({ type, id }: { type: string; id: string }) {
  const { t } = useTranslation();
  const label: Record<string, string> = {
    formulation: t("formulation.prov.formulation", "配方"), claim: t("formulation.prov.claim", "结论"),
    source: t("formulation.prov.source", "文献"), run: t("formulation.prov.run", "实验"), artifact: t("formulation.prov.artifact", "工件"),
  };
  return <Tooltip title={`${type}:${id}`}><Tag color={COLORS[type] ?? "default"}>{label[type] ?? label.artifact} <code>{shortId(id)}</code></Tag></Tooltip>;
}

export function ProvenanceView({ nodeType, nodeId, depth = 3 }: { nodeType: string; nodeId: string; depth?: number }) {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["formulation", "prov", nodeType, nodeId, depth], queryFn: () => fx.provenance(nodeType, nodeId, depth) });
  if (q.isLoading) return <Spin />;
  if (q.error) return <ErrorAlert error={q.error} />;
  const edges = q.data?.edges ?? [];
  if (!edges.length) return <Empty description={t("formulation.prov.empty", "暂无谱系记录（该节点尚未关联文献结论或实验运行）")} />;
  return (
    <Timeline items={edges.map((e, i) => ({
      key: `${e.from_type}:${e.from_id}:${e.to_type}:${e.to_id}:${e.relation}:${i}`,
      color: COLORS[e.to_type] === "default" ? "gray" : COLORS[e.to_type],
      children: <Space wrap><NodeBadge type={e.from_type} id={e.from_id} /><Typography.Text type="secondary" code>—{e.relation}→</Typography.Text><NodeBadge type={e.to_type} id={e.to_id} /></Space>,
    }))} />
  );
}

export function ProvenanceDrawer({ open, onClose, nodeType, nodeId, title }: { open: boolean; onClose: () => void; nodeType: string; nodeId: string; title?: string }) {
  const { t } = useTranslation();
  return (
    <Drawer open={open} onClose={onClose} width={560} destroyOnHidden
      title={`${t("formulation.prov.title", "证据谱系")}${title ? ` · ${title.slice(0, 30)}` : ""}`}>
      <ProvenanceView nodeType={nodeType} nodeId={nodeId} />
    </Drawer>
  );
}

export default function ProvenancePage() {
  const { nodeType = "formulation", nodeId = "" } = useParams();
  const { t } = useTranslation();
  const [depth, setDepth] = useState(3);
  return (
    <PageContainer title={t("formulation.prov.title", "证据谱系")} subTitle={`${nodeType}:${shortId(nodeId)}`}
      extra={<Space>{t("formulation.prov.depth", "上溯深度")}<InputNumber min={1} max={6} value={depth} onChange={(v) => setDepth(v ?? 3)} /></Space>}>
      <ProCard bordered><ProvenanceView nodeType={nodeType} nodeId={nodeId} depth={depth} /></ProCard>
    </PageContainer>
  );
}

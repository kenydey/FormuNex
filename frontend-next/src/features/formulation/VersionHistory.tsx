/** Formulation revision history: list / lineage graph, diff between revisions, save a new revision (legacy VersionHistoryModal
 * + charts/FormulationLineageGraph). Used by the lineage page and as a Drawer in the editor / leaderboard. */
import { App, Button, Empty, Input, Segmented, Select, Space, Table, Tag, Typography, theme } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { EChartsOption } from "echarts";
import { useUi } from "@/app/store";
import { Chart } from "@/shared/Chart";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { fx } from "./api";
import { previousVersion, versionTree } from "./logic";
import type { Formulation, FormulationVersion, IngredientChange, Lineage, VersionDiff } from "./types";

export function useMetricLabel() {
  const { t } = useTranslation();
  const labels: Record<string, string> = {
    salt_spray_hours: t("formulation.metric.salt_spray_hours", "耐盐雾"),
    cost_cny_per_kg: t("formulation.metric.cost_cny_per_kg", "成本"),
    voc_gpl: "VOC",
    cleaning_efficiency: t("formulation.metric.cleaning_efficiency", "清洗率"),
  };
  return (m: string) => labels[m] ?? m;
}

/** What changed between two revisions. */
export function VersionDiffView({ diff }: { diff: VersionDiff }) {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const metric = useMetricLabel();
  const changeLabel: Record<string, [string, string]> = {
    added: [t("formulation.diff.added", "新增"), "success"],
    removed: [t("formulation.diff.removed", "移除"), "error"],
    adjusted: [t("formulation.diff.adjusted", "调整"), "processing"],
  };
  const deltas = Object.entries(diff.metric_deltas ?? {}).filter(([, d]) => d && d.pct != null);
  const signed = (v: number, digits?: number) => <span style={{ color: v > 0 ? token.colorSuccess : token.colorError }}>{v > 0 ? "+" : ""}{digits != null ? v.toFixed(digits) : v}</span>;
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Space wrap>
        <Typography.Text strong>v{diff.from_version} → v{diff.to_version}</Typography.Text>
        {diff.topology_changed ? <Tag color="warning">{t("formulation.diff.topology", "成分组成变化")}</Tag> : <Tag>{t("formulation.diff.ratio_only", "仅配比调整")}</Tag>}
        {diff.renamed && <Typography.Text type="secondary">{t("formulation.diff.renamed", "重命名：{{a}} → {{b}}", { a: diff.renamed[0], b: diff.renamed[1] })}</Typography.Text>}
      </Space>
      {diff.change_summary && <Typography.Text type="secondary">{diff.change_summary}</Typography.Text>}
      <Table<IngredientChange> size="small" pagination={false} rowKey={(c) => `${c.name}-${c.change}`} dataSource={diff.ingredient_changes}
        locale={{ emptyText: <Empty description={t("formulation.diff.no_ing", "成分无变化")} /> }}
        columns={[
          { title: t("formulation.diff.change", "变化"), dataIndex: "change", width: 80, render: (c: string) => <Tag color={changeLabel[c]?.[1]}>{changeLabel[c]?.[0] ?? c}</Tag> },
          { title: t("formulation.ing.name", "成分"), dataIndex: "name" },
          { title: t("formulation.diff.pct", "质量分数 %"), align: "right", render: (_d, c) => `${c.before_pct ?? "—"} → ${c.after_pct ?? "—"}` },
          { title: "Δ", dataIndex: "delta_pct", align: "right", width: 90, render: (v: number | null) => (v != null ? signed(v) : "") },
        ]} />
      {deltas.length > 0 && <Space wrap>{deltas.map(([m, d]) => <Tag key={m}>{metric(m)} {signed(d.pct ?? 0, 1)}%</Tag>)}</Space>}
    </Space>
  );
}

/** Lineage graph (ECharts tree, root at top); clicking a node selects that version. */
export function LineageGraph({ versions, onPick }: { versions: FormulationVersion[]; onPick: (v: FormulationVersion) => void }) {
  const { t } = useTranslation();
  const roots = versionTree(versions);
  const option: EChartsOption = {
    tooltip: { trigger: "item", formatter: (p: unknown) => { const d = (p as { data: { name: string; summary: string } }).data; return `${d.name}<br/>${d.summary || "—"}`; } },
    series: roots.map((root, i) => ({
      type: "tree", data: [root], orient: "TB", top: `${(i * 100) / roots.length + 6}%`, bottom: `${100 - ((i + 1) * 100) / roots.length + 6}%`,
      symbolSize: 26, expandAndCollapse: false, initialTreeDepth: -1,
      label: { position: "inside", fontSize: 10 }, leaves: { label: { position: "inside" } },
    })),
  };
  return (
    <>
      <Chart option={option} height={Math.max(260, 120 * roots.length)} onEvents={{
        click: (p: unknown) => { const id = (p as { data?: { value?: string } }).data?.value; const v = versions.find((x) => x.id === id); if (v) onPick(v); },
      }} />
      <Typography.Text type="secondary">{t("formulation.ver.graph_hint", "点击节点查看与上一版的差异 · 自上而下为版本演进方向")}</Typography.Text>
    </>
  );
}

/**
 * Revision history of one formulation. Either ``lineageId`` is known, or it is looked up by the formulation's name + domain.
 * With ``formulation`` the user can save the current state as a new immutable revision.
 */
export function VersionHistory({ lineageId, formulation, onLineage, onOpenVersion }: {
  lineageId?: string | null; formulation?: Formulation; onLineage?: (l: { lineage_id: string; parent: string }) => void; onOpenVersion?: (versionId: string) => void;
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const projectId = useUi((s) => s.projectId);
  const [view, setView] = useState<"list" | "graph">("list");
  const [note, setNote] = useState("");
  const [pair, setPair] = useState<[string, string] | null>(null);
  const key = ["formulation", "lineage", lineageId ?? `${formulation?.name}|${formulation?.domain}`];
  const lineage = useQuery({
    queryKey: key,
    enabled: !!lineageId || !!formulation?.name,
    queryFn: async (): Promise<Lineage | null> => {
      if (lineageId) return fx.lineage(lineageId);
      const found = await fx.findLineages(formulation!.name, formulation!.domain, 1);
      if (!found.length) return null;
      try { return await fx.lineage(found[0].lineage_id); } catch { return found[0]; }
    },
  });
  const versions = lineage.data?.versions ?? [];
  const diff = useQuery({ queryKey: ["formulation", "vdiff", pair], enabled: !!pair, queryFn: () => fx.diffVersions(pair![0], pair![1]) });
  const save = useMutation({
    mutationFn: () => fx.saveVersion({
      formulation: formulation!, lineage_id: lineage.data?.lineage_id ?? null,
      parent_version_id: versions.length ? versions[versions.length - 1].id : null, change_summary: note.trim(), project_id: projectId,
    }),
    onSuccess: (out) => {
      setNote("");
      message.success(t("formulation.ver.saved", "已保存为第 {{v}} 版", { v: out.version }));
      onLineage?.({ lineage_id: out.lineage_id, parent: out.id });
      void qc.invalidateQueries({ queryKey: ["formulation", "lineage"] });
    },
    onError: (e) => message.error(errorText(e)),
  });
  const compareWithPrev = (v: FormulationVersion) => {
    const prev = previousVersion(versions, v);
    if (prev) setPair([prev.id, v.id]);
  };
  const opts = versions.map((v) => ({ value: v.id, label: `v${v.version} ${v.change_summary?.slice(0, 20) ?? ""}` }));

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <Typography.Text type="secondary">{t("formulation.ver.intro", "每次保存都是一条不可变的修订记录；选两版即可看到具体改了哪些成分、指标怎么变。")}</Typography.Text>
      {formulation && <Space.Compact style={{ width: "100%" }}>
        <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder={t("formulation.ver.note_ph", "本次修改说明（留空则自动生成）")} />
        <Button type="primary" loading={save.isPending} disabled={!formulation.ingredients.length} onClick={() => save.mutate()}>{t("formulation.ver.save", "保存为新版本")}</Button>
      </Space.Compact>}
      <ErrorAlert error={lineage.error ?? diff.error} />
      {versions.length === 0 ? <Empty description={lineage.isLoading ? undefined : t("formulation.ver.empty", "该配方尚无修订记录——保存一次即可开始记录谱系。")} /> : <>
        <Space wrap>
          <Segmented value={view} onChange={(v) => setView(v as "list" | "graph")} options={[
            { value: "list", label: t("formulation.ver.list", "列表") }, { value: "graph", label: t("formulation.ver.graph", "图谱") }]} />
          <Select style={{ width: 180 }} placeholder={t("formulation.ver.from", "起始版本")} options={opts} value={pair?.[0]}
            onChange={(v: string) => setPair([v, pair?.[1] ?? versions[versions.length - 1].id])} />
          →
          <Select style={{ width: 180 }} placeholder={t("formulation.ver.to", "目标版本")} options={opts} value={pair?.[1]}
            onChange={(v: string) => setPair([pair?.[0] ?? versions[0].id, v])} />
          {lineage.data && !lineageId && <Link to={`/lab/lineage/${lineage.data.lineage_id}`}>{t("formulation.ver.open_page", "在谱系页打开")}</Link>}
        </Space>
        {view === "graph" ? <LineageGraph versions={versions} onPick={compareWithPrev} /> : (
          <Table<FormulationVersion> size="small" rowKey="id" pagination={false} dataSource={versions} columns={[
            { title: t("formulation.ver.version", "版本"), dataIndex: "version", width: 70, render: (v: number) => <b>v{v}</b> },
            { title: t("formulation.ver.summary", "修改说明"), dataIndex: "change_summary", ellipsis: true,
              render: (s: string, v) => <>{s || t("formulation.ver.no_summary", "（无说明）")}{v.parent_version_id === null && v.version > 1 && <Tag color="warning" style={{ marginLeft: 6 }}>{t("formulation.ver.parent_gone", "父版本已删除")}</Tag>}</> },
            { title: t("formulation.ver.author", "操作人"), dataIndex: "created_by", width: 110, render: (s: string) => s || t("formulation.ver.anon", "未署名") },
            { title: t("common.created", "创建时间"), dataIndex: "created_at", width: 170, render: (s: string | null) => (s ? new Date(s).toLocaleString() : "—") },
            { title: t("common.actions", "操作"), width: 170, render: (_d, v) => <Space size={0}>
              {previousVersion(versions, v) && <Button size="small" type="link" onClick={() => compareWithPrev(v)}>{t("formulation.ver.compare_prev", "与上一版对比")}</Button>}
              {onOpenVersion && <Button size="small" type="link" onClick={() => onOpenVersion(v.id)}>{t("formulation.ver.open", "打开")}</Button>}
            </Space> },
          ]} />
        )}
        {diff.data && <VersionDiffView diff={diff.data} />}
      </>}
    </Space>
  );
}

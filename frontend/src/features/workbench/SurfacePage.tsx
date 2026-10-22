import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Empty, Select, Space, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { call } from "@/api/client";
import { ErrorAlert } from "@/shared/feedback";
import { ContourChart } from "./charts";
import { useCampaigns, useProjectWorkspace } from "./hooks";
import { effectiveObjectives, factorKeys } from "./ledger";
import { surfacePoints } from "./surface";
import type { CampaignDetail } from "./types";

/** 响应曲面 (legacy ``charts/ContourPlot`` from the workbench): two factors × one measured metric of a campaign. */
export default function SurfacePage() {
  const { t } = useTranslation();
  const { ws, project } = useProjectWorkspace();
  const campaigns = useCampaigns();
  const [picked, setPicked] = useState<number | null>(null);
  const cid = picked ?? ws.workbench_campaign_id ?? campaigns.items[0]?.id ?? null;
  const [sel, setSel] = useState<{ x?: string; y?: string; m?: string }>({});
  const detail = useQuery({ queryKey: ["workbench", cid], enabled: cid != null, queryFn: () => call<CampaignDetail>("GET", `/api/experiments/workbench/${cid}`) });
  const rows = useMemo(() => detail.data?.rows ?? [], [detail.data]);
  const factors = useMemo(() => factorKeys(null, rows), [rows]);
  const objectives = effectiveObjectives(project.data?.workspace.requirement?.objectives, detail.data?.objectives_snapshot);
  const x = sel.x ?? factors[0];
  const y = sel.y ?? factors[1];
  const m = sel.m ?? objectives[0]?.metric;
  const points = useMemo(() => (x && y && m ? surfacePoints(rows, x, y, m) : []), [rows, x, y, m]);
  const predictedCount = useMemo(() => points.filter((p) => p.predicted).length, [points]);
  return (
    <PageContainer subTitle={t("workbench.surface.sub", "按已完成实验的实际参数与实测值插值（IDW），观察两因子对指标的响应；缺实测值的点以预测值参与插值，图中以空心菱形标注")}>
      <ProCard bordered>
        <Space wrap>
          <span>{t("workbench.campaign", "实验台账")}</span>
          <Select style={{ width: 280 }} value={cid ?? undefined} onChange={(v) => { setPicked(v); setSel({}); }}
            options={campaigns.items.map((c) => ({ value: c.id, label: `#${c.id} · ${c.name}` }))} />
          <span>X</span><Select style={{ width: 160 }} value={x} onChange={(v) => setSel((s) => ({ ...s, x: v }))} options={factors.map((f) => ({ value: f }))} />
          <span>Y</span><Select style={{ width: 160 }} value={y} onChange={(v) => setSel((s) => ({ ...s, y: v }))} options={factors.map((f) => ({ value: f }))} />
          <span>{t("workbench.metric", "指标")}</span>
          <Select style={{ width: 200 }} value={m} onChange={(v) => setSel((s) => ({ ...s, m: v }))}
            options={objectives.map((o) => ({ value: o.metric, label: o.display_name || o.metric }))} />
          {cid != null && <Link to={`/cockpit/workbench/${cid}`}>{t("workbench.open_ledger", "打开台账")}</Link>}
        </Space>
      </ProCard>
      <ErrorAlert error={detail.error ?? campaigns.error} />
      <ProCard bordered style={{ marginTop: 16 }} loading={detail.isLoading && cid != null}>
        {cid == null ? <Empty description={t("workbench.surface.no_campaign", "还没有实验台账")} />
          : factors.length < 2 ? <Empty description={t("workbench.surface.two_factors", "该台账少于 2 个因子，无法绘制响应曲面")} />
            : <>
              <ContourChart points={points} xLabel={x ?? ""} yLabel={y ?? ""} metric={m ?? ""} height={480} />
              <Typography.Text type="secondary">{t("workbench.surface.n", "{{n}} 个已完成实验点参与插值（实测 {{m}} 个，预测值 {{p}} 个）", { n: points.length, m: points.length - predictedCount, p: predictedCount })}</Typography.Text>
            </>}
      </ProCard>
    </PageContainer>
  );
}

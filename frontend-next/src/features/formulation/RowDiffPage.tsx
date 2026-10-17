import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Empty, Select, Space, Switch, Table, Typography, theme } from "antd";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ErrorAlert } from "@/shared/feedback";
import { fx } from "./api";
import { buildRowDiff, fmtValue, type DiffLine } from "./logic";

/** Side-by-side formulation diff of two workbench rows (legacy ExperimentDiff). */
export default function RowDiffPage() {
  const { campaignId = "" } = useParams();
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const a = Number(params.get("a")) || undefined;
  const b = Number(params.get("b")) || undefined;
  const onlyChanged = params.get("changed") === "1";
  const campaign = useQuery({ queryKey: ["formulation", "campaign", campaignId], queryFn: () => fx.campaign(campaignId) });
  const rows = campaign.data?.rows ?? [];
  const ra = rows.find((r) => r.id === a);
  const rb = rows.find((r) => r.id === b);
  const diff = ra && rb ? buildRowDiff(ra, rb) : null;
  const set = (k: string, v: string | null) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v); else next.delete(k);
    setParams(next);
  };
  const options = rows.map((r) => ({ value: r.id, label: `#${r.id} ${r.item_id || ""}` }));
  return (
    <PageContainer title={t("formulation.diff.title", "配方对比")} subTitle={campaign.data?.name} loading={campaign.isLoading}>
      <ErrorAlert error={campaign.error} />
      <Space wrap style={{ marginBottom: 16 }}>
        <Select style={{ width: 200 }} value={a} options={options} placeholder="A" onChange={(v: number) => set("a", String(v))} />
        <Typography.Text>vs</Typography.Text>
        <Select style={{ width: 200 }} value={b} options={options} placeholder="B" onChange={(v: number) => set("b", String(v))} />
        <Switch checked={onlyChanged} onChange={(v) => set("changed", v ? "1" : null)} /> {t("formulation.diff.only_changed", "仅看变化")}
      </Space>
      {!diff ? <Empty description={t("formulation.diff.pick", "选择两行进行对比")} /> : (
        <ProCard direction="column" gutter={[0, 16]} ghost>
          <ProCard bordered title={t("formulation.diff.factors", "配方参数")}>
            <DiffTable lines={onlyChanged ? diff.factors.filter((d) => d.changed) : diff.factors} a={a!} b={b!} campaignId={campaignId} />
          </ProCard>
          <ProCard bordered title={t("formulation.diff.measured", "实测指标")}>
            <DiffTable lines={onlyChanged ? diff.measurements.filter((d) => d.changed) : diff.measurements} a={a!} b={b!} campaignId={campaignId} />
          </ProCard>
        </ProCard>
      )}
    </PageContainer>
  );
}

function DiffTable({ lines, a, b, campaignId }: { lines: DiffLine[]; a: number; b: number; campaignId: string }) {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const cell = (v: unknown, d: DiffLine) => <Typography.Text code strong={d.changed} type={d.changed ? "warning" : undefined}>{fmtValue(v)}</Typography.Text>;
  return (
    <Table<DiffLine> size="small" rowKey="key" pagination={false} dataSource={lines} locale={{ emptyText: <Empty description={t("formulation.diff.none", "无差异")} /> }}
      onRow={(d) => ({ style: d.changed ? { background: token.colorWarningBg } : undefined })}
      columns={[
        { title: t("formulation.row.factor", "因子"), dataIndex: "key" },
        { title: <Link to={`/lab/experiments/workbench/${campaignId}/rows/${a}`}>#{a}</Link>, dataIndex: "a", align: "right", render: cell },
        { title: <Link to={`/lab/experiments/workbench/${campaignId}/rows/${b}`}>#{b}</Link>, dataIndex: "b", align: "right", render: cell },
      ]} />
  );
}

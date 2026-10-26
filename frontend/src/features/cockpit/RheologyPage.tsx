import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Button, Input, Select, Space, Table } from "antd";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useSession } from "@/app/session";
import { Chart } from "@/shared/Chart";
import { ErrorAlert } from "@/shared/feedback";

type Model = "auto" | "power_law" | "herschel_bulkley" | "cross";

interface FitOut {
  model: string;
  params: Record<string, number>;
  r2: number;
  rmse: number;
  comparison?: Record<string, { r2: number; rmse: number }>;
  thixotropy_index?: number;
  thixotropy_note?: string;
  shear_thinning: { assessment: string; n: number; viscosity_ratio: number | null; note: string };
}

/** 补齐 2-20: 流变拟合 — 粘贴剪切速率/黏度或应力表，选模型拟合。 */
export default function RheologyPage() {
  const { t } = useTranslation();
  const { can } = useSession();
  const [csv, setCsv] = useState("1\t10\n2\t6.3\n5\t3.6\n10\t2.4\n20\t1.6\n50\t1.0\n100\t0.7");
  const [mode, setMode] = useState<"viscosity" | "stress">("viscosity");
  const [model, setModel] = useState<Model>("auto");

  const parse = (): { shear_rate: number; viscosity?: number; shear_stress?: number }[] => csv.split(/\n/)
    .map((l) => l.split(/[,;\t]/).map((x) => Number(x.trim())))
    .filter((r) => r.length >= 2 && r.every((v) => Number.isFinite(v)))
    .map((r) => (mode === "viscosity" ? { shear_rate: r[0], viscosity: r[1] } : { shear_rate: r[0], shear_stress: r[1] }));

  const fit = useMutation({
    mutationFn: () => call<FitOut>("POST", "/api/cockpit/rheology/fit", { points: parse(), model }),
  });
  const out = fit.data;
  const pts = parse();

  // 拟合曲线：按实测剪切速率范围取对数等距点，用拟合参数算黏度。
  const fittedCurve = (): [number, number][] => {
    if (!out) return [];
    const gammas = pts.map((p) => p.shear_rate);
    const lo = Math.min(...gammas);
    const hi = Math.max(...gammas);
    const xs: number[] = [];
    for (let i = 0; i <= 50; i++) xs.push(lo * Math.pow(hi / lo, i / 50));
    const { params } = out;
    return xs.map((g) => {
      let eta: number;
      if (out.model === "cross") {
        eta = params.eta_inf + (params.eta0 - params.eta_inf) / (1 + Math.pow(params.lambda * g, params.m));
      } else if (out.model === "herschel_bulkley") {
        eta = (params.tau0 + params.K * Math.pow(g, params.n)) / g;
      } else {
        eta = (params.K * Math.pow(g, params.n)) / g;
      }
      return [g, eta] as [number, number];
    });
  };

  const measuredEta = pts.map((p) => ({ shear_rate: p.shear_rate, eta: p.viscosity ?? (p.shear_stress! / p.shear_rate) }));

  return (
    <PageContainer subTitle={t("rheo.sub", "Power-law / Herschel–Bulkley / Cross 标准模型拟合；触变指数为两点筛查定义")}>
      <ProCard bordered>
        <Space direction="vertical" style={{ width: "100%" }}>
          <Space wrap>
            <Select style={{ width: 180 }} value={mode} onChange={setMode}
              options={[{ value: "viscosity", label: t("rheo.mode_visc", "剪切速率, 黏度") }, { value: "stress", label: t("rheo.mode_stress", "剪切速率, 剪切应力") }]} />
            <Select style={{ width: 200 }} value={model} onChange={setModel}
              options={[{ value: "auto", label: t("rheo.auto", "auto（取 R² 最高）") }, { value: "power_law", label: "Power-law" }, { value: "herschel_bulkley", label: "Herschel–Bulkley" }, { value: "cross", label: "Cross" }]} />
            <Button type="primary" disabled={pts.length < 4 || !can("formulation.read")} loading={fit.isPending} onClick={() => fit.mutate()}>{t("rheo.fit", "拟合")}</Button>
            <span>{t("rheo.points", "已解析 {{n}} 点（至少 4 点）", { n: pts.length })}</span>
          </Space>
          <Input.TextArea rows={7} value={csv} onChange={(e) => setCsv(e.target.value)}
            placeholder={t("rheo.csv", "每行：剪切速率, 黏度（或剪切应力），逗号/分号/制表符分隔")} />
        </Space>
      </ProCard>
      <ErrorAlert error={fit.error} />
      {out && (
        <>
          <ProCard bordered style={{ marginTop: 16 }} title={t("rheo.result", "拟合结果：{{m}}，R² {{r2}}，RMSE {{rmse}}", { m: out.model, r2: out.r2.toFixed(4), rmse: out.rmse.toFixed(4) })}>
            <Space direction="vertical" style={{ width: "100%" }}>
              <Table size="small" rowKey="k" pagination={false}
                dataSource={Object.entries(out.params).map(([k, v]) => ({ k, v }))}
                columns={[{ title: t("rheo.param", "参数"), dataIndex: "k" }, { title: t("rheo.value", "值"), dataIndex: "v", render: (v: number) => v.toFixed(4) }]} />
              <span>{t("rheo.thinning", "剪切变稀评估：{{a}}（n={{n}}）{{note}}", { a: t(`rheo.${out.shear_thinning.assessment}`, out.shear_thinning.assessment), n: out.shear_thinning.n.toFixed(3), note: out.shear_thinning.note ? ` — ${out.shear_thinning.note}` : "" })}</span>
              {out.thixotropy_index != null && <span>{t("rheo.ti", "触变指数 η(低剪切)/η(高剪切) = {{ti}}", { ti: out.thixotropy_index.toFixed(3) })} — {out.thixotropy_note}</span>}
              {out.comparison && (
                <Table size="small" rowKey="m" pagination={false}
                  dataSource={Object.entries(out.comparison).map(([m, c]) => ({ m, ...c }))}
                  columns={[{ title: t("rheo.model", "模型"), dataIndex: "m" }, { title: "R²", dataIndex: "r2", render: (v: number) => v.toFixed(4) }, { title: "RMSE", dataIndex: "rmse", render: (v: number) => v.toFixed(4) }]} />
              )}
            </Space>
          </ProCard>
          <ProCard bordered style={{ marginTop: 16 }}>
            <Chart height={380} option={{
              tooltip: { trigger: "item" },
              legend: { data: [t("rheo.measured", "实测"), t("rheo.fitted", "拟合")] },
              xAxis: { type: "log", name: t("rheo.shear_rate", "剪切速率") },
              yAxis: { type: "log", name: t("rheo.viscosity", "黏度") },
              series: [
                { name: t("rheo.measured", "实测"), type: "scatter", symbolSize: 8, data: measuredEta.map((p) => [p.shear_rate, p.eta]) },
                { name: t("rheo.fitted", "拟合"), type: "line", symbol: "none", data: fittedCurve() },
              ],
            }} />
          </ProCard>
        </>
      )}
    </PageContainer>
  );
}

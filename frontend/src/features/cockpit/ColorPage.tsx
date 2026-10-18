import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Button, InputNumber, Select, Space, Table } from "antd";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { Chart } from "@/shared/Chart";
import { ErrorAlert } from "@/shared/feedback";
import { FormulationEditor, type DraftFormulation } from "./FormulationEditor";

interface ColorOut {
  name: string;
  L: number;
  a: number;
  b: number;
  delta_e_00: number | null;
  delta_e_ab: number;
  delta_e: number;
  delta_e_method: string;
  delta_e_00_vs_reference?: number | null;
  delta_e_ab_vs_reference?: number;
}

/** CIE76 ΔE*ab：L*a*b* 欧氏距离（纯函数，供 ΔE 矩阵用）。 */
export function deltaEab(lab1: [number, number, number], lab2: [number, number, number]): number {
  return Math.sqrt(lab1.reduce((s, v, i) => s + (v - lab2[i]) ** 2, 0));
}

/** 补齐 2-21: 色度 — 组分编辑器 + a*–b* 散点 + ΔE 矩阵（双口径）。 */
export default function ColorPage() {
  const { t } = useTranslation();
  const [forms, setForms] = useState<DraftFormulation[]>([
    { name: "配方 1", ingredients: [{ name: "Titanium dioxide", weight_pct: 20, role: "pigment" }] },
  ]);
  const [refLab, setRefLab] = useState<[number, number, number] | null>(null);
  const materials = useQuery({ queryKey: ["cockpit", "color-materials"], queryFn: () => call<{ count: number; materials: { name: string; cas: string | null; role: string; lab: [number, number, number] }[] }>("GET", "/api/cockpit/color/materials") });

  const run = useMutation({
    mutationFn: () => call<{ formulations: ColorOut[] }>("POST", "/api/cockpit/color", {
      formulations: forms, reference_lab: refLab,
    }),
  });
  const out = run.data?.formulations ?? [];

  return (
    <PageContainer subTitle={t("color.sub", "累积配方做 a*–b* 散点；ΔE 矩阵为 CIE76 口径；与参考色的 ΔE 同时给 ΔE00 与 ΔE*ab 两个口径")}>
      <ProCard bordered>
        <Space direction="vertical" style={{ width: "100%" }}>
          <FormulationEditor formulations={forms} onChange={setForms} />
          <Space wrap>
            <span>{t("color.materials", "从原料库添加：")}</span>
            <Select style={{ width: 280 }} placeholder={t("color.material_pick", "选原料（带 CIELAB 值）")}
              options={(materials.data?.materials ?? []).map((m) => ({ value: m.name, label: `${m.name} (L* ${m.lab[0]})` }))}
              onChange={(name) => {
                const m = materials.data?.materials.find((x) => x.name === name);
                if (!m || !forms.length) return;
                const next = [...forms];
                next[next.length - 1] = { ...next[next.length - 1], ingredients: [...next[next.length - 1].ingredients, { name: m.name, weight_pct: 10, role: m.role }] };
                setForms(next);
              }} />
          </Space>
          <Space wrap>
            <span>{t("color.reference", "参考色 Lab（可选）：")}</span>
            <InputNumber placeholder="L*" onChange={(v) => setRefLab((r) => [Number(v ?? 0), r?.[1] ?? 0, r?.[2] ?? 0])} />
            <InputNumber placeholder="a*" onChange={(v) => setRefLab((r) => [r?.[0] ?? 0, Number(v ?? 0), r?.[2] ?? 0])} />
            <InputNumber placeholder="b*" onChange={(v) => setRefLab((r) => [r?.[0] ?? 0, r?.[1] ?? 0, Number(v ?? 0)])} />
            <Button onClick={() => setRefLab(null)}>{t("color.reference_clear", "清除参考色")}</Button>
          </Space>
          <Button type="primary" loading={run.isPending} onClick={() => run.mutate()}>{t("color.run", "计算色度")}</Button>
        </Space>
      </ProCard>
      <ErrorAlert error={run.error} />
      {out.length > 0 && (
        <>
          <ProCard bordered style={{ marginTop: 16 }}>
            <Chart height={380} option={{
              tooltip: { trigger: "item", formatter: (p: unknown) => { const d = (p as { name: string; data: [number, number] }).data; return `${(p as { name: string }).name}<br/>a*: ${d[0]}<br/>b*: ${d[1]}`; } },
              xAxis: { name: "a*", scale: true },
              yAxis: { name: "b*", scale: true },
              series: [{ type: "scatter", symbolSize: 12, data: out.map((f) => ({ name: f.name, value: [f.a, f.b] })) }],
            }} />
          </ProCard>
          <ProCard bordered style={{ marginTop: 16 }}>
            <Table size="small" rowKey="name" pagination={false} dataSource={out} columns={[
              { title: t("color.form", "配方"), dataIndex: "name" },
              { title: "L*", dataIndex: "L", render: (v: number) => v.toFixed(2) },
              { title: "a*", dataIndex: "a", render: (v: number) => v.toFixed(2) },
              { title: "b*", dataIndex: "b", render: (v: number) => v.toFixed(2) },
              { title: "ΔE00（vs 白）", dataIndex: "delta_e_00", render: (v: number | null) => (v == null ? "—（colour-science 未安装）" : v.toFixed(3)) },
              { title: "ΔE*ab（vs 白）", dataIndex: "delta_e_ab", render: (v: number) => v.toFixed(3) },
              { title: t("color.method", "口径"), dataIndex: "delta_e_method" },
              ...(refLab ? [
                { title: "ΔE00（vs 参考）", dataIndex: "delta_e_00_vs_reference", render: (v: number | null) => (v == null ? "—" : v.toFixed(3)) },
                { title: "ΔE*ab（vs 参考）", dataIndex: "delta_e_ab_vs_reference", render: (v: number) => v.toFixed(3) },
              ] : []),
            ]} />
          </ProCard>
          <ProCard bordered style={{ marginTop: 16 }} title={t("color.matrix", "配方间 ΔE*ab 矩阵（CIE76）")}>
            <Table size="small" rowKey="name" pagination={false} dataSource={out.map((f) => ({ name: f.name, lab: [f.L, f.a, f.b] as [number, number, number] }))} columns={[
              { title: t("color.form", "配方"), dataIndex: "name" },
              ...out.map((f) => ({
                title: f.name,
                render: (_v: unknown, r: { lab: [number, number, number] }) => deltaEab(r.lab, [f.L, f.a, f.b]).toFixed(2),
              })),
            ]} />
          </ProCard>
        </>
      )}
    </PageContainer>
  );
}

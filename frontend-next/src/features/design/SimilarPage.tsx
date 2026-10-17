import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Alert, Button, Checkbox, Empty, Form, InputNumber, List, Progress, Space, Tag, Typography } from "antd";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router-dom";
import { call } from "@/api/client";
import { EditableRows } from "@/shared/EditableRows";
import { ErrorAlert } from "@/shared/feedback";
import { FormulationPicker } from "./FormulationPicker";
import { similarityFactors } from "./logic";
import type { SimilarMatch } from "./types";
import { useRequirement } from "./useRequirement";

interface Part { name: string; weight_pct: number }

/** Similar historical formulations (legacy SimilarFormulationModal): ingredient wt% vector vs every recorded experiment. */
export default function SimilarPage() {
  const { t } = useTranslation();
  const { projectId, requirement } = useRequirement();
  const [versionId, setVersionId] = useState<string | undefined>();
  // Prefilled when opened from a leaderboard card (navigate state ``{ formulation }``).
  const preset = (useLocation().state as { formulation?: { ingredients?: { name: string; weight_pct: number }[] } } | null)?.formulation;
  const [parts, setParts] = useState<Part[]>(() => (preset?.ingredients ?? []).map((i) => ({ name: i.name, weight_pct: Number(i.weight_pct) })));
  const [sameDomain, setSameDomain] = useState(true);
  const [excludeProject, setExcludeProject] = useState(false);
  const [minSim, setMinSim] = useState(0.3);
  const [limit, setLimit] = useState(10);
  const factors = similarityFactors({ ingredients: parts.map((p) => ({ name: (p.name ?? "").trim(), role: "", weight_pct: Number(p.weight_pct) })) });

  const search = useMutation({
    mutationFn: () => call<{ matches: SimilarMatch[] }>("POST", "/api/kg/formulations/similar", {
      factors, limit, min_similarity: minSim, domain: sameDomain ? requirement?.domain ?? null : null,
      exclude_project_id: excludeProject ? projectId : null,
    }),
  });
  const matches = search.data?.matches ?? [];

  return (
    <PageContainer subTitle={t("design.sim.sub", "按成分 wt% 相似度检索跨项目的历史实验配方及其实测结果")}>
      <ProCard bordered title={t("design.sim.query", "当前配方")}
        extra={<FormulationPicker domain={requirement?.domain} value={versionId} onPick={(id, f) => {
          setVersionId(id);
          if (f) setParts(Object.entries(similarityFactors(f)).map(([name, weight_pct]) => ({ name, weight_pct })));
        }} />}>
        <EditableRows<Part> value={parts} onChange={setParts} addLabel={t("design.sim.add", "添加成分")} newRow={() => ({ name: "", weight_pct: 0 })}
          columns={[{ title: t("design.ingredient", "成分"), dataIndex: "name" }, { title: "wt%", dataIndex: "weight_pct", valueType: "digit", width: 140 }]} />
        <Form layout="inline" style={{ marginTop: 12, rowGap: 12 }}>
          <Form.Item><Checkbox checked={sameDomain} onChange={(e) => setSameDomain(e.target.checked)}>
            {t("design.sim.same_domain", "仅同产品域")}{requirement ? `（${requirement.domain}）` : ""}</Checkbox></Form.Item>
          <Form.Item><Checkbox checked={excludeProject} disabled={!projectId} onChange={(e) => setExcludeProject(e.target.checked)}>
            {t("design.sim.exclude", "排除当前项目")}</Checkbox></Form.Item>
          <Form.Item label={t("design.sim.min", "最低相似度")}><InputNumber min={0} max={1} step={0.05} value={minSim} onChange={(v) => setMinSim(v ?? 0.3)} /></Form.Item>
          <Form.Item label={t("design.sim.limit", "数量")}><InputNumber min={1} max={50} value={limit} onChange={(v) => setLimit(v ?? 10)} /></Form.Item>
          <Button type="primary" loading={search.isPending} disabled={Object.keys(factors).length === 0} onClick={() => search.mutate()}>
            {t("design.sim.search", "检索相似配方")}</Button>
        </Form>
        {Object.keys(factors).length === 0 && <Alert style={{ marginTop: 12 }} type="info" showIcon
          message={t("design.sim.empty_query", "填写成分 wt%，或从已保存的配方版本载入。")} />}
      </ProCard>
      <ProCard bordered style={{ marginTop: 16 }} title={t("design.sim.results", "相似历史配方")}>
        <ErrorAlert error={search.error} />
        {search.isSuccess && matches.length === 0 ? <Empty description={t("design.sim.none", "未找到相似历史配方")} /> : (
          <List dataSource={matches} rowKey="experiment_id" loading={search.isPending} renderItem={(m) => (
            <List.Item>
              <List.Item.Meta
                avatar={<Progress type="circle" size={52} percent={Number((m.similarity * 100).toFixed(1))} />}
                title={<Space wrap><span>{m.project_title || m.project_id}</span>
                  <Typography.Text type="secondary">{t("design.sim.exp", "实验 #{{id}}", { id: m.experiment_id })}</Typography.Text></Space>}
                description={<Space direction="vertical" size={4}>
                  <Space wrap size={4}>{Object.entries(m.factors).map(([k, v]) => (
                    <Tag key={k} color={m.shared_ingredients.includes(k) ? "green" : "orange"}>{k}: {v}%</Tag>))}</Space>
                  {Object.keys(m.measured).length > 0 && <Typography.Text type="secondary">
                    {t("design.sim.measured", "实测")}: {Object.entries(m.measured).map(([k, v]) => `${k}: ${v}`).join(" · ")}</Typography.Text>}
                  {m.shared_ingredients.length > 0 && <Typography.Text type="success">
                    {t("design.sim.shared", "共同成分")}: {m.shared_ingredients.join(", ")}</Typography.Text>}
                </Space>} />
            </List.Item>
          )} />
        )}
      </ProCard>
    </PageContainer>
  );
}

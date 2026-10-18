import { PageContainer, ProCard } from "@ant-design/pro-components";
import { App, Button, Checkbox, Form, Input, InputNumber, Select, Slider, Space, Tabs, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useUi } from "@/app/store";
import { EditableRows } from "@/shared/EditableRows";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { GhsPictogram } from "@/shared/ghs";
import { AgentContextCard } from "@/features/requirements/AgentContextCard";
import { ConstraintsEditor } from "@/features/requirements/ConstraintsEditor";
import { EffectTrace } from "@/features/requirements/EffectTrace";
import { ProjectHistory } from "@/features/requirements/ProjectHistory";
import { parseActiveConstraints, type ConstraintKey } from "@/features/requirements/constraints";
import { previewEffectTrace } from "@/features/requirements/effectTrace";
import { DOMAINS, SUBSTRATES, type Acceptance, type Objective, type ProjectDetail, type RedLineSubstance, type ReleaseSpec,
  type Requirement, type SpecContract } from "./types";

const PICTOS = ["GHS01", "GHS02", "GHS03", "GHS04", "GHS05", "GHS06", "GHS07", "GHS08", "GHS09"];
const METHODS = ["airless", "air_spray", "electrostatic", "dip", "roller", "brush", "electrophoretic", "autodeposition"];

/** 研发需求工作台 (方案 §5.2): scenario, objective contract, process window, red lines, release specs. */
export default function SpecStudioPage() {
  const { t, i18n } = useTranslation();
  const { message } = App.useApp();
  const { projectId = "" } = useParams();
  const setProject = useUi((s) => s.setProject);
  const qc = useQueryClient();
  const en = i18n.language === "en-US";
  const q = useQuery({ queryKey: ["project", projectId], queryFn: () => call<ProjectDetail>("GET", `/api/projects/${projectId}`) });
  // 补齐计划 2-11: 基材选项取后端 /api/meta/substrates（内置 + 配置扩展），失败时回退 SUBSTRATES 硬编码。
  const substratesQ = useQuery({
    queryKey: ["meta", "substrates"],
    queryFn: () => call<{ value: string; label_zh: string; label_en: string }[]>("GET", "/api/meta/substrates"),
    staleTime: 60_000,
  });
  const substrateOptions = (substratesQ.data?.length ? substratesQ.data : SUBSTRATES.map((d) => ({ value: d[0], label_zh: d[1], label_en: d[2] })))
    .map((d) => ({ value: d.value, label: d[en ? "label_en" : "label_zh"] as string }));
  const [req, setReq] = useState<Requirement | null>(null);
  const [spec, setSpec] = useState<SpecContract>({});
  const [activeKeys, setActiveKeys] = useState<ConstraintKey[]>([]);
  useEffect(() => { setProject(projectId); }, [projectId, setProject]);
  useEffect(() => {
    if (q.data) {
      setReq(q.data.workspace.requirement ?? { domain: q.data.domain || "anticorrosion_coating", objectives: [] });
      setSpec(q.data.workspace.spec_contract ?? {});
      setActiveKeys(parseActiveConstraints(q.data.workspace.active_constraints));
    }
  }, [q.data]);
  const save = useMutation({
    mutationFn: () => call("PUT", `/api/projects/${projectId}`, { workspace: { requirement: { ...req, project_id: projectId }, spec_contract: spec, active_constraints: activeKeys } }),
    onSuccess: () => {
      message.success(t("spec.saved", "规格契约已保存（生成新版本）"));
      void qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (e) => message.error(errorText(e)),
  });
  if (q.error) return <PageContainer><ErrorAlert error={q.error} /></PageContainer>;
  if (!req) return <PageContainer loading />;
  const setR = (patch: Partial<Requirement>) => setReq({ ...req, ...patch });
  const setS = (patch: Partial<SpecContract>) => setSpec({ ...spec, ...patch });
  const pw = spec.process_window ?? {};

  return (
    <PageContainer title={q.data?.title} subTitle={t("spec.sub", "规格契约是 DOE、寻优、合规拦截与发布门禁的共同依据")}
      extra={<Space>
        <Link to={`/spec/projects/${projectId}/sources`}>{t("requirements.sources.link", "项目资料")}</Link>
        <Button type="primary" loading={save.isPending} onClick={() => save.mutate()}>{t("common.save", "保存")}</Button>
      </Space>}>
      <ProCard split="vertical" bordered>
        <ProCard>
          <Tabs items={[
            { key: "scenario", label: t("spec.tab.scenario", "应用场景"), children: (
              <Form layout="vertical" style={{ maxWidth: 640 }}>
                <Form.Item label={t("spec.domain", "产品领域")}>
                  <Select value={req.domain} onChange={(v) => setR({ domain: v })} options={DOMAINS.map((d) => ({ value: d[0], label: d[en ? 2 : 1] }))} />
                </Form.Item>
                <Form.Item label={t("spec.product_type", "产品类型")}><Input value={req.product_type} onChange={(e) => setR({ product_type: e.target.value })} /></Form.Item>
                <Form.Item label={t("spec.application", "应用场景")}><Input.TextArea rows={3} value={req.application} onChange={(e) => setR({ application: e.target.value })} /></Form.Item>
                <Form.Item label={t("spec.substrate", "基材")}>
                  <Select value={req.substrate} onChange={(v) => setR({ substrate: v })} options={substrateOptions} />
                </Form.Item>
                <Form.Item label={t("spec.notes", "备注")}><Input.TextArea rows={3} value={req.notes} onChange={(e) => setR({ notes: e.target.value })} /></Form.Item>
              </Form>
            ) },
            { key: "objectives", label: t("spec.tab.objectives", "目标契约"), children: (
              <>
                <Typography.Paragraph type="secondary">{t("spec.objectives.help", "寻优目标：方向、参考范围或目标值、权重。用于 DOE 与 BayBE。")}</Typography.Paragraph>
                <Typography.Paragraph type="secondary">{t("spec.objectives.release_hint", "勾选“出厂指标”的目标，在定型/冻结时自动派生为出厂检验项（COA），无需在“出厂与验收”页重复录入；显式录入的出厂指标优先。")}</Typography.Paragraph>
                <EditableRows<Objective>
                  value={req.objectives as Objective[]}
                  onChange={(rows) => setR({ objectives: rows })}
                  newRow={() => ({ metric: "", direction: "maximize", weight: 1 })}
                  columns={[
                    { title: t("spec.metric", "指标"), dataIndex: "metric", formItemProps: { rules: [{ required: true }] } },
                    { title: t("spec.display_name", "名称"), dataIndex: "display_name" },
                    { title: t("spec.direction", "方向"), dataIndex: "direction", valueType: "select", valueEnum: {
                      maximize: t("spec.dir.max", "越大越好"), minimize: t("spec.dir.min", "越小越好"), match_target: t("spec.dir.target", "接近目标值") } },
                    { title: t("spec.ref_min", "下限"), dataIndex: "ref_min", valueType: "digit" },
                    { title: t("spec.ref_max", "上限"), dataIndex: "ref_max", valueType: "digit" },
                    { title: t("spec.target", "目标值"), dataIndex: "target_value", valueType: "digit" },
                    { title: t("spec.unit", "单位"), dataIndex: "unit", width: 90 },
                    { title: t("spec.weight", "权重"), dataIndex: "weight", valueType: "digit", width: 90 },
                    // 补齐计划 2-8: 出厂指标标志
                    { title: t("spec.release_indicator", "出厂指标"), dataIndex: "is_release_indicator", valueType: "switch", width: 90 },
                  ]}
                />
              </>
            ) },
            { key: "process", label: t("spec.tab.process", "工艺窗口"), children: (
              <Form layout="vertical" style={{ maxWidth: 640 }}>
                {([["cure_temp_c", t("spec.cure_temp", "固化温度（℃）"), 20, 250], ["cure_time_min", t("spec.cure_time", "固化时间（min）"), 1, 240],
                  ["film_thickness_um", t("spec.film", "干膜厚度（μm）"), 1, 500]] as const).map(([k, label, lo, hi]) => (
                  <Form.Item key={k} label={label}>
                    <Slider range min={lo} max={hi} value={pw[k] ?? [lo, hi]} onChange={(v) => setS({ process_window: { ...pw, [k]: v as [number, number] } })} />
                  </Form.Item>
                ))}
                <Form.Item label={t("spec.application_method", "施工方式")}>
                  <Checkbox.Group value={pw.application_method} onChange={(v) => setS({ process_window: { ...pw, application_method: v as string[] } })}
                    options={METHODS.map((m) => ({ value: m, label: t(`spec.method.${m}`, { airless: "高压无气喷涂", air_spray: "空气喷涂", electrostatic: "静电喷涂", dip: "浸涂", roller: "辊涂", brush: "刷涂", electrophoretic: "电泳", autodeposition: "自沉积" }[m]!) }))} />
                </Form.Item>
              </Form>
            ) },
            { key: "constraints", label: t("requirements.constraints.tab", "工艺约束"), children: (
              <Space direction="vertical" style={{ width: "100%" }} size={16}>
                <ConstraintsEditor requirement={req} activeKeys={activeKeys} onChange={(r, keys) => { setReq(r); setActiveKeys(keys); }} />
                <EffectTrace items={previewEffectTrace(req)} />
              </Space>
            ) },
            { key: "redlines", label: t("spec.tab.redlines", "合规红线"), children: (
              <>
                <Typography.Paragraph type="secondary">{t("spec.redlines.help", "红线在三处生效：配方编辑实时拦截、DOE 试验点预筛、发布门禁复核。")}</Typography.Paragraph>
                <Typography.Title level={5}>{t("spec.redlines.substances", "禁用 / 限用物质")}</Typography.Title>
                <EditableRows<RedLineSubstance>
                  value={spec.red_lines} onChange={(rows) => setS({ red_lines: rows })}
                  newRow={() => ({ cas: "", max_pct: 0 })}
                  columns={[
                    { title: "CAS", dataIndex: "cas", formItemProps: { rules: [{ required: true, pattern: /^\d{2,7}-\d{2}-\d$/ }] } },
                    { title: t("spec.name", "名称"), dataIndex: "name" },
                    { title: t("spec.max_pct", "上限（%，0 = 禁用）"), dataIndex: "max_pct", valueType: "digit" },
                    { title: t("spec.reason", "依据"), dataIndex: "reason" },
                  ]}
                />
                <Form layout="vertical" style={{ marginTop: 16, maxWidth: 640 }}>
                  <Form.Item label={t("spec.voc_limit", "VOC 上限（g/L）")} extra={t("spec.voc_help", "参照 GB 30981 / GB 33372 等限值标准")}>
                    <InputNumber min={0} value={spec.voc_limit_g_l ?? undefined} onChange={(v) => setS({ voc_limit_g_l: v })} />
                  </Form.Item>
                  <Form.Item label={t("spec.forbidden_pictos", "不得出现的象形图")}>
                    <Checkbox.Group value={spec.forbidden_pictograms} onChange={(v) => setS({ forbidden_pictograms: v as string[] })}
                      options={PICTOS.map((p) => ({ value: p, label: <Space size={4}><GhsPictogram code={p} size={28} />{p}</Space> }))} />
                  </Form.Item>
                  <Form.Item label={t("spec.forbidden_signal", "信号词限制")}>
                    <Select allowClear style={{ width: 240 }} value={spec.forbidden_signal_word ?? undefined} onChange={(v) => setS({ forbidden_signal_word: v ?? null })}
                      options={[{ value: "危险", label: t("spec.no_danger", "不得为“危险”") }, { value: "警告", label: t("spec.no_signal", "不得有任何信号词") }]} />
                  </Form.Item>
                </Form>
              </>
            ) },
            { key: "release", label: t("spec.tab.release", "出厂与验收"), children: (
              <>
                <Typography.Title level={5}>{t("spec.release_specs", "出厂指标（COA 判定）")}</Typography.Title>
                {/* 补齐计划 2-8: 派生预览 —— 标出厂指标且未显式录入的目标，定型时自动派生 */}
                {(req.objectives ?? []).filter((o) => o.is_release_indicator && o.metric && !(spec.release_specs ?? []).some((s) => s.metric === o.metric)).length > 0 && (
                  <Typography.Paragraph type="secondary">
                    {t("spec.release_derived_hint", "定型时自动派生：{{metrics}}（来自目标契约的出厂指标勾选；显式录入的优先）", {
                      metrics: (req.objectives ?? []).filter((o) => o.is_release_indicator && o.metric && !(spec.release_specs ?? []).some((s) => s.metric === o.metric))
                        .map((o) => o.display_name || o.metric).join("、"),
                    })}
                  </Typography.Paragraph>
                )}
                <EditableRows<ReleaseSpec>
                  value={spec.release_specs} onChange={(rows) => setS({ release_specs: rows })}
                  newRow={() => ({ metric: "", comparison: "full" })}
                  columns={[
                    { title: t("spec.metric", "指标"), dataIndex: "metric", formItemProps: { rules: [{ required: true }] } },
                    { title: t("spec.label", "检验项目"), dataIndex: "label" },
                    { title: t("spec.unit", "单位"), dataIndex: "unit", width: 80 },
                    { title: t("spec.method", "检验方法"), dataIndex: "method" },
                    // 补齐计划 2-8: 检验方法标准号（COA 优先显示，空则回退 method）
                    { title: t("spec.test_method_standard", "检验方法标准号"), dataIndex: "test_method_standard" },
                    { title: t("spec.min", "下限"), dataIndex: "min", valueType: "digit", width: 90 },
                    { title: t("spec.max", "上限"), dataIndex: "max", valueType: "digit", width: 90 },
                    { title: t("spec.comparison", "判定"), dataIndex: "comparison", valueType: "select", width: 120,
                      valueEnum: { full: t("spec.cmp.full", "全数值比较"), rounded: t("spec.cmp.rounded", "修约值比较") } },
                  ]}
                />
                <Typography.Title level={5} style={{ marginTop: 16 }}>{t("spec.acceptance", "定型验收指标（发布门禁 G3）")}</Typography.Title>
                <EditableRows<Acceptance>
                  value={spec.acceptance} onChange={(rows) => setS({ acceptance: rows })}
                  newRow={() => ({ metric: "" })}
                  columns={[
                    { title: t("spec.metric", "指标"), dataIndex: "metric", formItemProps: { rules: [{ required: true }] } },
                    { title: t("spec.label", "名称"), dataIndex: "label" },
                    { title: t("spec.min", "下限"), dataIndex: "min", valueType: "digit" },
                    { title: t("spec.max", "上限"), dataIndex: "max", valueType: "digit" },
                  ]}
                />
              </>
            ) },
            { key: "agent", label: t("requirements.agent.title", "Agent 上下文"), children: (
              <AgentContextCard projectId={projectId} value={typeof q.data?.workspace.agent_context === "string" ? q.data.workspace.agent_context : ""} />
            ) },
          ]} />
        </ProCard>
        <ProCard colSpan={340}>
          <ProjectHistory projectId={projectId} />
        </ProCard>
      </ProCard>
    </PageContainer>
  );
}

import { ModalForm, PageContainer, ProFormDigit, ProFormRadio, ProFormSelect, ProFormText, ProTable } from "@ant-design/pro-components";
import { Alert, App, Button, Tag, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";
import { call } from "@/api/client";
import { ErrorAlert } from "@/shared/feedback";
import { useCampaigns, useEngines, useProjectWorkspace } from "./hooks";
import { adoptPlan } from "./loop";
import { TrainingDataBanner } from "./panels";
import type { CampaignSummary, DoePlan } from "./types";

interface NewCampaign { name?: string; source: "project" | "generate"; design?: string; n?: number }

/** 实验台账列表 (legacy ``WorkbenchModal`` campaign picker + "create from DOE"). */
export default function WorkbenchPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { projectId, ws, project } = useProjectWorkspace();
  const campaigns = useCampaigns();
  const engines = useEngines();
  const plan = ws.doe_plan;
  const pendingAdopt = !!plan && (!plan.plan_id || plan.plan_id !== ws.workbench_adopted_plan_id);

  const create = async (v: NewCampaign) => {
    let p: DoePlan | null | undefined = v.source === "project" ? plan : null;
    if (v.source === "generate") {
      const req = { ...(project.data?.workspace.requirement ?? { domain: project.data?.domain ?? "anticorrosion_coating", objectives: [] }), project_id: projectId };
      const qs = new URLSearchParams({ design: v.design ?? "full_factorial" });
      if (v.n) qs.set("n", String(v.n));
      p = await call<DoePlan>("POST", `/api/doe?${qs}`, req);
    }
    if (!p) throw new Error(t("workbench.no_plan", "当前项目没有 DOE 计划"));
    const { campaign, warning } = await adoptPlan(qc, projectId, p, { name: v.name });
    if (warning) message.warning(t("workbench.plan_status_warn", "DOE 计划状态同步失败（台账已创建）：{{e}}", { e: warning }));
    message.success(t("workbench.created", "已创建台账 #{{id}}", { id: campaign.campaign_id }));
    navigate(`/cockpit/workbench/${campaign.campaign_id}`);
    return true;
  };

  return (
    <PageContainer subTitle={t("workbench.list_sub", "DOE 方案落地为实验台账：回填实际参数与实测值，保存后进入训练与闭环")}>
      <TrainingDataBanner />
      {!projectId && <Alert type="info" showIcon style={{ marginBottom: 12 }} message={t("workbench.pick_project", "未选择项目：列出全部台账；新建台账需先在顶部选择项目")} />}
      {pendingAdopt && projectId && (
        <Alert type="warning" showIcon style={{ marginBottom: 12 }}
          message={t("workbench.pending_adopt", "项目当前 DOE 计划（{{d}}，{{n}} 个实验点）尚未建立台账", { d: plan!.design, n: plan!.runs.length })} />
      )}
      <ErrorAlert error={campaigns.error} />
      <ProTable<CampaignSummary> rowKey="id" search={false} loading={campaigns.isLoading} dataSource={campaigns.items}
        options={{ reload: () => void campaigns.refetch() }}
        headerTitle={t("workbench.total", "共 {{n}} 个台账", { n: campaigns.items.length })}
        toolBarRender={() => [
          <ModalForm<NewCampaign> key="new" title={t("workbench.new", "新建实验台账")} modalProps={{ destroyOnHidden: true }}
            trigger={<Button type="primary" icon={<PlusOutlined />} disabled={!projectId}>{t("workbench.new", "新建实验台账")}</Button>}
            initialValues={{ source: plan ? "project" : "generate", design: "full_factorial" }}
            onFinish={async (v) => {
              try {
                return await create(v);
              } catch (e) {
                message.error(e instanceof Error ? e.message : String(e));
                return false;
              }
            }}>
            <ProFormText name="name" label={t("workbench.name", "名称")} placeholder={t("workbench.name_ph", "留空自动命名")} />
            <ProFormRadio.Group name="source" label={t("workbench.source", "实验方案来源")} options={[
              { value: "project", label: t("workbench.source_project", "项目当前 DOE 计划"), disabled: !plan },
              { value: "generate", label: t("workbench.source_generate", "按项目需求生成新 DOE") },
            ]} />
            <ProFormSelect name="design" label={t("workbench.design", "设计")} dependencies={["source"]}
              options={(engines.designs.length ? engines.designs : ["full_factorial", "fractional_factorial", "ccd", "box_behnken", "lhs"]).map((d) => ({ value: d, label: d }))} />
            <ProFormDigit name="n" label={t("workbench.n_runs", "实验点数（可选）")} min={2} max={200} />
            <Typography.Text type="secondary">{t("workbench.doe_more", "更复杂的设计（正交表 / 混料 / D-最优）请用")} <Link to="/lab/doe">{t("workbench.doe_designer", "DOE 设计器")}</Link></Typography.Text>
          </ModalForm>,
        ]}
        columns={[
          { title: "ID", dataIndex: "id", width: 70, render: (_d, r) => <Link to={`/cockpit/workbench/${r.id}`}>#{r.id}</Link> },
          { title: t("workbench.name", "名称"), dataIndex: "name", render: (_d, r) => <Link to={`/cockpit/workbench/${r.id}`}>{r.name}</Link> },
          { title: t("workbench.strategy", "策略"), dataIndex: "strategy", render: (_d, r) => <Tag>{r.strategy || "—"}</Tag> },
          { title: t("workbench.rows", "行数"), dataIndex: "row_count", width: 80 },
          { title: t("workbench.col.status", "状态"), dataIndex: "status", render: (_d, r) => <Tag>{r.status}</Tag> },
          { title: t("workbench.project", "项目"), dataIndex: "project_id", render: (_d, r) => (r.project_id ? (r.project_id === projectId ? <Tag color="blue">{t("workbench.current_project", "当前项目")}</Tag> : r.project_id.slice(0, 8)) : "—") },
          { title: "", key: "cur", render: (_d, r) => (r.id === ws.workbench_campaign_id ? <Tag color="green">{t("workbench.active", "项目活动台账")}</Tag> : null) },
        ]} />
    </PageContainer>
  );
}

import { PageContainer, ProTable } from "@ant-design/pro-components";
import { Alert, App, Button, Drawer, Popconfirm, Space, Switch, Table, Tag, Typography } from "antd";
import { LockOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useUi } from "@/app/store";
import { ErrorAlert } from "@/shared/feedback";
import { TrainingDataBanner } from "./panels";
import type { ModelInfo, ModelVersion } from "./types";

const when = (iso?: string | null, id?: string) => {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(d.getTime()) ? d.toLocaleString(undefined, { hour12: false }) : (iso || id || "").slice(0, 19).replace("T", " ") || "—";
};
const rmse = (v?: number | null) => (v == null ? "—" : Number(v).toFixed(Number(v) < 1 ? 3 : 1));

/** 模型版本 (legacy ``ModelVersionsPanel``): archived versions of one metric's surrogate; rollback pins, unpin follows newest. */
export function ModelVersionsDrawer({ model, onClose }: { model: ModelInfo | null; onClose: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const pid = model?.project_id ?? "";
  const key = ["models", "versions", pid, model?.metric];
  const versions = useQuery({ queryKey: key, enabled: !!model && !!pid,
    queryFn: () => call<ModelVersion[]>("GET", `/api/models/versions?project_id=${encodeURIComponent(pid)}&metric=${encodeURIComponent(model!.metric)}`) });
  const done = () => { void qc.invalidateQueries({ queryKey: ["models"] }); };
  const rollback = useMutation({ mutationFn: (v: string) => call<ModelInfo>("POST", "/api/models/rollback", { project_id: pid, metric: model!.metric, version_id: v }),
    onSuccess: () => { message.success(t("workbench.models.rolled", "已回滚并锁定该版本")); done(); } });
  const unpin = useMutation({ mutationFn: () => call<ModelInfo>("POST", "/api/models/unpin", { project_id: pid, metric: model!.metric }),
    onSuccess: () => { message.success(t("workbench.models.unpinned", "已解除锁定，使用最新版本")); done(); onClose(); } });
  return (
    <Drawer width={680} open={!!model} onClose={onClose} destroyOnHidden title={model ? t("workbench.models.versions_of", "模型版本 · {{m}}", { m: model.metric }) : ""}>
      {model && !pid && <Alert type="info" showIcon message={t("workbench.models.no_project", "该模型没有项目归属，无法管理版本。")} />}
      {model?.pinned && (
        <Alert type="warning" showIcon icon={<LockOutlined />} style={{ marginBottom: 12 }} data-testid="model-pinned-note"
          message={t("workbench.models.pinned", "已锁定在回滚版本") + (model.newer_version_id ? t("workbench.models.newer", "；重训产生的新版本已存档，未生效") : "")}
          action={<Button size="small" loading={unpin.isPending} onClick={() => unpin.mutate()} data-testid="model-unpin">{t("workbench.models.unpin", "解除锁定并使用最新")}</Button>} />
      )}
      <ErrorAlert error={versions.error ?? rollback.error ?? unpin.error} />
      {pid && (
        <Table<ModelVersion> size="small" rowKey="version_id" loading={versions.isLoading} pagination={false} dataSource={versions.data ?? []}
          locale={{ emptyText: t("workbench.models.no_versions", "尚无存档版本") }}
          columns={[
            { title: t("workbench.models.trained", "训练时间"), render: (_v, v, i) => (
              <Space size={4}>{when(v.trained_at, v.version_id)}
                {v.is_current && <Tag color="blue">{t("workbench.models.current", "当前")}</Tag>}
                {v.is_current && v.pinned && <Tag color="warning">{t("workbench.models.locked", "锁定")}</Tag>}
                {i === 0 && !v.is_current && <Tag color="green">{t("workbench.models.newest", "最新")}</Tag>}
              </Space>
            ) },
            { title: t("workbench.models.backend", "后端"), dataIndex: "backend" },
            { title: "n", dataIndex: "n_samples", width: 60 },
            { title: "R²", dataIndex: "r2", render: (v?: number | null) => (v == null ? "—" : Number(v).toFixed(2)) },
            { title: "RMSE", dataIndex: "rmse", render: rmse },
            { title: "", render: (_v, v) => !v.is_current && (
              <Popconfirm title={t("workbench.models.rollback_q", "回滚后将锁定该版本，之后的重训只存档、不覆盖。确认？")} onConfirm={() => rollback.mutate(v.version_id)}>
                <Button size="small" loading={rollback.isPending && rollback.variables === v.version_id}>{t("workbench.models.rollback", "回滚到此版本")}</Button>
              </Popconfirm>
            ) },
          ]} />
      )}
    </Drawer>
  );
}

/** 代理模型 (legacy model cards + ``ModelVersionsPanel`` + ``TrainingDataBanner``). */
export default function ModelsPage() {
  const { t } = useTranslation();
  const projectId = useUi((s) => s.projectId);
  const [onlyProject, setOnlyProject] = useState(true);
  const [open, setOpen] = useState<ModelInfo | null>(null);
  const models = useQuery({ queryKey: ["models"], queryFn: () => call<ModelInfo[]>("GET", "/api/models") });
  const list = (models.data ?? []).filter((m) => !onlyProject || !projectId || m.project_id === projectId);
  const current = open ? (models.data ?? []).find((m) => m.metric === open.metric && m.project_id === open.project_id && m.domain === open.domain) ?? open : null;
  return (
    <PageContainer subTitle={t("workbench.models.sub", "按项目 × 指标训练的代理模型；回滚会锁定版本，重训不会覆盖")}>
      <TrainingDataBanner />
      <ErrorAlert error={models.error} />
      <ProTable<ModelInfo> rowKey={(m) => `${m.domain}-${m.project_id}-${m.metric}`} search={false} loading={models.isLoading} dataSource={list}
        options={{ reload: () => void models.refetch() }}
        headerTitle={t("workbench.models.total", "共 {{n}} 个模型", { n: list.length })}
        toolBarRender={() => (projectId ? [<Space key="f"><Switch checked={onlyProject} onChange={setOnlyProject} />{t("workbench.models.only_project", "仅当前项目")}</Space>] : [])}
        columns={[
          { title: t("workbench.metric", "指标"), dataIndex: "metric", render: (_d, m) => <Space><code>{m.metric}</code>{m.pinned && <Tag icon={<LockOutlined />} color="warning">{t("workbench.models.locked", "锁定")}</Tag>}</Space> },
          { title: t("workbench.models.domain", "产品域"), dataIndex: "domain" },
          { title: t("workbench.models.backend", "后端"), dataIndex: "backend" },
          { title: "n", dataIndex: "n_samples", width: 60 },
          { title: "R²", dataIndex: "r2", render: (_d, m) => m.r2.toFixed(2) },
          { title: "CV R²", dataIndex: "cv_r2", render: (_d, m) => (m.cv_r2 == null ? "—" : m.cv_r2.toFixed(2)) },
          { title: "RMSE", dataIndex: "rmse", render: (_d, m) => rmse(m.rmse) },
          { title: "q90", dataIndex: "conformal_q90", render: (_d, m) => (m.conformal_q90 == null ? "—" : <Typography.Text type={m.uncertainty_calibrated ? "success" : undefined}>±{rmse(m.conformal_q90)}</Typography.Text>) },
          { title: t("workbench.models.trained", "训练时间"), dataIndex: "trained_at", render: (_d, m) => when(m.trained_at, m.version_id ?? undefined) },
          { title: "", key: "v", render: (_d, m) => <Button size="small" onClick={() => setOpen(m)}>{t("workbench.models.versions", "版本")}</Button> },
        ]} />
      <ModelVersionsDrawer model={current} onClose={() => setOpen(null)} />
    </PageContainer>
  );
}

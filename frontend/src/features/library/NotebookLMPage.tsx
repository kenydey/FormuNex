import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Alert, App, Badge, Button, Form, Input, List, Space, Switch, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useSession } from "@/app/session";
import { useUi } from "@/app/store";
import { ErrorAlert, errorText } from "@/shared/feedback";
import type { NotebookLMLogin, NotebookLMStatus } from "./types";

const STATUS_KEY = ["notebooklm", "status"];

function Checks({ items }: { items: { ok: boolean; label: string }[] }) {
  return (
    <List size="small" dataSource={items} renderItem={(c) => (
      <List.Item key={c.label}><Badge status={c.ok ? "success" : "default"} text={<Typography.Text type={c.ok ? undefined : "secondary"}>{c.label}</Typography.Text>} /></List.Item>
    )} />
  );
}

function LoginResult({ r }: { r: NotebookLMLogin }) {
  const { t } = useTranslation();
  return (
    <Alert type="info" showIcon message={r.started ? t("library.nlm.login_started", "已尝试在后端打开浏览器登录窗口。") : t("library.nlm.login_manual", "需手动完成授权：")}
      description={<Space direction="vertical" size={2}>
        {r.hint && <span>{r.hint}</span>}
        {r.command && <Typography.Text code copyable>{r.command}</Typography.Text>}
        {r.manual_url && <a href={r.manual_url} target="_blank" rel="noreferrer">{t("library.nlm.open", "打开 NotebookLM")}</a>}
      </Space>} />
  );
}

/** Per-project notebook binding (stored on the project workspace). */
function ProjectBinding({ projectId, status }: { projectId: string; status: NotebookLMStatus | undefined }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { can } = useSession();
  const qc = useQueryClient();
  const project = useQuery({ queryKey: ["project", projectId],
    queryFn: () => call<{ title?: string; workspace?: { notebooklm_notebook_id?: string | null } }>("GET", `/api/projects/${encodeURIComponent(projectId)}`) });
  const current = (project.data?.workspace?.notebooklm_notebook_id ?? "").trim();
  const [draft, setDraft] = useState("");
  useEffect(() => setDraft(current), [current]);
  const save = useMutation({
    mutationFn: async () => {
      const id = draft.trim();
      if (!id) throw new Error(t("library.nlm.need_id", "请填写本项目的 Notebook ID"));
      if (status && !status.enabled) await call("POST", "/api/notebooklm/config", { enabled: true });
      await call("PUT", `/api/projects/${encodeURIComponent(projectId)}`, { workspace: { notebooklm_notebook_id: id } });
    },
    onSuccess: () => {
      message.success(t("library.nlm.bound", "已保存到本项目"));
      void qc.invalidateQueries({ queryKey: ["project", projectId] });
      void qc.invalidateQueries({ queryKey: STATUS_KEY });
    },
    onError: (e) => message.error(errorText(e)),
  });
  const legacy = (status?.notebook_id ?? "").trim();
  return (
    <ProCard bordered title={t("library.nlm.project", "本项目 Notebook")} subTitle={project.data?.title}>
      <ErrorAlert error={project.error} />
      <Checks items={[
        { ok: !!(status?.auth_ready ?? status?.available), label: t("library.nlm.auth_ready", "全局授权已就绪") },
        { ok: !!current, label: t("library.nlm.id_set", "本项目 Notebook ID 已填写") },
      ]} />
      <Form layout="vertical" style={{ marginTop: 12 }}>
        <Form.Item label={t("library.nlm.project_id", "本项目 Notebook ID")} extra={t("library.nlm.project_hint", "NotebookLM 链接中的 notebook id；随项目工作区一起保存，联网检索勾选 NotebookLM 时使用")}>
          <Input value={draft} onChange={(e) => setDraft(e.target.value)} style={{ fontFamily: "monospace" }} />
        </Form.Item>
        <Space wrap>
          <Button type="primary" disabled={!can("admin.llm")} loading={save.isPending} onClick={() => save.mutate()}>{t("library.nlm.save_project", "保存到本项目")}</Button>
          {legacy && legacy !== draft.trim() && (
            <Button type="link" onClick={() => setDraft(legacy)}>{t("library.nlm.use_legacy", "使用全局遗留 Notebook ID（{{id}}）", { id: legacy })}</Button>
          )}
        </Space>
      </Form>
    </ProCard>
  );
}

/** NotebookLM: global enable + Google authorisation, and the current project's notebook binding. */
export default function NotebookLMPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { can } = useSession();
  const qc = useQueryClient();
  const projectId = useUi((s) => s.projectId);
  const status = useQuery({ queryKey: STATUS_KEY, queryFn: () => call<NotebookLMStatus>("GET", "/api/notebooklm/auth-status") });
  const s = status.data;
  const [enabled, setEnabled] = useState(false);
  const [legacyId, setLegacyId] = useState("");
  useEffect(() => { if (s) { setEnabled(!!s.enabled); setLegacyId(s.notebook_id ?? ""); } }, [s]);
  const save = useMutation({
    mutationFn: () => call<NotebookLMStatus>("POST", "/api/notebooklm/config", { enabled, notebook_id: legacyId.trim() }),
    onSuccess: (r) => { qc.setQueryData(STATUS_KEY, r); message.success(t("common.saved", "已保存")); },
    onError: (e) => message.error(errorText(e)),
  });
  const login = useMutation({
    mutationFn: () => call<NotebookLMLogin>("POST", "/api/notebooklm/login", {}),
    onSuccess: () => void status.refetch(),
    onError: (e) => message.error(errorText(e)),
  });

  return (
    <PageContainer subTitle={t("library.nlm.sub", "全局只负责启用与 Google 授权；每个研究项目单独绑定 Notebook ID")}
      extra={<Button loading={status.isFetching} onClick={() => void status.refetch()}>{t("library.nlm.refresh", "刷新状态")}</Button>}>
      <ErrorAlert error={status.error} />
      <ProCard gutter={16} wrap>
        <ProCard colSpan={{ xs: 24, lg: 12 }} bordered title={t("library.nlm.global", "全局授权")} loading={status.isLoading}>
          {s && (
            <Space direction="vertical" style={{ width: "100%" }}>
              <Alert type={s.available ? "success" : "warning"} showIcon
                message={s.available ? t("library.nlm.ready", "全局授权已就绪；请在各项目中绑定 Notebook ID") : t("library.nlm.not_ready", "未就绪")}
                description={!s.available ? s.hint : undefined} />
              <Checks items={[
                { ok: !!s.lib_installed, label: t("library.nlm.lib", "notebooklm-py 已安装") },
                { ok: !!s.enabled, label: t("library.nlm.enabled", "功能已启用") },
                { ok: !!s.session_present, label: t("library.nlm.session", "Google 会话已授权") },
              ]} />
              {!s.lib_installed && <Alert type="info" message={t("library.nlm.install", "请在系统设置 → 依赖管理中安装 notebooklm-py")} />}
              <Space><Switch checked={enabled} onChange={setEnabled} />{t("library.nlm.enable", "启用 NotebookLM")}</Space>
              <Form layout="vertical">
                <Form.Item label={t("library.nlm.legacy", "遗留全局 Notebook ID（可选，仅作迁移回退）")}>
                  <Input value={legacyId} onChange={(e) => setLegacyId(e.target.value)} style={{ fontFamily: "monospace" }} />
                </Form.Item>
              </Form>
              <Space wrap>
                <Button type="primary" disabled={!can("admin.llm")} loading={save.isPending} onClick={() => save.mutate()}>{t("library.nlm.save", "保存配置")}</Button>
                <Button loading={login.isPending} disabled={!s.lib_installed} onClick={() => login.mutate()}>{t("library.nlm.login", "授权登录")}</Button>
              </Space>
              {login.data && <LoginResult r={login.data} />}
            </Space>
          )}
        </ProCard>
        <ProCard colSpan={{ xs: 24, lg: 12 }} ghost>
          {projectId ? <ProjectBinding key={projectId} projectId={projectId} status={s} />
            : <Alert type="info" showIcon message={t("library.need_project", "请先在顶部选择一个项目")} />}
        </ProCard>
      </ProCard>
    </PageContainer>
  );
}

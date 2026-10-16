import { PageContainer, ProTable } from "@ant-design/pro-components";
import { App, Button, Form, Input, Modal, Select } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useUi } from "@/app/store";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { DOMAINS, type ProjectDetail } from "./types";

interface ProjectSummary { id: string; title: string; headline: string; domain: string; updated_at: string; has_doe: boolean; has_optimize: boolean }

export default function ProjectsPage() {
  const { t, i18n } = useTranslation();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const setProject = useUi((s) => s.setProject);
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const q = useQuery({ queryKey: ["projects"], queryFn: () => call<ProjectSummary[]>("GET", "/api/projects") });
  const create = useMutation({
    mutationFn: (v: { title: string; domain: string }) =>
      call<ProjectDetail>("POST", "/api/projects", { title: v.title, requirement: { domain: v.domain, objectives: [] } }),
    onSuccess: (p) => {
      void qc.invalidateQueries({ queryKey: ["projects"] });
      setProject(p.id);
      navigate(`/spec/projects/${p.id}`);
    },
    onError: (e) => message.error(errorText(e)),
  });
  const domainLabel = (d: string) => DOMAINS.find((x) => x[0] === d)?.[i18n.language === "en-US" ? 2 : 1] ?? d;
  return (
    <PageContainer>
      <ErrorAlert error={q.error} />
      <ProTable<ProjectSummary>
        rowKey="id" search={false} loading={q.isLoading} dataSource={q.data} options={{ reload: () => void q.refetch() }}
        toolBarRender={() => [<Button key="new" type="primary" onClick={() => setOpen(true)}>{t("spec.new_project", "新建项目")}</Button>]}
        columns={[
          { title: t("spec.project", "项目"), dataIndex: "title", render: (_d, r) => <Link to={`/spec/projects/${r.id}`} onClick={() => setProject(r.id)}>{r.title}</Link> },
          { title: t("spec.domain", "产品领域"), dataIndex: "domain", render: (_d, r) => domainLabel(r.domain), width: 160 },
          { title: t("spec.headline", "摘要"), dataIndex: "headline", ellipsis: true },
          { title: t("common.updated", "更新时间"), dataIndex: "updated_at", valueType: "dateTime", width: 170 },
        ]}
      />
      <Modal open={open} title={t("spec.new_project", "新建项目")} onCancel={() => setOpen(false)} confirmLoading={create.isPending}
        onOk={async () => create.mutate(await form.validateFields())} destroyOnClose>
        <Form form={form} layout="vertical" initialValues={{ domain: "anticorrosion_coating" }}>
          <Form.Item name="title" label={t("spec.project", "项目")} rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="domain" label={t("spec.domain", "产品领域")}>
            <Select options={DOMAINS.map((d) => ({ value: d[0], label: d[i18n.language === "en-US" ? 2 : 1] }))} />
          </Form.Item>
        </Form>
      </Modal>
    </PageContainer>
  );
}

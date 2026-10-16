import { PageContainer, ProTable } from "@ant-design/pro-components";
import { App, Button, Form, Input, Modal } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useUi } from "@/app/store";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { StatusTag } from "@/shared/tags";
import type { Product } from "./types";

export default function ProductsPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const projectId = useUi((s) => s.projectId);
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const products = useQuery({ queryKey: ["plm", "products", q], queryFn: () => call<Product[]>("GET", `/api/plm/products${q ? `?q=${encodeURIComponent(q)}` : ""}`) });
  const create = useMutation({
    mutationFn: (v: { code: string; name_zh: string; name_en?: string }) => call<{ id: string }>("POST", "/api/plm/products", { ...v, project_id: projectId }),
    onSuccess: (p) => { void qc.invalidateQueries({ queryKey: ["plm", "products"] }); navigate(`/plm/products/${p.id}`); },
    onError: (e) => message.error(errorText(e)),
  });
  return (
    <PageContainer>
      <ErrorAlert error={products.error} />
      <ProTable<Product> rowKey="id" search={false} loading={products.isLoading} dataSource={products.data} options={{ reload: () => void products.refetch() }}
        toolBarRender={() => [
          <Input.Search key="q" allowClear placeholder={t("plm.search", "产品代码 / 名称")} onSearch={setQ} />,
          <Button key="new" type="primary" onClick={() => setOpen(true)}>{t("plm.new_product", "新建产品")}</Button>]}
        columns={[
          { title: t("plm.code", "产品代码"), dataIndex: "code", render: (_d, r) => <Link to={`/plm/products/${r.id}`}>{r.code}</Link> },
          { title: t("plm.name", "名称"), dataIndex: "name_zh" },
          { title: "English", dataIndex: "name_en" },
          { title: t("plm.lifecycle", "生命周期"), dataIndex: "lifecycle_state", render: (_d, r) => <StatusTag status={r.lifecycle_state} /> },
          { title: t("plm.revisions", "修订版数"), dataIndex: "revisions", width: 100 },
          { title: t("common.updated", "更新时间"), dataIndex: "updated_at", valueType: "dateTime", width: 170 },
        ]} />
      <Modal open={open} title={t("plm.new_product", "新建产品")} onCancel={() => setOpen(false)} confirmLoading={create.isPending}
        onOk={async () => create.mutate(await form.validateFields())} destroyOnHidden>
        <Form form={form} layout="vertical">
          <Form.Item name="code" label={t("plm.code", "产品代码")} rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="name_zh" label={t("plm.name", "名称")} rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="name_en" label="English"><Input /></Form.Item>
        </Form>
      </Modal>
    </PageContainer>
  );
}

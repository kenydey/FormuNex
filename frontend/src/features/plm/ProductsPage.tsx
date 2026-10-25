import { PageContainer, ProTable } from "@ant-design/pro-components";
import { App, Button, Form, Input, Modal, Popconfirm, Select } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useUi } from "@/app/store";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { StatusTag } from "@/shared/tags";
import type { Product } from "./types";
import { BatchExportModal } from "./BatchExportModal";
import ExcelImportModal from "./ExcelImportModal";

interface CodingCompany { id: string; is_default: boolean; coding_rules: { categories: { name_zh: string; name_en: string; identifier: string }[]; code_digits: number | null } }

export default function ProductsPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const projectId = useUi((s) => s.projectId);
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [batchOpen, setBatchOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [form] = Form.useForm();
  const products = useQuery({ queryKey: ["plm", "products", q], queryFn: () => call<Product[]>("GET", `/api/plm/products${q ? `?q=${encodeURIComponent(q)}` : ""}`) });
  const companies = useQuery({ queryKey: ["companies"], queryFn: () => call<CodingCompany[]>("GET", "/api/plm/companies") });
  const categories = companies.data?.find((c) => c.is_default)?.coding_rules?.categories ?? [];
  const lifecycle = useMutation({
    mutationFn: (job: { id: string; action: "archive" | "restore" }) => call("POST", `/api/plm/products/${job.id}/${job.action}`),
    onSuccess: () => { message.success("已保存"); void qc.invalidateQueries({ queryKey: ["plm", "products"] }); },
    onError: (e) => message.error(errorText(e)),
  });
  const chosenCategory = Form.useWatch("category_identifier", form);
  const create = useMutation({
    mutationFn: (v: { code?: string; category_identifier?: string; legacy_code?: string; name_zh: string; name_en?: string }) => call<{ id: string }>("POST", "/api/plm/products", { ...v, project_id: projectId }),
    onSuccess: (p) => { void qc.invalidateQueries({ queryKey: ["plm", "products"] }); navigate(`/plm/products/${p.id}`); },
    onError: (e) => message.error(errorText(e)),
  });
  const submitNew = async () => {
    const v = await form.validateFields();
    if (v.code && v.category_identifier) { message.error("产品代码与自动编码 Category 二选一"); return; }
    if (!v.code && !v.category_identifier) { message.error("请填写产品代码或选择自动编码 Category"); return; }
    create.mutate(v);
  };
  return (
    <PageContainer>
      <ErrorAlert error={products.error} />
      <ProTable<Product> rowKey="id" search={false} loading={products.isLoading} dataSource={products.data} options={{ reload: () => void products.refetch() }}
        toolBarRender={() => [
          <Input.Search key="q" allowClear placeholder={t("plm.search", "产品代码 / 名称")} onSearch={setQ} />,
          <Button key="batch" onClick={() => setBatchOpen(true)}>{t("plm.batch_export", "批量导出")}</Button>,
          <Button key="import" onClick={() => setImportOpen(true)}>Excel 导入</Button>,
          <Button key="new" type="primary" onClick={() => setOpen(true)}>{t("plm.new_product", "新建产品")}</Button>]}
        columns={[
          { title: t("plm.code", "产品代码"), dataIndex: "code", render: (_d, r) => <Link to={`/plm/products/${r.id}`}>{r.code}</Link> },
          { title: t("plm.name", "名称"), dataIndex: "name_zh" },
          { title: "English", dataIndex: "name_en" },
          { title: t("plm.lifecycle", "生命周期"), dataIndex: "lifecycle_state", render: (_d, r) => <StatusTag status={r.lifecycle_state} /> },
          { title: t("plm.revisions", "修订版数"), dataIndex: "revisions", width: 100 },
          { title: t("common.updated", "更新时间"), dataIndex: "updated_at", valueType: "dateTime", width: 170 },
          { title: "操作", render: (_d, r) => r.lifecycle_state === "withdrawn"
            ? <Popconfirm title="恢复该产品？恢复后生命周期回到研发中（rd）。" onConfirm={() => lifecycle.mutate({ id: r.id, action: "restore" })}><Button type="link">恢复</Button></Popconfirm>
            : <Popconfirm title="归档该产品？归档后生命周期变为已撤回（withdrawn）。" onConfirm={() => lifecycle.mutate({ id: r.id, action: "archive" })}><Button type="link" danger>归档</Button></Popconfirm> },
        ]} />
      <BatchExportModal open={batchOpen} onClose={() => setBatchOpen(false)} />
      <ExcelImportModal open={importOpen} onClose={() => setImportOpen(false)} kind="formulas" />
      <Modal open={open} title={t("plm.new_product", "新建产品")} onCancel={() => setOpen(false)} confirmLoading={create.isPending}
        onOk={submitNew} destroyOnHidden>
        <Form form={form} layout="vertical">
          <Form.Item name="code" label={t("plm.code", "产品代码")}><Input disabled={!!chosenCategory} placeholder={chosenCategory ? "按编码规则自动生成" : undefined} /></Form.Item>
          {categories.length > 0 && <Form.Item name="category_identifier" label="自动编码 Category">
            <Select allowClear placeholder="选择后按默认公司编码规则自动生成代码"
              options={categories.map((c) => ({ label: `${c.name_zh} (${c.identifier})`, value: c.identifier }))}
              onChange={(v) => { if (v) form.setFieldsValue({ code: undefined }); }} />
          </Form.Item>}
          <Form.Item name="legacy_code" label="旧码对照（手工维护，不自动映射）"><Input /></Form.Item>
          <Form.Item name="name_zh" label={t("plm.name", "名称")} rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="name_en" label="English"><Input /></Form.Item>
        </Form>
      </Modal>
    </PageContainer>
  );
}

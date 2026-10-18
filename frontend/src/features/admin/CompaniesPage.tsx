import { PageContainer, ProTable, type ProColumns } from "@ant-design/pro-components";
import { App, Button, Checkbox, Form, Input, Modal, Tag, Upload } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useSession } from "@/app/session";
import { Forbidden, errorText } from "@/shared/feedback";

interface Company { id: string; name: string; name_en: string; address: string; phone: string; email: string; emergency_phone: string; unified_credit_code: string; has_logo: boolean; is_default: boolean; updated_at: string }

/** 公司配置（整合方案 §5）：SDS 第 1 节 / TDS / 标签的供应商信息来源，需 admin.companies。 */
export default function CompaniesPage() {
  const { t } = useTranslation();
  const { can } = useSession();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const [form] = Form.useForm();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Company | null>(null);
  const companies = useQuery({ queryKey: ["companies"], queryFn: () => call<Company[]>("GET", "/api/plm/companies"), enabled: can("admin.companies") });
  const save = useMutation({
    mutationFn: (v: Record<string, unknown>) => editing
      ? call("PUT", `/api/plm/companies/${editing.id}`, v)
      : call("POST", "/api/plm/companies", v),
    onSuccess: () => { message.success(t("common.saved", "已保存")); setCreating(false); setEditing(null); void qc.invalidateQueries({ queryKey: ["companies"] }); },
    onError: (e) => message.error(errorText(e)),
  });
  const setDefault = useMutation({
    mutationFn: (id: string) => call("POST", `/api/plm/companies/${id}/set-default`, {}),
    onSuccess: () => { message.success(t("admin.company_default", "已设为默认公司")); void qc.invalidateQueries({ queryKey: ["companies"] }); },
    onError: (e) => message.error(errorText(e)),
  });
  const uploadLogo = useMutation({
    mutationFn: async ({ id, file }: { id: string; file: File }) => {
      const fd = new FormData();
      fd.append("file", file);
      const r = await fetch(`/api/plm/companies/${id}/logo`, { method: "POST", body: fd, credentials: "include" });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    onSuccess: () => { message.success(t("admin.logo_uploaded", "Logo 已上传")); void qc.invalidateQueries({ queryKey: ["companies"] }); },
    onError: (e) => message.error(errorText(e)),
  });
  if (!can("admin.companies")) return <Forbidden permission="admin.companies" />;

  const columns: ProColumns<Company>[] = [
    { title: t("admin.company", "公司"), dataIndex: "name" },
    { title: "English", dataIndex: "name_en" },
    { title: t("admin.address", "地址"), dataIndex: "address" },
    { title: t("admin.phone", "电话"), dataIndex: "phone" },
    { title: t("admin.emergency", "24h 应急电话"), dataIndex: "emergency_phone" },
    { title: t("admin.credit_code", "统一社会信用代码"), dataIndex: "unified_credit_code" },
    { title: "Logo", dataIndex: "has_logo", render: (_d, r) => r.has_logo
      ? <img src={`/api/plm/companies/${r.id}/logo`} alt="logo" style={{ height: 28, maxWidth: 120, objectFit: "contain" }} /> : "—" },
    { title: t("common.status", "状态"), dataIndex: "is_default", render: (_d, r) => r.is_default ? <Tag color="green">默认</Tag> : "—" },
    { title: t("common.actions", "操作"), valueType: "option", render: (_d, r) => [
      <a key="edit" onClick={() => { setEditing(r); form.setFieldsValue(r); }}>编辑</a>,
      !r.is_default && <a key="default" onClick={() => setDefault.mutate(r.id)}>设为默认</a>,
      <Upload key="logo" accept="image/png,image/jpeg,image/gif,image/webp,image/svg+xml" showUploadList={false}
        beforeUpload={(file) => { void uploadLogo.mutateAsync({ id: r.id, file }); return false; }}>
        <a>上传 Logo</a>
      </Upload>,
    ] },
  ];
  const open = creating || !!editing;
  return (
    <PageContainer>
      <ProTable<Company> rowKey="id" search={false} columns={columns} dataSource={companies.data} loading={companies.isLoading}
        toolBarRender={() => [<Button key="new" type="primary" onClick={() => { form.resetFields(); setCreating(true); }}>新建公司</Button>]}
        options={{ reload: () => void companies.refetch() }} />
      <Modal open={open} title={editing ? "编辑公司" : "新建公司"} destroyOnHidden
        onCancel={() => { setCreating(false); setEditing(null); }} confirmLoading={save.isPending}
        onOk={async () => save.mutate(await form.validateFields())}>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="公司名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="name_en" label="英文名称"><Input /></Form.Item>
          <Form.Item name="address" label="地址"><Input /></Form.Item>
          <Form.Item name="phone" label="电话"><Input /></Form.Item>
          <Form.Item name="email" label="邮箱"><Input /></Form.Item>
          <Form.Item name="emergency_phone" label="24h 应急电话（SDS 强制非空）" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="unified_credit_code" label="统一社会信用代码"><Input /></Form.Item>
          <Form.Item name="is_default" valuePropName="checked"><Checkbox>设为默认公司</Checkbox></Form.Item>
        </Form>
      </Modal>
    </PageContainer>
  );
}

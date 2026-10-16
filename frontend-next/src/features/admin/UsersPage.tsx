import { PageContainer, ProTable, type ProColumns } from "@ant-design/pro-components";
import { App, Button, Form, Input, Modal, Select, Space, Tag } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useSession } from "@/app/session";
import { Forbidden, errorText } from "@/shared/feedback";

interface User { id: string; username: string; display_name: string; email: string | null; roles: string[]; is_active: boolean; last_login_at: string | null }
interface Role { code: string; name_zh: string; name_en: string; permissions: string[]; builtin: boolean }

export default function UsersPage() {
  const { t, i18n } = useTranslation();
  const { can } = useSession();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: () => call<User[]>("GET", "/api/identity/users"), enabled: can("admin.users") });
  const roles = useQuery({ queryKey: ["roles"], queryFn: () => call<Role[]>("GET", "/api/identity/roles") });
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<User | null>(null);
  const [form] = Form.useForm();
  const roleName = (code: string) => {
    const r = roles.data?.find((x) => x.code === code);
    return r ? (i18n.language === "en-US" ? r.name_en : r.name_zh) : code;
  };
  const roleOptions = (roles.data ?? []).map((r) => ({ value: r.code, label: i18n.language === "en-US" ? r.name_en : r.name_zh }));
  const save = useMutation({
    mutationFn: async (v: { username?: string; password?: string; display_name?: string; email?: string; roles: string[] }) =>
      editing ? call("PUT", `/api/identity/users/${editing.id}/roles`, { roles: v.roles }) : call("POST", "/api/identity/users", v),
    onSuccess: () => {
      message.success(t("common.saved", "已保存"));
      setCreating(false);
      setEditing(null);
      void qc.invalidateQueries({ queryKey: ["users"] });
    },
    onError: (e) => message.error(errorText(e)),
  });
  if (!can("admin.users")) return <Forbidden permission="admin.users" />;

  const columns: ProColumns<User>[] = [
    { title: t("users.username", "用户名"), dataIndex: "username" },
    { title: t("users.display_name", "姓名"), dataIndex: "display_name" },
    { title: t("users.roles", "角色"), dataIndex: "roles", render: (_d, r) => <Space wrap>{r.roles.map((x) => <Tag key={x}>{roleName(x)}</Tag>)}</Space> },
    { title: t("users.active", "状态"), dataIndex: "is_active", render: (_d, r) => (r.is_active ? <Tag color="green">{t("users.enabled", "启用")}</Tag> : <Tag>{t("users.disabled", "停用")}</Tag>) },
    { title: t("users.last_login", "最近登录"), dataIndex: "last_login_at", valueType: "dateTime" },
    { title: t("common.actions", "操作"), valueType: "option", render: (_d, r) => [
      <a key="roles" onClick={() => { setEditing(r); form.setFieldsValue({ roles: r.roles }); }}>{t("users.edit_roles", "调整角色")}</a>,
    ] },
  ];

  return (
    <PageContainer>
      <ProTable<User>
        rowKey="id" search={false} columns={columns} dataSource={users.data} loading={users.isLoading}
        toolBarRender={() => [<Button key="new" type="primary" onClick={() => { form.resetFields(); setCreating(true); }}>{t("users.new", "新建用户")}</Button>]}
        options={{ reload: () => void users.refetch() }}
      />
      <Modal open={creating || !!editing} title={editing ? t("users.edit_roles", "调整角色") : t("users.new", "新建用户")}
        onCancel={() => { setCreating(false); setEditing(null); }} confirmLoading={save.isPending}
        onOk={async () => save.mutate(await form.validateFields())} destroyOnClose>
        <Form form={form} layout="vertical">
          {!editing && <>
            <Form.Item name="username" label={t("users.username", "用户名")} rules={[{ required: true }]}><Input /></Form.Item>
            <Form.Item name="display_name" label={t("users.display_name", "姓名")}><Input /></Form.Item>
            <Form.Item name="email" label="Email"><Input type="email" /></Form.Item>
            <Form.Item name="password" label={t("login.password", "密码")} extra={t("password.policy", "至少 10 位，含大写、小写、数字、符号中的三类")}
              rules={[{ required: true, min: 10 }]}><Input.Password autoComplete="new-password" /></Form.Item>
          </>}
          <Form.Item name="roles" label={t("users.roles", "角色")}><Select mode="multiple" options={roleOptions} /></Form.Item>
        </Form>
      </Modal>
    </PageContainer>
  );
}

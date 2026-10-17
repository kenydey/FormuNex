import { PageContainer, ProTable } from "@ant-design/pro-components";
import { App, Button, Input, Popconfirm, Segmented, Space, Tag, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { ErrorAlert, errorText } from "@/shared/feedback";
import type { MemoryItem } from "./types";

type Scope = "" | "global" | "project" | "user";
const PAGE_SIZE = 20;

/** Agent long-term memory: scope filter, search, paging, delete. */
export default function MemoryPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const [scope, setScope] = useState<Scope>("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const scopeLabel: Record<string, string> = {
    global: t("library.memory.global", "全局"), project: t("library.memory.project", "项目"), user: t("library.memory.user", "个人"),
  };
  const list = useQuery({
    queryKey: ["memories", scope, q, page],
    queryFn: () => {
      const p = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
      if (scope) p.set("scope", scope);
      if (q) p.set("q", q);
      return call<{ items: MemoryItem[]; total: number }>("GET", `/api/memories?${p}`);
    },
  });
  const remove = useMutation({
    mutationFn: (id: number) => call("DELETE", `/api/memories/${id}`),
    onSuccess: () => { message.success(t("library.memory.deleted", "已删除")); void qc.invalidateQueries({ queryKey: ["memories"] }); },
    onError: (e) => message.error(errorText(e)),
  });

  return (
    <PageContainer subTitle={t("library.memory.sub", "跨会话保留的偏好与事实，按全局 / 项目 / 个人三域隔离；写入时自动拦截密钥与提示词注入内容")}>
      <Space wrap style={{ marginBottom: 12 }}>
        <Segmented<Scope> value={scope} onChange={(v) => { setScope(v); setPage(1); }} options={[
          { value: "", label: t("library.memory.all", "全部") }, { value: "global", label: scopeLabel.global },
          { value: "project", label: scopeLabel.project }, { value: "user", label: scopeLabel.user }]} />
        <Input.Search allowClear style={{ width: 320 }} placeholder={t("library.memory.search", "搜索 key / value…")} onSearch={(v) => { setQ(v.trim()); setPage(1); }} />
      </Space>
      <ErrorAlert error={list.error} />
      <ProTable<MemoryItem> rowKey="id" search={false} loading={list.isLoading} dataSource={list.data?.items} options={{ reload: () => void list.refetch() }}
        headerTitle={t("library.memory.total", "共 {{n}} 条", { n: list.data?.total ?? 0 })}
        pagination={{ current: page, pageSize: PAGE_SIZE, total: list.data?.total ?? 0, onChange: setPage, showSizeChanger: false }}
        columns={[
          { title: "key", dataIndex: "key", width: 220, render: (_d, m) => <Typography.Text strong>{m.key}</Typography.Text> },
          { title: "value", dataIndex: "value", render: (_d, m) => <Typography.Paragraph ellipsis={{ rows: 2, expandable: true }} style={{ marginBottom: 0 }}>{m.value}</Typography.Paragraph> },
          { title: t("library.memory.scope", "作用域"), width: 180, render: (_d, m) => (
            <Tag>{scopeLabel[m.scope] ?? m.scope}{m.scope !== "global" && m.scope_id ? ` · ${m.scope_id}` : ""}</Tag>) },
          { title: t("common.updated", "更新时间"), dataIndex: "updated_at", width: 180, valueType: "dateTime" },
          { title: t("common.actions", "操作"), width: 90, render: (_d, m) => (
            <Popconfirm title={t("library.memory.delete_q", "删除这条记忆？")} description={`${m.key}: ${m.value.slice(0, 80)}`}
              okButtonProps={{ danger: true }} onConfirm={() => remove.mutate(m.id)}>
              <Button type="link" danger size="small" loading={remove.isPending && remove.variables === m.id}>{t("common.delete", "删除")}</Button>
            </Popconfirm>) },
        ]} />
    </PageContainer>
  );
}

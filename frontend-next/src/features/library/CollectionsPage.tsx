import { ModalForm, PageContainer, ProFormDigit, ProFormSwitch, ProFormText, ProTable } from "@ant-design/pro-components";
import { App, Button, Drawer, Empty, Popconfirm, Space, Tag, Timeline, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { fmtEpoch, splitList } from "./logic";
import { WithProject } from "./shared";
import type { CollectionDetail, CollectionSummary } from "./types";

interface CreateForm { name: string; query: string; date_from?: string; date_to?: string; domains?: string; preset?: string; sched: boolean; interval: number }

const withProject = (path: string, projectId: string) => `${path}?${new URLSearchParams({ project_id: projectId })}`;

function CollectionsBody({ projectId }: { projectId: string }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const [openId, setOpenId] = useState<string | null>(null);
  const listKey = ["collections", projectId];
  const list = useQuery({ queryKey: listKey, queryFn: () => call<{ collections: CollectionSummary[] }>("GET", withProject("/api/collections", projectId)) });
  const detail = useQuery({
    queryKey: ["collections", projectId, openId], enabled: !!openId,
    queryFn: () => call<CollectionDetail>("GET", withProject(`/api/collections/${encodeURIComponent(openId!)}`, projectId)),
  });
  const invalidate = () => void qc.invalidateQueries({ queryKey: ["collections", projectId] });
  const create = useMutation({
    mutationFn: (v: CreateForm) => {
      const filters: Record<string, unknown> = {};
      if (v.date_from?.trim()) filters.date_from = v.date_from.trim();
      if (v.date_to?.trim()) filters.date_to = v.date_to.trim();
      const domains = splitList(v.domains ?? "");
      if (domains.length) filters.domain_allowlist = domains;
      return call<CollectionDetail>("POST", "/api/collections", {
        project_id: projectId, name: v.name.trim(), query: v.query.trim(), filters, screening_preset: v.preset?.trim() || undefined,
        schedule: { enabled: v.sched, interval_hours: Math.max(1, Number(v.interval) || 24) },
      });
    },
    onSuccess: (c) => { invalidate(); setOpenId(c.collection_id); message.success(t("library.coll.created", "集合已创建")); },
  });
  const refresh = useMutation({
    mutationFn: (id: string) => call("POST", withProject(`/api/collections/${encodeURIComponent(id)}/refresh`, projectId), {}),
    onSuccess: () => { invalidate(); message.success(t("library.coll.refreshed", "已刷新并生成快照")); },
    onError: (e) => message.error(errorText(e)),
  });
  const remove = useMutation({
    mutationFn: (id: string) => call("DELETE", withProject(`/api/collections/${encodeURIComponent(id)}`, projectId)),
    onSuccess: (_r, id) => { if (openId === id) setOpenId(null); invalidate(); message.success(t("library.coll.deleted", "集合已删除")); },
    onError: (e) => message.error(errorText(e)),
  });

  return (
    <>
      <ErrorAlert error={list.error} />
      <ProTable<CollectionSummary> rowKey="collection_id" search={false} loading={list.isLoading} dataSource={list.data?.collections}
        options={{ reload: () => void list.refetch() }} headerTitle={t("library.coll.title", "Smart Collections")}
        toolBarRender={() => [
          <ModalForm<CreateForm> key="new" title={t("library.coll.new", "新建集合")} modalProps={{ destroyOnHidden: true }}
            initialValues={{ sched: true, interval: 24 }}
            trigger={<Button type="primary" icon={<PlusOutlined />}>{t("library.coll.new", "新建集合")}</Button>}
            onFinish={async (v) => { try { await create.mutateAsync(v); return true; } catch (e) { message.error(errorText(e)); return false; } }}>
            <ProFormText name="name" label={t("library.coll.name", "名称")} placeholder={t("library.coll.name_ph", "如 水性防腐文献")} rules={[{ required: true, whitespace: true }]} />
            <ProFormText name="query" label={t("library.coll.query", "查询")} placeholder="waterborne conversion coating corrosion" rules={[{ required: true, whitespace: true }]} />
            <Space style={{ display: "flex" }}>
              <ProFormText name="date_from" label={t("library.coll.from", "起始年份")} placeholder="2020" />
              <ProFormText name="date_to" label={t("library.coll.to", "截止年份")} placeholder="2026" />
            </Space>
            <ProFormText name="domains" label={t("library.coll.domains", "域名白名单（逗号分隔）")} placeholder="sciencedirect.com" />
            <ProFormText name="preset" label={t("library.coll.preset", "筛选预设（可选）")} />
            <Space style={{ display: "flex" }}>
              <ProFormSwitch name="sched" label={t("library.coll.sched", "定时刷新")} />
              <ProFormDigit name="interval" label={t("library.coll.interval", "间隔（小时）")} min={1} fieldProps={{ precision: 0 }} />
            </Space>
          </ModalForm>,
        ]}
        columns={[
          { title: t("library.coll.name", "名称"), dataIndex: "name", render: (_d, c) => <a onClick={() => setOpenId(c.collection_id)}>{c.name}</a> },
          { title: t("library.coll.query", "查询"), dataIndex: "query", ellipsis: true },
          { title: t("library.coll.sched", "定时刷新"), width: 130, render: (_d, c) => (c.schedule.enabled
            ? <Tag color="processing">{t("library.coll.every", "每 {{h}} 小时", { h: c.schedule.interval_hours })}</Tag> : <Tag>{t("library.coll.off", "已关闭")}</Tag>) },
          { title: t("library.coll.last_run", "上次运行"), width: 170, render: (_d, c) => fmtEpoch(c.schedule.last_run) },
          { title: t("library.coll.snapshots", "快照"), width: 200, render: (_d, c) => (
            <Space size={4}>{c.snapshot_count ?? 0}{c.last_snapshot && <Typography.Text type="secondary">
              {t("library.coll.last", "上次：共 {{n}} · +{{a}} · −{{r}}", { n: c.last_snapshot.total, a: c.last_snapshot.added, r: c.last_snapshot.removed })}</Typography.Text>}</Space>) },
          { title: t("common.actions", "操作"), width: 160, render: (_d, c) => (
            <Space size={0}>
              <Button type="link" size="small" loading={refresh.isPending && refresh.variables === c.collection_id} onClick={() => refresh.mutate(c.collection_id)}>
                {t("library.coll.refresh", "刷新")}</Button>
              <Popconfirm title={t("common.confirm_delete", "确认删除？")} onConfirm={() => remove.mutate(c.collection_id)} okButtonProps={{ danger: true }}>
                <Button type="link" size="small" danger loading={remove.isPending && remove.variables === c.collection_id}>{t("common.delete", "删除")}</Button>
              </Popconfirm>
            </Space>) },
        ]} />
      <Drawer open={!!openId} onClose={() => setOpenId(null)} width={640} destroyOnHidden loading={detail.isLoading}
        title={detail.data ? t("library.coll.detail", "集合详情：{{n}}", { n: detail.data.name }) : ""}
        extra={openId && <Button loading={refresh.isPending} onClick={() => refresh.mutate(openId)}>{t("library.coll.refresh", "刷新")}</Button>}>
        <ErrorAlert error={detail.error} />
        {detail.data && (detail.data.snapshots.length === 0
          ? <Empty description={t("library.coll.no_snap", "暂无快照，点击“刷新”生成第一次快照")} />
          : <Timeline items={[...detail.data.snapshots].reverse().map((s) => ({
            key: s.snapshot_id,
            children: (
              <Space direction="vertical" size={2}>
                <span>{fmtEpoch(s.at)} · {t("library.coll.total", "共 {{n}}", { n: s.total })} <Tag color="success">+{s.added.length}</Tag><Tag color="error">−{s.removed.length}</Tag>
                  {s.actor && <Typography.Text type="secondary">（{s.actor}）</Typography.Text>}</span>
                {s.added.length > 0 && <Typography.Text>{t("library.coll.added", "新增：")}{s.added.slice(0, 10).join(", ")}{s.added.length > 10 ? ` …(${s.added.length})` : ""}</Typography.Text>}
                {s.removed.length > 0 && <Typography.Text type="secondary">{t("library.coll.removed", "移除：")}{s.removed.slice(0, 10).join(", ")}{s.removed.length > 10 ? ` …(${s.removed.length})` : ""}</Typography.Text>}
              </Space>),
          }))} />)}
      </Drawer>
    </>
  );
}

/** Smart Collections: saved query + filters, refreshed by hand or on a schedule; every refresh leaves a snapshot diff. */
export default function CollectionsPage() {
  const { t } = useTranslation();
  return (
    <PageContainer subTitle={t("library.coll.sub", "保存的查询 + 筛选规则：手动或定时刷新，结果进入文献 Manifest，每次刷新保留快照差异")}>
      <WithProject>{(projectId) => <CollectionsBody key={projectId} projectId={projectId} />}</WithProject>
    </PageContainer>
  );
}

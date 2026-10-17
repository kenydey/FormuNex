import { ReloadOutlined, RollbackOutlined } from "@ant-design/icons";
import { ProCard } from "@ant-design/pro-components";
import { App, Button, Empty, List, Popconfirm, Space, Tag, Tooltip, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { ErrorAlert, errorText } from "@/shared/feedback";

export interface PayloadVersion {
  version: number;
  cause: string;
  created_at: string | null;
  fields?: string[];
  chat_count?: number;
  source_count?: number;
}

export const historyKey = (projectId: string) => ["project", projectId, "history"];

/** Human label of a snapshot cause (``update`` / ``rollback-to-N`` / free text). */
export function causeParts(cause: string | null | undefined): { kind: "update" | "rollback" | "other"; version?: number; raw: string } {
  const raw = cause ?? "";
  if (raw === "update") return { kind: "update", raw };
  const m = /^rollback-to-(\d+)$/.exec(raw);
  if (m) return { kind: "rollback", version: Number(m[1]), raw };
  return { kind: "other", raw };
}

/**
 * 契约 / 项目 payload 版本历史 + 回滚 (legacy ProjectHistoryPanel). Every PUT /api/projects/{id} snapshots
 * the previous payload; rolling back snapshots the current payload first, so a rollback is itself undoable.
 */
export function ProjectHistory({ projectId, limit = 30, onRolledBack }: { projectId: string; limit?: number; onRolledBack?: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: [...historyKey(projectId), limit],
    queryFn: () => call<{ versions: PayloadVersion[] }>("GET", `/api/projects/${encodeURIComponent(projectId)}/history?limit=${limit}`),
    enabled: !!projectId,
  });
  const rollback = useMutation({
    mutationFn: (v: number) => call("POST", `/api/projects/${encodeURIComponent(projectId)}/rollback/${v}`, {}),
    onSuccess: (_d, v) => {
      message.success(t("requirements.history.rolled_back", "已回滚到 v{{v}}", { v }));
      void qc.invalidateQueries({ queryKey: ["project", projectId] });
      void qc.invalidateQueries({ queryKey: ["projects"] });
      onRolledBack?.();
    },
    onError: (e) => message.error(errorText(e)),
  });
  const versions = q.data?.versions ?? [];
  const causeText = (c: string) => {
    const p = causeParts(c);
    if (p.kind === "update") return t("requirements.history.cause_update", "保存");
    if (p.kind === "rollback") return t("requirements.history.cause_rollback", "回滚至 v{{v}} 前快照", { v: p.version });
    return p.raw || "—";
  };
  return (
    <ProCard size="small" title={t("requirements.history.title", "版本历史")}
      extra={<Button size="small" type="text" icon={<ReloadOutlined />} loading={q.isFetching} onClick={() => void q.refetch()} aria-label={t("requirements.refresh", "刷新")} />}>
      <ErrorAlert error={q.error} />
      {!q.isLoading && versions.length === 0 && !q.error
        ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t("requirements.history.empty", "尚无版本快照（保存项目后会出现）")} />
        : (
          <List<PayloadVersion> size="small" loading={q.isLoading} dataSource={versions} rowKey="version"
            renderItem={(v) => (
              <List.Item actions={[
                <Popconfirm key="rb" title={t("requirements.history.confirm", "确认回滚到 v{{v}}？", { v: v.version })}
                  description={t("requirements.history.confirm_desc", "当前内容会先快照，再恢复该版本。")}
                  okButtonProps={{ danger: true, loading: rollback.isPending && rollback.variables === v.version }}
                  onConfirm={() => rollback.mutateAsync(v.version).catch(() => undefined)}>
                  <Button size="small" icon={<RollbackOutlined />} disabled={rollback.isPending}>{t("requirements.history.rollback", "回滚")}</Button>
                </Popconfirm>,
              ]}>
                <List.Item.Meta
                  title={<Space size={4}><Tag color="blue">v{v.version}</Tag><Typography.Text>{causeText(v.cause)}</Typography.Text></Space>}
                  description={
                    <Tooltip title={v.fields?.join(", ")}>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {v.created_at?.slice(0, 19).replace("T", " ") ?? "—"} · {t("requirements.history.counts", "对话 {{c}} · 资料 {{s}}", { c: v.chat_count ?? 0, s: v.source_count ?? 0 })}
                      </Typography.Text>
                    </Tooltip>
                  } />
              </List.Item>
            )} />
        )}
    </ProCard>
  );
}

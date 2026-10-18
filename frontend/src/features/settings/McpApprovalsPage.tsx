import { PageContainer, ProTable } from "@ant-design/pro-components";
import { Alert, App, Button, Space, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { ErrorAlert, errorText } from "@/shared/feedback";
import type { McpApprovalItem } from "./types";

export const MCP_PENDING_KEY = ["settings", "mcp-approvals"] as const;
const POLL_MS = 3000;

type Decision = { id: number; decision: "allow" | "deny"; scope: "once" | "session" };

/** One-line preview of the call arguments (``key=value, …``); null when none were recorded. */
export function argsPreview(args: Record<string, unknown> | null | undefined, max = 80): string | null {
  if (args == null) return null;
  const text = Object.entries(args).map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`).join(", ");
  return text.length > max ? `${text.slice(0, max - 1)}…` : text || "{}";
}

/** MCP 工具审批中心: tool calls held by the ask policy; undecided requests are auto-denied after 5 minutes. */
export default function McpApprovalsPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: MCP_PENDING_KEY,
    queryFn: () => call<{ items: McpApprovalItem[]; total: number }>("GET", "/api/mcp/approvals/pending"),
    refetchInterval: POLL_MS,
  });
  const decide = useMutation({
    mutationFn: ({ id, decision, scope }: Decision) => call("POST", `/api/mcp/approvals/${id}/decide`, { decision, scope }),
    onSuccess: (_r, v) => message.success(v.decision === "deny" ? t("settings.mcp.denied", "已拒绝") : t("settings.mcp.allowed", "已允许")),
    // 404 = decided elsewhere or expired: the refetch below shows the authoritative list either way.
    onError: (e) => message.error(errorText(e)),
    onSettled: () => void qc.invalidateQueries({ queryKey: MCP_PENDING_KEY }),
  });
  const busy = (id: number) => decide.isPending && decide.variables?.id === id;
  return (
    <PageContainer title={t("settings.mcp.title", "MCP 工具审批")}>
      <Alert type="info" showIcon style={{ marginBottom: 12 }}
        message={t("settings.mcp.intro", "以下 MCP 工具调用被策略拦截（默认 ask），等待审批；无决策时后端 5 分钟后自动拒绝。本页每 3 秒刷新。")} />
      <ErrorAlert error={q.error} />
      <ProTable<McpApprovalItem>
        rowKey="request_id" search={false}
        expandable={{
          rowExpandable: (r) => r.arguments != null,
          expandedRowRender: (r) => (
            <pre data-testid="mcp-args" style={{ margin: 0, maxHeight: 260, overflow: "auto", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
              {JSON.stringify(r.arguments, null, 2)}
            </pre>
          ),
        }} loading={q.isLoading} dataSource={q.data?.items ?? []} pagination={false}
        options={{ reload: () => void q.refetch() }}
        headerTitle={t("settings.mcp.pending", "待审批 {{n}} 条", { n: q.data?.total ?? 0 })}
        locale={{ emptyText: t("settings.mcp.empty", "暂无待审批的工具调用") }}
        columns={[
          { title: t("settings.mcp.id", "请求 ID"), dataIndex: "request_id", width: 90 },
          { title: t("settings.mcp.server", "服务器"), dataIndex: "server_id", render: (_d, r) => <Typography.Text code>{r.server_id}</Typography.Text> },
          { title: t("settings.mcp.tool", "工具"), dataIndex: "tool_name", render: (_d, r) => <Typography.Text code>{r.tool_name}</Typography.Text> },
          { title: t("settings.mcp.args", "调用参数"), dataIndex: "arguments", ellipsis: true, render: (_d, r) => argsPreview(r.arguments)
            ?? <Typography.Text type="secondary">{t("settings.mcp.no_args", "（未记录）")}</Typography.Text> },
          { title: t("settings.mcp.session", "会话"), dataIndex: "session_id", ellipsis: true },
          { title: t("settings.mcp.project", "项目"), dataIndex: "project_id", ellipsis: true },
          { title: t("settings.mcp.age", "等待"), dataIndex: "age_s", width: 90, render: (_d, r) => t("settings.mcp.seconds", "{{n}} 秒", { n: Math.round(r.age_s) }) },
          { title: t("common.actions", "操作"), valueType: "option", width: 300, render: (_d, r) => [
            <Space key="a">
              <Button size="small" danger loading={busy(r.request_id) && decide.variables?.decision === "deny"}
                onClick={() => decide.mutate({ id: r.request_id, decision: "deny", scope: "once" })}>{t("settings.mcp.deny", "拒绝")}</Button>
              <Button size="small" loading={busy(r.request_id) && decide.variables?.scope === "once" && decide.variables.decision === "allow"}
                onClick={() => decide.mutate({ id: r.request_id, decision: "allow", scope: "once" })}>{t("settings.mcp.allow_once", "允许一次")}</Button>
              <Button size="small" type="primary" loading={busy(r.request_id) && decide.variables?.scope === "session"}
                onClick={() => decide.mutate({ id: r.request_id, decision: "allow", scope: "session" })}>{t("settings.mcp.allow_session", "允许本次会话")}</Button>
            </Space>,
          ] },
        ]}
      />
      <Typography.Paragraph type="secondary">
        {t("settings.mcp.args_redacted", "调用参数中名称像密钥的字段（token / key / password / secret / authorization）已由后端打码为 ***。展开行查看完整参数。")}
      </Typography.Paragraph>
    </PageContainer>
  );
}

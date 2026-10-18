/**
 * Approval prompts raised by agent runs: session plans (W3-9, irreversible approve / reject) and MCP tool calls the
 * policy intercepted (W3-11, allow once / for the session / deny). Both centres poll the pending lists, pop the oldest
 * request the user has not closed, and are also reachable from the header task centre.
 */
import { CheckOutlined, CloseOutlined } from "@ant-design/icons";
import { Alert, App, Button, Card, Descriptions, Empty, Modal, Space, Spin, Tag, Timeline, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { create } from "zustand";
import { call } from "@/api/client";
import { ErrorAlert } from "@/shared/feedback";
import { StatusTag } from "@/shared/tags";
import type { McpApprovalItem, SessionPlan, SessionPlanPendingItem } from "./types";

export const PLANS_KEY = ["chat", "session-plans", "pending"];
export const MCP_KEY = ["chat", "mcp-approvals", "pending"];

/** Pending lists, polled; a 403 / missing endpoint stops polling instead of retrying forever. */
export function usePendingPlans() {
  return useQuery({ queryKey: PLANS_KEY, retry: false, refetchInterval: (q) => (q.state.status === "error" ? false : 5000),
    queryFn: async () => (await call<{ items: SessionPlanPendingItem[] }>("GET", "/api/session-plans/pending")).items ?? [] });
}
export function usePendingMcp() {
  return useQuery({ queryKey: MCP_KEY, retry: false, refetchInterval: (q) => (q.state.status === "error" ? false : 3000),
    queryFn: async () => (await call<{ items: McpApprovalItem[] }>("GET", "/api/mcp/approvals/pending")).items ?? [] });
}

/** Which prompt is open (set by the centres' auto-pop or by a click in the task centre). */
export const useApprovalUi = create<{
  planId: string | null; mcp: McpApprovalItem | null;
  openPlan: (id: string | null) => void; openMcp: (it: McpApprovalItem | null) => void;
}>((set) => ({
  planId: null, mcp: null,
  openPlan: (planId) => set({ planId }), openMcp: (mcp) => set({ mcp }),
}));

const STEP_COLOR: Record<string, string> = { done: "green", in_progress: "blue", failed: "red", pending: "gray" };

export function SessionPlanModal({ planId, onClose }: { planId: string; onClose: () => void }) {
  const { t } = useTranslation();
  const { modal } = App.useApp();
  const qc = useQueryClient();
  const key = ["chat", "session-plans", planId];
  const plan = useQuery({ queryKey: key, queryFn: () => call<SessionPlan>("GET", `/api/session-plans/${encodeURIComponent(planId)}`) });
  const decide = useMutation({
    mutationFn: (approved: boolean) => call<SessionPlan>("POST", `/api/session-plans/${encodeURIComponent(planId)}/decide`, { approved, actor: null }),
    onSuccess: (p) => qc.setQueryData(key, p),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: PLANS_KEY });
      void plan.refetch(); // e.g. 409 = decided elsewhere: show the authoritative state
    },
  });
  const p = plan.data;
  const confirm = (approved: boolean) => modal.confirm({
    title: approved ? t("chat.plan.confirm_approve", "确认批准该计划？") : t("chat.plan.confirm_reject", "确认拒绝该计划？"),
    content: t("chat.plan.irreversible", "此操作不可逆。"),
    okButtonProps: { danger: !approved },
    onOk: () => decide.mutateAsync(approved).catch(() => undefined),
  });
  return (
    <Modal open onCancel={onClose} width={720} title={t("chat.plan.title", "会话计划审批")} data-testid="session-plan-modal"
      footer={p?.status === "pending" ? [
        <Button key="reject" danger icon={<CloseOutlined />} loading={decide.isPending} onClick={() => confirm(false)} data-testid="plan-reject-btn">
          {t("chat.plan.reject", "拒绝")}
        </Button>,
        <Button key="approve" type="primary" icon={<CheckOutlined />} loading={decide.isPending} onClick={() => confirm(true)} data-testid="plan-approve-btn">
          {t("chat.plan.approve", "批准执行")}
        </Button>,
      ] : [<Button key="close" onClick={onClose}>{t("chat.close", "关闭")}</Button>]}>
      <ErrorAlert error={plan.error ?? decide.error} />
      {plan.isLoading ? <Spin style={{ display: "block", margin: 32 }} /> : !p ? <Empty description={t("chat.plan.not_found", "未找到该计划")} /> : (
        <Space direction="vertical" style={{ width: "100%" }}>
          <Space wrap>
            <span data-testid="plan-status"><StatusTag status={p.status} /></span>
            <Typography.Text code>{p.plan_id}</Typography.Text>
            {p.decided_at && <Typography.Text type="secondary">
              {t("chat.plan.decided", "决策于 {{at}}", { at: p.decided_at })}{p.decided_by ? ` · ${p.decided_by}` : ""}
            </Typography.Text>}
          </Space>
          <div style={{ maxHeight: "50vh", overflow: "auto" }}>
            {p.phases.map((ph, i) => (
              <Card key={`${ph.name}-${i}`} size="small" style={{ marginBottom: 8 }}
                title={t("chat.plan.phase", "阶段 {{n}} · {{name}}", { n: i + 1, name: ph.name })}>
                <Timeline style={{ marginBottom: -16 }} items={ph.steps.map((s, j) => ({ key: j, color: STEP_COLOR[s.status] ?? "gray", children: s.desc }))} />
              </Card>
            ))}
          </div>
          {p.status !== "pending" && <Alert type="info" showIcon message={p.status === "approved"
            ? t("chat.plan.was_approved", "该计划已批准，审批不可逆。") : t("chat.plan.was_rejected", "该计划已拒绝，审批不可逆。")} />}
        </Space>
      )}
    </Modal>
  );
}

export function McpApprovalDialog({ request, onClose }: { request: McpApprovalItem; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const decide = useMutation({
    mutationFn: ({ decision, scope }: { decision: "allow" | "deny"; scope: "once" | "session" }) =>
      call("POST", `/api/mcp/approvals/${request.request_id}/decide`, { decision, scope }),
    onSuccess: onClose,
    onSettled: () => void qc.invalidateQueries({ queryKey: MCP_KEY }),
  });
  const args = request.arguments != null ? JSON.stringify(request.arguments, null, 2) : null;
  return (
    <Modal open onCancel={onClose} title={t("chat.mcp.title", "MCP 工具审批")} width={600} data-testid="mcp-approval-dialog" footer={[
      <Button key="deny" danger loading={decide.isPending} onClick={() => decide.mutate({ decision: "deny", scope: "once" })} data-testid="mcp-approval-deny">
        {t("chat.mcp.deny", "拒绝")}
      </Button>,
      <Button key="once" loading={decide.isPending} onClick={() => decide.mutate({ decision: "allow", scope: "once" })} data-testid="mcp-approval-allow-once">
        {t("chat.mcp.allow_once", "允许一次")}
      </Button>,
      <Button key="session" type="primary" loading={decide.isPending} onClick={() => decide.mutate({ decision: "allow", scope: "session" })} data-testid="mcp-approval-allow-session">
        {t("chat.mcp.allow_session", "允许本次会话")}
      </Button>,
    ]}>
      <Typography.Paragraph type="secondary">
        {t("chat.mcp.intro", "以下 MCP 工具调用被策略拦截（默认 ask），等待你的审批。无决策时后端 5 分钟后自动拒绝。")}
      </Typography.Paragraph>
      <Descriptions size="small" bordered column={1} items={[
        { key: "s", label: t("chat.mcp.server", "服务器"), children: <Typography.Text code data-testid="mcp-approval-server">{request.server_id}</Typography.Text> },
        { key: "t", label: t("chat.mcp.tool", "工具"), children: <Typography.Text code data-testid="mcp-approval-tool">{request.tool_name}</Typography.Text> },
        ...(request.session_id ? [{ key: "ss", label: t("chat.mcp.session", "会话"), children: <Typography.Text code>{request.session_id}</Typography.Text> }] : []),
        { key: "id", label: t("chat.mcp.request_id", "请求 ID"), children: request.request_id },
        ...(request.age_s != null ? [{ key: "age", label: t("chat.mcp.age", "已等待"), children: t("chat.seconds", "{{n}} 秒", { n: Math.round(request.age_s) }) }] : []),
      ]} />
      <Typography.Text type="secondary" style={{ display: "block", marginTop: 12 }}>{t("chat.mcp.args", "调用参数")}</Typography.Text>
      {args ? <pre data-testid="mcp-approval-args" style={{ maxHeight: 200, overflow: "auto" }}>{args}</pre>
        : <Tag>{t("chat.mcp.no_args", "（参数未记录）")}</Tag>}
      {decide.error != null && <Alert style={{ marginTop: 12 }} type="error" showIcon
        message={t("chat.mcp.failed", "提交失败：{{e}}", { e: decide.error instanceof Error ? decide.error.message : String(decide.error) })} />}
    </Modal>
  );
}

/** Mounted once in the shell: auto-pops pending requests (closing one hides it until it leaves the pending list). */
export function ApprovalCenters() {
  const plans = usePendingPlans();
  const mcp = usePendingMcp();
  const { planId, mcp: mcpReq, openPlan, openMcp } = useApprovalUi();
  const closedPlans = useRef(new Set<string>());
  const closedMcp = useRef(new Set<number>());
  const [, force] = useState(0);

  useEffect(() => {
    const live = new Set((plans.data ?? []).map((p) => p.plan_id));
    for (const id of [...closedPlans.current]) if (!live.has(id)) closedPlans.current.delete(id);
    if (!planId) {
      const next = (plans.data ?? []).find((p) => !closedPlans.current.has(p.plan_id));
      if (next) openPlan(next.plan_id);
    }
  }, [plans.data, planId, openPlan]);

  useEffect(() => {
    const items = mcp.data ?? [];
    const live = new Set(items.map((i) => i.request_id));
    for (const id of [...closedMcp.current]) if (!live.has(id)) closedMcp.current.delete(id);
    if (mcpReq && !live.has(mcpReq.request_id) && mcp.isSuccess) openMcp(null); // decided elsewhere
    else if (!mcpReq) {
      const next = items.find((i) => !closedMcp.current.has(i.request_id));
      if (next) openMcp(next);
    }
  }, [mcp.data, mcp.isSuccess, mcpReq, openMcp]);

  return (
    <>
      {planId && <SessionPlanModal key={planId} planId={planId} onClose={() => {
        closedPlans.current.add(planId);
        openPlan(null);
        force((n) => n + 1);
      }} />}
      {mcpReq && <McpApprovalDialog key={mcpReq.request_id} request={mcpReq} onClose={() => {
        closedMcp.current.add(mcpReq.request_id);
        openMcp(null);
        force((n) => n + 1);
      }} />}
    </>
  );
}

import { BellOutlined, CloseOutlined, EyeOutlined, StopOutlined } from "@ant-design/icons";
import { App, Badge, Button, Empty, List, Popover, Progress, Space, Tabs, Tag, Tooltip, Typography } from "antd";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { call } from "@/api/client";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import dayjs from "dayjs";
import { errorText } from "@/shared/feedback";
import { usePendingMcp, usePendingPlans, useApprovalUi } from "./Approvals";
import { ThinkingTimeline } from "./ThinkingTimeline";
import { acceptedTaskId, progressPct } from "./logic";
import {
  cancelTracked, dismissTask, isTerminal, mergeServerTasks, resumeTracking, trackTask, useTaskCenter, type ServerTask, type TrackedTask,
} from "./taskStore";

/** Auto-track every mutation that answers with ``{ task_id }`` (HTTP 202), so all pages feed the centre. */
function useAutoTrack() {
  const qc = useQueryClient();
  useEffect(() => {
    resumeTracking();
    return qc.getMutationCache().subscribe((ev) => {
      if (ev.type !== "updated" || ev.action.type !== "success") return;
      const id = acceptedTaskId(ev.action.data);
      if (!id) return;
      const meta = ev.mutation.options.meta as { taskTitle?: string; taskLink?: string } | undefined;
      trackTask(id, { title: meta?.taskTitle, link: meta?.taskLink });
    });
  }, [qc]);
}

function TaskRow({ task }: { task: TrackedTask }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const done = isTerminal(task.state);
  const status = { pending: "normal", running: "active", completed: "success", failed: "exception", cancelled: "normal" } as const;
  const stateLabel = {
    pending: t("chat.task.pending", "排队中"), running: t("chat.task.running", "运行中"), completed: t("chat.task.completed", "已完成"),
    failed: t("chat.task.failed", "失败"), cancelled: t("chat.task.cancelled", "已取消"),
  }[task.state];
  const color = { pending: "default", running: "processing", completed: "success", failed: "error", cancelled: "default" }[task.state];
  const title = task.title || task.kind || t("chat.task.untitled", "后台任务");
  return (
    <List.Item data-testid="task-row" actions={[
      task.server && !done && <Tooltip key="f" title={t("chat.task.follow", "跟踪进度")}>
        <Button size="small" type="text" icon={<EyeOutlined />} onClick={() => trackTask(task.id)} />
      </Tooltip>,
      !done && <Tooltip key="c" title={t("chat.task.cancel", "取消任务")}>
        <Button size="small" type="text" danger icon={<StopOutlined />}
          onClick={() => cancelTracked(task.id).catch((e) => message.error(errorText(e)))} />
      </Tooltip>,
      <Tooltip key="d" title={done ? t("chat.task.dismiss", "移除") : t("chat.task.stop_follow", "不再跟踪（后台任务继续运行）")}>
        <Button size="small" type="text" icon={<CloseOutlined />} onClick={() => dismissTask(task.id)} />
      </Tooltip>,
    ].filter(Boolean)}>
      <div style={{ width: "100%", minWidth: 0 }}>
        <Space size={6} wrap>
          {task.link ? <Link to={task.link}>{title}</Link> : <Typography.Text strong>{title}</Typography.Text>}
          <Tag color={color}>{stateLabel}</Tag>
          {task.startedAt > 0 && <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {dayjs(task.startedAt).format(dayjs(task.startedAt).isSame(dayjs(), "day") ? "HH:mm:ss" : "MM-DD HH:mm")}
          </Typography.Text>}
          {task.server && <Tag bordered={false}>{t("chat.task.server", "服务器")}</Tag>}
        </Space>
        {!done || task.state === "completed"
          ? <Progress size="small" percent={task.state === "completed" ? 100 : progressPct(task.progress)} status={status[task.state]} />
          : null}
        {task.message && <Typography.Paragraph type={task.state === "failed" ? "danger" : "secondary"} ellipsis={{ rows: 2, tooltip: task.message }}
          style={{ margin: 0, fontSize: 12 }}>{task.stage ? `[${task.stage}] ` : ""}{task.message}</Typography.Paragraph>}
        {task.steps.length > 0 && <ThinkingTimeline steps={task.steps} />}
      </div>
    </List.Item>
  );
}

/** Header notification / task centre: background tasks with progress and cancel, plus pending approvals. */
export function TaskCenter() {
  const { t } = useTranslation();
  useAutoTrack();
  const local = useTaskCenter((s) => s.tasks);
  const hidden = useTaskCenter((s) => s.hidden);
  const hide = useTaskCenter((s) => s.hide);
  const clearLocalFinished = useTaskCenter((s) => s.clearFinished);
  const [open, setOpen] = useState(false);
  // The caller's recent tasks from the server (durable task snapshots) — also those started in other tabs/devices.
  const server = useQuery({
    queryKey: ["tasks", "mine"],
    queryFn: () => call<{ items: ServerTask[] }>("GET", "/api/tasks?limit=30"),
    refetchInterval: open ? 10_000 : 60_000,
    retry: false,
  });
  const tasks = useMemo(() => mergeServerTasks(local, server.data?.items ?? [], hidden), [local, server.data, hidden]);
  const clearFinished = () => {
    hide(tasks.filter((x) => x.server && isTerminal(x.state)).map((x) => x.id));
    clearLocalFinished();
  };
  const markSeen = useTaskCenter((s) => s.markSeen);
  const plans = usePendingPlans();
  const mcp = usePendingMcp();
  const { openPlan, openMcp } = useApprovalUi();
  const running = local.filter((x) => !isTerminal(x.state)).length;
  const unseen = local.filter((x) => x.unseen).length;
  const approvals = (plans.data?.length ?? 0) + (mcp.data?.length ?? 0);
  const content = (
    <div style={{ width: 420 }} data-testid="task-centre">
      <Tabs size="small" items={[
        { key: "tasks", label: t("chat.task.tab", "后台任务 {{n}}", { n: tasks.length }), children: (
          <>
            <List size="small" dataSource={tasks} style={{ maxHeight: 420, overflow: "auto" }}
              locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t("chat.task.empty", "暂无后台任务")} /> }}
              renderItem={(task) => <TaskRow key={task.id} task={task} />} />
            {tasks.some((x) => isTerminal(x.state)) && (
              <Button size="small" type="link" onClick={clearFinished}>{t("chat.task.clear", "清除已结束")}</Button>
            )}
          </>
        ) },
        { key: "approvals", label: t("chat.approvals.tab", "待审批 {{n}}", { n: approvals }), children: (
          <List size="small" style={{ maxHeight: 420, overflow: "auto" }}
            dataSource={[
              ...(plans.data ?? []).map((p) => ({ key: `p-${p.plan_id}`, onClick: () => openPlan(p.plan_id),
                title: t("chat.approvals.plan", "会话计划"), desc: `${p.phase_names.join(" → ")} · ${t("chat.steps", "{{n}} 步", { n: p.step_count })}` })),
              ...(mcp.data ?? []).map((m) => ({ key: `m-${m.request_id}`, onClick: () => openMcp(m),
                title: t("chat.approvals.mcp", "MCP 工具调用"), desc: `${m.server_id} / ${m.tool_name}` })),
            ]}
            locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t("chat.approvals.empty", "暂无待审批事项")} /> }}
            renderItem={(it) => (
              <List.Item key={it.key} actions={[<Button key="o" size="small" onClick={it.onClick}>{t("chat.approvals.open", "审批")}</Button>]}>
                <List.Item.Meta title={it.title} description={it.desc} />
              </List.Item>
            )} />
        ) },
      ]} />
    </div>
  );
  return (
    <Popover trigger="click" placement="bottomRight" content={content} onOpenChange={(o) => { setOpen(o); if (o) { markSeen(); void server.refetch(); } }}>
      <Tooltip title={t("chat.task.centre", "任务与通知中心")}>
        <Badge count={running + approvals + unseen} size="small" data-testid="task-centre-badge">
          <Button type="text" icon={<BellOutlined />} aria-label={t("chat.task.centre", "任务与通知中心")} />
        </Badge>
      </Tooltip>
    </Popover>
  );
}

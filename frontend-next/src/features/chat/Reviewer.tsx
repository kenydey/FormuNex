import { AuditOutlined, ReloadOutlined } from "@ant-design/icons";
import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Alert, App, Button, Descriptions, Drawer, Empty, List, Space, Table, Tag, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { call } from "@/api/client";
import { useUi } from "@/app/store";
import { ErrorAlert, errorText } from "@/shared/feedback";
import type { RerunResult, ReviewChecklist, ReviewContext, ReviewRunDetail, ReviewRunSummary } from "./types";

const runPath = (id: string) => `/api/reviews/runs/${encodeURIComponent(id)}`;

function OutcomeTag({ outcome }: { outcome?: string | null }) {
  const { t } = useTranslation();
  if (outcome === "pass") return <Tag color="success">{t("chat.review.pass", "通过")}</Tag>;
  if (outcome === "flagged") return <Tag color="warning">{t("chat.review.flagged", "有发现")}</Tag>;
  return <Tag>{t("chat.review.none", "未结论")}</Tag>;
}

function StaleTag({ run }: { run: ReviewRunSummary }) {
  const { t } = useTranslation();
  if (run.stale === true) return <Tag color="error">{t("chat.review.stale", "已过期")}{run.stale_reason ? `：${run.stale_reason}` : ""}</Tag>;
  if (run.stale === "unverified") return <Tag color="warning">{t("chat.review.unverified", "新鲜度未验证")}</Tag>;
  return null;
}

/** Re-run the evidence audit of one Q&A; resolves to the new run id (null when the review passed without a new run). */
function useRerun(ctx: ReviewContext | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (runId: string) => {
      const res = await call<RerunResult>("POST", `${runPath(runId)}/rerun`, {
        question: ctx?.question ?? "", answer: ctx?.answer ?? "", citations: ctx?.citations ?? [], project_id: ctx?.projectId ?? null,
      });
      return res.fix?.run_id ?? null;
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["chat", "reviews"] }),
  });
}

/** Evidence-audit card under an assistant answer whose fix-loop ran (``reviewer_fix.run_id``). */
export function ReviewerCard({ runId, ctx }: { runId: string; ctx: ReviewContext }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [active, setActive] = useState(runId);
  const [auditOpen, setAuditOpen] = useState(false);
  const run = useQuery({ queryKey: ["chat", "reviews", "run", active], queryFn: () => call<ReviewRunDetail>("GET", runPath(active)) });
  const rerun = useRerun(ctx);
  if (run.isLoading) return <Typography.Text type="secondary" data-testid="reviewer-card">{t("chat.review.loading", "审计加载中…")}</Typography.Text>;
  if (run.error && !run.data) {
    return <Typography.Text type="secondary" data-testid="reviewer-card">{t("chat.review.unavailable", "审计记录不可用：{{e}}", { e: errorText(run.error) })}</Typography.Text>;
  }
  const r = run.data;
  return (
    <div data-testid="reviewer-card" style={{ marginTop: 8 }}>
      <Space wrap size={6}>
        <Typography.Text type="secondary">{t("chat.review.title", "证据审计")}</Typography.Text>
        <OutcomeTag outcome={r?.outcome} />
        <Tag color="warning" data-testid="reviewer-card-warn">⚠ {r?.warn_count ?? 0}</Tag>
        <Tag color="error" data-testid="reviewer-card-fail">✕ {r?.fail_count ?? 0}</Tag>
        {(r?.unaddressed_count ?? 0) > 0 && <Tag>{t("chat.review.unaddressed", "未处置 {{n}}", { n: r?.unaddressed_count })}</Tag>}
        {r && <StaleTag run={r} />}
        <Button size="small" icon={<ReloadOutlined />} loading={rerun.isPending} disabled={!ctx.question.trim()} data-testid="reviewer-card-rerun"
          title={ctx.question.trim() ? undefined : t("chat.review.no_question", "缺少提问内容，无法重审")}
          onClick={() => rerun.mutate(active, {
            onSuccess: (id) => (id ? setActive(id) : message.info(t("chat.review.up_to_date", "已是最新，无需重审"))),
            onError: (e) => message.error(t("chat.review.rerun_failed", "重审失败：{{e}}", { e: errorText(e) })),
          })}>
          {t("chat.review.rerun", "重审")}
        </Button>
        <Button size="small" icon={<AuditOutlined />} onClick={() => setAuditOpen(true)} data-testid="reviewer-card-audit">
          {t("chat.review.details", "审计详情")}
        </Button>
      </Space>
      {r?.stale === true && <Alert style={{ marginTop: 6 }} type="error" showIcon
        message={t("chat.review.stale_banner", "审计结论已过期——原结论不可信，可重审。")} description={r.stale_reason} />}
      <Drawer width={1000} open={auditOpen} onClose={() => setAuditOpen(false)} destroyOnHidden title={t("chat.review.title", "证据审计")}>
        <ReviewerAuditPanel initialRunId={active} projectId={r?.project_id ?? ctx.projectId ?? null} ctx={ctx} />
      </Drawer>
    </div>
  );
}

/** Review runs (newest first) + one run's claim-level action log, staleness and release checklist. */
export function ReviewerAuditPanel({ initialRunId, projectId, ctx, onSelect }: {
  initialRunId?: string | null; projectId?: string | null; ctx?: ReviewContext; onSelect?: (id: string) => void;
}) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<string | null>(initialRunId ?? null);
  const [checklistFor, setChecklistFor] = useState<string | null>(null);
  const qs = new URLSearchParams({ limit: "30" });
  if (projectId) qs.set("project_id", projectId);
  const runs = useQuery({ queryKey: ["chat", "reviews", "list", projectId ?? null],
    queryFn: async () => (await call<{ items: ReviewRunSummary[] }>("GET", `/api/reviews/runs?${qs}`)).items ?? [] });
  const detail = useQuery({ queryKey: ["chat", "reviews", "run", selected], enabled: !!selected,
    queryFn: () => call<ReviewRunDetail>("GET", runPath(selected!)) });
  const checklist = useQuery({ queryKey: ["chat", "reviews", "checklist", checklistFor], enabled: !!checklistFor && checklistFor === selected,
    queryFn: () => call<ReviewChecklist>("GET", `/api/reports/checklist/${encodeURIComponent(checklistFor!)}`) });
  const rerun = useRerun(ctx);
  const select = (id: string) => {
    setSelected(id);
    onSelect?.(id);
  };
  const d = detail.data;
  const canRerun = !!ctx?.question && !!ctx?.answer;
  return (
    <ProCard split="vertical" bordered>
      <ProCard colSpan="34%" title={t("chat.review.runs", "审计记录（最新在前）")} loading={runs.isLoading}
        extra={<Button size="small" type="text" icon={<ReloadOutlined />} onClick={() => void runs.refetch()} />}>
        <ErrorAlert error={runs.error} />
        <List size="small" dataSource={runs.data ?? []} locale={{ emptyText: t("chat.review.empty", "暂无审计记录") }}
          renderItem={(r) => (
            <List.Item key={r.run_id} data-testid="reviewer-audit-row" onClick={() => select(r.run_id)}
              style={{ cursor: "pointer", fontWeight: r.run_id === selected ? 600 : undefined }}>
              <List.Item.Meta title={<Typography.Text code ellipsis style={{ maxWidth: 220 }}>{r.run_id}</Typography.Text>}
                description={<Space size={4} wrap><OutcomeTag outcome={r.outcome} /><span>⚠{r.warn_count ?? 0} ✕{r.fail_count ?? 0}</span><StaleTag run={r} /></Space>} />
            </List.Item>
          )} />
      </ProCard>
      <ProCard loading={!!selected && detail.isLoading}>
        <ErrorAlert error={detail.error ?? rerun.error} />
        {!selected || !d ? <Empty description={t("chat.review.pick", "左侧选择一条审计记录查看详情")} /> : (
          <Space direction="vertical" style={{ width: "100%" }}>
            <Descriptions size="small" column={2} items={[
              { key: "id", label: "Run", children: <Typography.Text code copyable>{d.run_id}</Typography.Text>, span: 2 },
              { key: "st", label: t("chat.review.status", "状态"), children: `${d.status ?? "—"} / ${d.outcome ?? "—"}` },
              { key: "stale", label: t("chat.review.freshness", "新鲜度"), children: d.stale == null || d.stale === false ? "—" : <StaleTag run={d} /> },
            ]} />
            <Space>
              <Button size="small" icon={<ReloadOutlined />} disabled={!canRerun} loading={rerun.isPending}
                title={canRerun ? undefined : t("chat.review.need_ctx", "需从会话消息打开才能重审（缺问答输入）")}
                onClick={() => rerun.mutate(d.run_id, { onSuccess: (id) => id && select(id) })}>
                {t("chat.review.rerun", "重审")}
              </Button>
            </Space>
            {d.stale === true && <Alert type="error" showIcon message={t("chat.review.stale_banner", "审计结论已过期——原结论不可信，可重审。")} description={d.stale_reason} />}
            <Typography.Text strong>{t("chat.review.action_log", "Action log（断言级处置时间线）")}</Typography.Text>
            <Table size="small" pagination={false} rowKey="note"
              dataSource={Object.entries(d.dispositions ?? {}).map(([note, v]) => ({ note, ...v }))}
              locale={{ emptyText: t("chat.review.no_claims", "无断言记录") }}
              columns={[
                { title: t("chat.review.claim", "断言"), dataIndex: "note", ellipsis: true },
                { title: t("chat.review.claim_status", "状态"), dataIndex: "status", width: 100, render: (v) => v ?? "—" },
                { title: t("chat.review.disposition", "处置"), dataIndex: "disposition", width: 110,
                  render: (v: string | null) => <Tag color={v === "resolved" ? "success" : v === "open" ? "warning" : v === "unaddressed" ? "error" : "default"}>{v ?? "—"}</Tag> },
                { title: t("chat.review.reflags", "重标次"), dataIndex: "reflag_count", width: 80, render: (v) => v ?? 0 },
              ]} />
            <Space data-testid="reviewer-checklist">
              <Typography.Text strong>{t("chat.review.checklist", "发布清单（逐条 通过 / 标记）")}</Typography.Text>
              <Button size="small" data-testid="reviewer-checklist-load" loading={checklist.isFetching}
                onClick={() => (checklistFor === d.run_id ? void checklist.refetch() : setChecklistFor(d.run_id))}>
                {checklistFor === d.run_id && checklist.data ? t("chat.review.refresh", "刷新") : t("chat.review.gen_checklist", "生成清单")}
              </Button>
            </Space>
            {checklistFor === d.run_id && <ErrorAlert error={checklist.error} />}
            {checklistFor === d.run_id && checklist.data && (
              <>
                <Space data-testid="reviewer-checklist-summary">
                  <Tag color="success">{t("chat.review.pass", "通过")} {checklist.data.summary.pass}</Tag>
                  <Tag color="warning">{t("chat.review.mark", "标记")} {checklist.data.summary.flagged}</Tag>
                  <Tag>{t("chat.review.na", "不适用")} {checklist.data.summary.n_a}</Tag>
                </Space>
                <List size="small" dataSource={checklist.data.items} locale={{ emptyText: t("chat.review.no_items", "本次审计没有可核对的条目") }}
                  renderItem={(it) => (
                    <List.Item key={it.id} data-testid="reviewer-checklist-item">
                      <Space align="start">
                        <Tag color={it.verdict === "pass" ? "success" : it.verdict === "flagged" ? "warning" : "default"}>
                          {it.verdict === "pass" ? t("chat.review.pass", "通过") : it.verdict === "flagged" ? t("chat.review.mark", "标记") : t("chat.review.na", "不适用")}
                        </Tag>
                        <Tag>{categoryLabel(t, it.category)}</Tag>
                        <span>{it.statement}{it.reviewer_note && <Typography.Text type="secondary"> — {it.reviewer_note}</Typography.Text>}</span>
                      </Space>
                    </List.Item>
                  )} />
              </>
            )}
          </Space>
        )}
      </ProCard>
    </ProCard>
  );
}

function categoryLabel(t: TFunction, c: string) {
  switch (c) {
    case "citation": return t("chat.review.cat.citation", "引用");
    case "numeric": return t("chat.review.cat.numeric", "数值");
    case "method": return t("chat.review.cat.method", "方法");
    case "general": return t("chat.review.cat.general", "通用");
    default: return c;
  }
}

/** /knowledge/reviews[/:runId] — browse evidence-audit runs of the current project (re-run needs the chat context). */
export default function ReviewsPage() {
  const { t } = useTranslation();
  const { runId } = useParams();
  const navigate = useNavigate();
  const projectId = useUi((s) => s.projectId);
  return (
    <PageContainer subTitle={t("chat.review.sub", "问答回答的证据审计：断言处置时间线、过期提示与发布清单")}>
      <ReviewerAuditPanel key={projectId ?? "all"} initialRunId={runId ?? null} projectId={projectId}
        onSelect={(id) => navigate(`/knowledge/reviews/${encodeURIComponent(id)}`, { replace: true })} />
    </PageContainer>
  );
}

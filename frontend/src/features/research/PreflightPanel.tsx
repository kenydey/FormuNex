import { ModalForm, ProFormText, ProFormTextArea } from "@ant-design/pro-components";
import { Alert, App, Button, Empty, List, Segmented, Space, Tag, Tooltip, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import dayjs from "dayjs";
import { ApiError, call } from "@/api/client";
import { useSession } from "@/app/session";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { severityColor, sortFindings } from "./lib";
import type { PreflightFinding, PreflightState } from "./types";

export const PREFLIGHT_KINDS = ["storm", "tech_report_formulation", "tech_report_doe", "tech_report_optimization"] as const;
export type PreflightKind = (typeof PREFLIGHT_KINDS)[number];

/** 409 bodies of preflight finalize carry ``{errors, state}``: show the reasons, not raw JSON. */
function preflightError(e: unknown): string {
  const d = e instanceof ApiError ? (e.detail as { errors?: string[] } | undefined) : undefined;
  return d && Array.isArray(d.errors) && d.errors.length ? d.errors.join("；") : errorText(e);
}

/**
 * Publication preflight before a report export: citation / placeholder / numeric gates. Blocking findings make the
 * export 409; here they can be resolved (with a note) or overridden (actor + reason, audited), re-reviewed and finalized.
 */
export function PreflightPanel({ projectId, kind, onKindChange }: { projectId: string | null; kind: PreflightKind; onKindChange: (k: PreflightKind) => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { me, can } = useSession();
  const qc = useQueryClient();
  const [edit, setEdit] = useState<{ f: PreflightFinding; mode: "resolve" | "override" } | null>(null);
  const key = ["research", "preflight", projectId, kind];
  const state = useQuery({
    queryKey: key, enabled: !!projectId,
    queryFn: () => call<PreflightState>("GET", `/api/wiki/preflight/${encodeURIComponent(projectId!)}?kind=${encodeURIComponent(kind)}`),
  });
  const put = (s: PreflightState) => qc.setQueryData(key, s);
  const review = useMutation({
    mutationFn: () => call<PreflightState>("POST", "/api/wiki/preflight/review", { project_id: projectId, kind }),
    onSuccess: put, onError: (e) => message.error(preflightError(e)),
  });
  const finalize = useMutation({
    mutationFn: () => call<{ ok: boolean; errors: string[]; state: PreflightState }>("POST", "/api/wiki/preflight/finalize",
      { project_id: projectId, kind, actor: me?.display_name || me?.username || "user" }),
    onSuccess: (r) => { put(r.state); if (r.ok) message.success(t("research.pf.finalized", "已定稿")); else message.error(r.errors.join("；")); },
    onError: (e) => message.error(preflightError(e)),
  });
  const findings = useMemo(() => sortFindings(state.data?.findings ?? []), [state.data]);
  const labels: Record<PreflightKind, string> = {
    storm: t("research.pf.kind.storm", "STORM 长文"), tech_report_formulation: t("research.pf.kind.formulation", "配方技术报告"),
    tech_report_doe: t("research.pf.kind.doe", "DOE 技术报告"), tech_report_optimization: t("research.pf.kind.optimization", "优化技术报告"),
  };
  if (!projectId) return <Empty description={t("research.need_project", "请先在顶部选择项目，才能保存到货架或版本库。")} />;
  const s = state.data;
  const openBlocking = s?.open_blocking ?? 0;
  const summary = !s || !s.content_hash ? t("research.pf.none", "尚未预检：先生成报告并导出一次（或点「重新预检」）。")
    : openBlocking > 0 ? t("research.pf.blocked", "{{n}} 条阻断项未处理 —— 导出会被拒绝；处理或放行后可重试。", { n: openBlocking })
      : s.ready ? t("research.pf.ready", "预检通过，已定稿 {{at}} {{who}}", { at: s.finalization?.at ? dayjs(s.finalization.at * 1000).format("YYYY-MM-DD HH:mm") : "", who: s.finalization?.actor ?? "" })
        : t("research.pf.clear", "无未处理阻断项，导出时将自动定稿。");
  return (
    <>
      <Space wrap style={{ marginBottom: 12 }}>
        <Segmented value={kind} onChange={(v) => onKindChange(v as PreflightKind)} options={PREFLIGHT_KINDS.map((k) => ({ value: k, label: labels[k] }))} />
        <Button onClick={() => void state.refetch()} loading={state.isFetching}>{t("research.pf.reload", "刷新")}</Button>
        {kind === "storm" && <Tooltip title={t("research.pf.review_tip", "按当前已生成的 STORM 长文重新跑引用 / 占位符 / 数值检查")}>
          <Button loading={review.isPending} onClick={() => review.mutate()}>{t("research.pf.review", "重新预检")}</Button></Tooltip>}
        <Button type="primary" disabled={openBlocking > 0 || !s?.content_hash || !can("knowledge.write")} loading={finalize.isPending} onClick={() => finalize.mutate()}>
          {t("research.pf.finalize", "定稿")}</Button>
      </Space>
      <ErrorAlert error={state.error} />
      <Alert type={openBlocking > 0 ? "error" : s?.ready ? "success" : "info"} showIcon style={{ marginBottom: 12 }} message={summary}
        description={s && s.open_major > 0 ? t("research.pf.major", "另有 {{n}} 条主要问题待查看。", { n: s.open_major }) : undefined} />
      <List<PreflightFinding> bordered dataSource={findings} locale={{ emptyText: t("research.pf.no_findings", "无发现项") }}
        renderItem={(f) => {
          const handled = f.status !== "open";
          return (
            <List.Item actions={handled ? [] : [
              <a key="r" onClick={() => setEdit({ f, mode: "resolve" })}>{t("research.pf.resolve", "标记已处理")}</a>,
              <a key="o" onClick={() => setEdit({ f, mode: "override" })}>{t("research.pf.override", "放行…")}</a>,
            ]}>
              <List.Item.Meta
                title={<Space wrap>
                  <Tag color={severityColor(f.severity)}>{f.severity}</Tag>
                  <Typography.Text type="secondary">{f.check}</Typography.Text>
                  <span>{f.title}</span>
                  {f.stale && <Tooltip title={t("research.pf.stale_tip", "内容已变更，需重新确认")}><Tag color="warning">stale</Tag></Tooltip>}
                  <Tag color={handled ? "success" : "warning"}>{f.status === "open" ? t("research.pf.open", "待处理")
                    : f.status === "overridden" ? t("research.pf.overridden", "已放行") : t("research.pf.resolved", "已处理")}</Tag>
                </Space>}
                description={<>
                  {f.detail && <div>{f.detail}</div>}
                  {(f.evidence ?? []).length > 0 && <Typography.Text type="secondary" ellipsis={{ tooltip: f.evidence!.join(" | ") }}>{f.evidence!.slice(0, 2).join(" · ")}</Typography.Text>}
                  {handled && f.resolution && <div><Typography.Text type="secondary">{f.resolution.actor ?? "—"}：{f.resolution.reason ?? f.resolution.note ?? ""}</Typography.Text></div>}
                </>} />
            </List.Item>
          );
        }} />
      <ModalForm<{ actor: string; text: string }>
        open={!!edit} title={edit?.mode === "override" ? t("research.pf.override_title", "放行（接受风险并留痕）") : t("research.pf.resolve_title", "标记已处理")}
        modalProps={{ destroyOnHidden: true, onCancel: () => setEdit(null) }}
        submitter={{ submitButtonProps: { disabled: !can("knowledge.write") } }}
        initialValues={{ actor: me?.display_name || me?.username || "user", text: "" }}
        onFinish={async (v) => {
          if (!edit) return false;
          const body = { project_id: projectId, kind, finding_id: edit.f.id, actor: v.actor.trim(),
            ...(edit.mode === "override" ? { reason: v.text.trim() } : { note: v.text.trim() }) };
          try {
            put(await call<PreflightState>("POST", `/api/wiki/preflight/${edit.mode}`, body));
            setEdit(null);
            return true;
          } catch (e) {
            message.error(preflightError(e));
            return false;
          }
        }}>
        <Typography.Paragraph>{edit?.f.title}</Typography.Paragraph>
        <ProFormText name="actor" label={t("research.pf.actor", "操作人")} rules={[{ required: true, whitespace: true }]} fieldProps={{ maxLength: 120 }} />
        <ProFormTextArea name="text" label={edit?.mode === "override" ? t("research.pf.reason", "放行理由") : t("research.pf.note", "处理备注")}
          rules={[{ required: true, whitespace: true }]} fieldProps={{ maxLength: 2000 }} />
      </ModalForm>
    </>
  );
}

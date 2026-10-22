import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Alert, App, Badge, Button, Dropdown, Input, InputNumber, Modal, Select, Space, Switch, Table, Tag, Tooltip, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { BarChartOutlined, DownloadOutlined, HistoryOutlined, PaperClipOutlined, RedoOutlined, SaveOutlined, SnippetsOutlined, UndoOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useReducer, useRef, useState, type HTMLAttributes } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { call } from "@/api/client";
import { ErrorAlert } from "@/shared/feedback";
import { StructuredDiff } from "@/shared/StructuredDiff";
import { ContourChart } from "./charts";
import { saveText, useProjectWorkspace } from "./hooks";
import {
  applyPaste, buildSyncRows, editableColumns, effectiveObjectives, factorKeys, hasMeasurement, isRatingObjective, ledgerStats,
  loopSummary, loopTone, parseClipboard, RATING_OPTIONS, rowChanges, setCell, specText, STATUS_OPTIONS, toCsv, trainingRecords, valueMeetsSpec,
} from "./ledger";
import { followLoop, runLoop, useLoopRun } from "./loop";
import { BiasTrendCard, TrainingDataBanner } from "./panels";
import { RoundsDrawer } from "./RoundsDrawer";
import { RowDrawer } from "./RowDrawer";
import { surfacePoints } from "./surface";
import type { CampaignDetail, PredictionBias, Quality, SyncResponse, WorkbenchRow } from "./types";
import { emptyHistory, record, redo, shortcut, undo, type UndoHistory } from "./undo";

interface AttachmentCounts { campaign_id: number; counts: Record<string, number> }
/** Unsaved ledger state captured by undo / redo. */
interface LedgerSnap { rows: WorkbenchRow[]; dirty: Set<number> }

const STATUS_COLOR: Record<string, string> = { Pending: "default", "In Progress": "processing", Completed: "success", Blocked: "error" };
const TONE_COLOR: Record<string, string> = { converged: "gold", failed: "error", paused: "default", running: "processing", idle: "purple" };

function statusLabel(t: (k: string, d: string) => string, s: string): string {
  if (s === "Pending") return t("workbench.status.pending", "待开始");
  if (s === "In Progress") return t("workbench.status.in_progress", "进行中");
  if (s === "Completed") return t("workbench.status.completed", "已完成");
  if (s === "Blocked") return t("workbench.status.blocked", "受阻");
  return s;
}

/** 实验台账 (legacy ``LabWorkbench``): edit actual params / measurements, batch operations, sync → training + optional loop. */
export default function CampaignPage() {
  const { t } = useTranslation();
  const { message, modal } = App.useApp();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const campaignId = Number(useParams().campaignId);
  const [search, setSearch] = useSearchParams();
  const focusRow = search.get("row") ? Number(search.get("row")) : null;
  const { projectId, ws, project, patch } = useProjectWorkspace();
  const loopRun = useLoopRun();

  const detail = useQuery({ queryKey: ["workbench", campaignId], enabled: Number.isFinite(campaignId),
    queryFn: () => call<CampaignDetail>("GET", `/api/experiments/workbench/${campaignId}`) });
  const quality = useQuery({ queryKey: ["workbench", campaignId, "quality"], enabled: Number.isFinite(campaignId), retry: false,
    queryFn: () => call<Quality>("GET", `/api/experiments/workbench/${campaignId}/quality`) });
  const health = useQuery({ queryKey: ["health"], staleTime: 60_000, retry: false,
    queryFn: () => call<{ datalab?: { ledger_mode?: string } }>("GET", "/health") });
  const ledgerMode = health.data?.datalab?.ledger_mode;
  const attKey = useMemo(() => ["workbench", campaignId, "attachment-counts"], [campaignId]);
  const attCounts = useQuery({ queryKey: attKey, enabled: Number.isFinite(campaignId), retry: false,
    queryFn: () => call<AttachmentCounts>("GET", `/api/experiments/workbench/${campaignId}/attachment-counts`) });

  const [rows, setRows] = useState<WorkbenchRow[]>([]);
  const [dirty, setDirty] = useState<Set<number>>(new Set());
  const [selected, setSelected] = useState<number[]>([]);
  const [hint, setHint] = useState<string | null>(null);
  const [bias, setBias] = useState<PredictionBias | null>(null);
  const [drawerRow, setDrawerRow] = useState<number | null>(focusRow);
  const [drawerTab, setDrawerTab] = useState("detail");
  const [roundsOpen, setRoundsOpen] = useState(false);
  const [diffOpen, setDiffOpen] = useState(false);
  const [paste, setPaste] = useState<{ open: boolean; text: string; row: number; col: number }>({ open: false, text: "", row: 0, col: 0 });
  const [surface, setSurface] = useState<{ open: boolean; x?: string; y?: string; m?: string }>({ open: false });
  const [batchStatus, setBatchStatus] = useState<string>("Completed");

  // Undo / redo of unsaved edits: a bounded history of {rows, dirty} snapshots, cleared on save / reload.
  const history = useRef<UndoHistory<LedgerSnap>>(emptyHistory());
  const [, rerender] = useReducer((x: number) => x + 1, 0);
  const current = useRef<LedgerSnap>({ rows, dirty });
  current.current = { rows, dirty };
  const remember = useCallback((key?: string) => { history.current = record(history.current, current.current, key); rerender(); }, []);
  const clearHistory = useCallback(() => { history.current = emptyHistory(); rerender(); }, []);
  const step = useCallback((dir: "undo" | "redo") => {
    const r = (dir === "undo" ? undo : redo)(history.current, current.current);
    if (!r) return;
    history.current = r.history;
    setRows(r.state.rows);
    setDirty(r.state.dirty);
    rerender();
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const action = shortcut(e);
      const el = e.target as HTMLElement | null;
      // Leave text areas and dialogs (paste box, drawer note editor) to the browser's own undo.
      if (!action || el?.tagName === "TEXTAREA" || el?.closest?.(".ant-modal, .ant-drawer")) return;
      e.preventDefault();
      step(action);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [step]);

  // Reset local edits whenever a fresh server copy arrives (load, sync, restore).
  useEffect(() => {
    if (detail.data) {
      setRows(detail.data.rows);
      setDirty(new Set());
      clearHistory();
    }
  }, [detail.data, clearHistory]);
  useEffect(() => { setSelected([]); setHint(null); setBias(null); clearHistory(); }, [campaignId, clearHistory]);
  useEffect(() => { if (focusRow != null) setDrawerRow(focusRow); }, [focusRow]);

  const d = detail.data;
  const sameCampaignPlan = ws.doe_plan && ws.workbench_campaign_id === campaignId ? ws.doe_plan : null;
  const objectives = useMemo(() => effectiveObjectives(project.data?.workspace.requirement?.objectives, d?.objectives_snapshot), [project.data, d]);
  const factors = useMemo(() => factorKeys(sameCampaignPlan, rows), [sameCampaignPlan, rows]);
  const bounds = useMemo(() => new Map((sameCampaignPlan?.factors ?? []).map((f) => [f.name, f])), [sameCampaignPlan]);
  const cols = useMemo(() => editableColumns(factors, objectives), [factors, objectives]);
  const stats = ledgerStats(rows);
  const loop = loopSummary(d?.loop_status, d?.loop_history);
  const tone = loopRun.running ? "running" : loopTone(loop.status, loop.converged);

  const edit = useCallback((id: number, col: string, raw: unknown) => {
    remember(`${id}:${col}`);
    setRows((prev) => prev.map((r) => (r.id === id ? setCell(r, col, raw, objectives) : r)));
    setDirty((prev) => (prev.has(id) ? prev : new Set(prev).add(id)));
  }, [objectives, remember]);

  const applyToSelected = (mutate: (r: WorkbenchRow) => WorkbenchRow) => {
    remember();
    const ids = new Set(selected);
    setRows((prev) => prev.map((r) => (ids.has(r.id) ? mutate(r) : r)));
    setDirty((prev) => new Set([...prev, ...ids]));
  };
  const confirmBatch = (title: string, run: () => void) => {
    if (!selected.length) {
      void message.warning(t("workbench.select_first", "请先勾选至少一行"));
      return;
    }
    modal.confirm({ title, content: t("workbench.batch_unsaved", "修改尚未保存，需点「保存台账并同步」生效。"), onOk: run });
  };

  const save = useMutation({
    mutationFn: () => call<SyncResponse>("PUT", "/api/experiments/workbench/sync", {
      campaign_id: campaignId,
      rows: buildSyncRows(rows, objectives.map((o) => o.metric)),
      trigger_loop: ws.auto_loop_on_sync ? true : null,
      requirement: project.data?.workspace.requirement ? { ...project.data.workspace.requirement, project_id: projectId } : null,
      optimize_engine: ws.optimize_engine ?? "auto",
      doe_engine: ws.loop_doe_engine ?? "auto",
      campaign_state: ws.campaign_state ?? null,
    }),
    onSuccess: (res) => {
      const parts: string[] = [];
      if (res.training_message) parts.push(res.training_message);
      if (res.loop_message) parts.push(res.loop_message);
      if (typeof res.kg_written === "number" && res.kg_written < 0) parts.push(t("workbench.kg_failed", "KG 回流失败：{{e}}", { e: (res.kg_error ?? "").slice(0, 80) }));
      else if (typeof res.kg_written === "number" && res.kg_written > 0) parts.push(t("workbench.kg_written", "KG 回流 {{n}} 条实测证据", { n: res.kg_written }));
      if ((res.quality?.dropped_values ?? 0) > 0) parts.push(t("workbench.dropped", "质控丢弃 {{n}} 个非法值", { n: res.quality!.dropped_values }));
      setHint(parts.join(" · ") || t("workbench.synced", "已同步 {{n}} 行", { n: res.updated }));
      setBias(res.prediction_bias?.by_metric && Object.keys(res.prediction_bias.by_metric).length ? res.prediction_bias : null);
      qc.setQueryData<CampaignDetail>(["workbench", campaignId], (old) => (old ? { ...old, rows: res.rows, loop_status: res.loop_status ?? old.loop_status } : old));
      void qc.invalidateQueries({ queryKey: ["workbench", campaignId, "bias-trend"] });
      void qc.invalidateQueries({ queryKey: ["workbench", campaignId, "quality"] });
      void qc.invalidateQueries({ queryKey: attKey });
      void qc.invalidateQueries({ queryKey: ["workbench", "campaigns"] });
      void qc.invalidateQueries({ queryKey: ["training-status"] });
      if (res.loop_task_id) void followLoop(qc, projectId, res.loop_task_id, campaignId);
    },
  });

  const reconcile = useMutation({
    mutationFn: () => call("POST", `/api/experiments/workbench/${campaignId}/reconcile`, {}),
    onSuccess: () => { setHint(t("workbench.reconciled", "已清理失效引用")); void quality.refetch(); },
  });

  const train = useMutation({
    mutationFn: async () => {
      const req = project.data?.workspace.requirement;
      const records = trainingRecords(rows, objectives.map((o) => o.metric), req?.domain ?? project.data?.domain ?? "anticorrosion_coating", projectId, campaignId);
      if (!records.length) throw new Error(t("workbench.train_none", "请先在台账中完成至少一行（Completed）并填写实测值"));
      return call<{ message?: string }>("POST", "/api/experiments", { records, retrain: true });
    },
    onSuccess: (r) => {
      message.success(r.message || t("workbench.trained", "已回灌并训练"));
      void qc.invalidateQueries({ queryKey: ["models"] });
      void qc.invalidateQueries({ queryKey: ["training-status"] });
    },
  });

  const toggleFlag = (key: "auto_loop_on_sync" | "wiki_dossier_auto_patch" | "prediction_bias_soft_correct", on: boolean, confirmText: string) => {
    if (!projectId) return;
    const apply = () => patch.mutate({ [key]: on, ...(key === "auto_loop_on_sync" && on ? { auto_loop_round: 0 } : {}) });
    if (on) modal.confirm({ title: t("workbench.flag_confirm", "确认开启？"), content: <div style={{ whiteSpace: "pre-line" }}>{confirmText}</div>, onOk: apply });
    else apply();
  };

  const selRows = rows.filter((r) => selected.includes(r.id));
  const drawerData = rows.find((r) => r.id === drawerRow) ?? null;
  const completed = rows.filter((r) => r.status === "Completed");
  const sx = surface.x ?? factors[0];
  const sy = surface.y ?? factors[1];
  const sm = surface.m ?? objectives[0]?.metric;
  const sPoints = useMemo(() => (sx && sy && sm ? surfacePoints(rows, sx, sy, sm) : []), [rows, sx, sy, sm]);

  const openRow = useCallback((id: number, tab: string) => { setDrawerTab(tab); setDrawerRow(id); }, []);
  const rowMenuItems = useMemo(() => [
    { key: "detail", label: t("workbench.tab.detail", "详情") }, { key: "note", label: t("workbench.tab.note", "备注与标签") },
    { key: "attachments", label: t("workbench.tab.attachments", "附件") }, { key: "qc", label: t("workbench.tab.qc", "检测报告") },
    { key: "versions", label: t("workbench.tab.versions", "版本历史") }, { key: "lineage", label: t("workbench.tab.lineage", "谱系") },
  ], [t]);
  // Right-click on a ledger row opens the same menu as its 「更多」 button.
  const tableComponents = useMemo(() => ({ body: { row: (props: HTMLAttributes<HTMLTableRowElement> & { "data-row-key"?: number }) => {
    const id = props["data-row-key"];
    if (id == null) return <tr {...props} />;
    return (
      <Dropdown trigger={["contextMenu"]} menu={{ items: rowMenuItems, onClick: ({ key }) => openRow(Number(id), key) }}>
        <tr {...props} />
      </Dropdown>
    );
  } } }), [rowMenuItems, openRow]);
  const attCount = (id: number) => attCounts.data?.counts?.[String(id)];

  const columns: ColumnsType<WorkbenchRow> = [
    { title: "ID", dataIndex: "id", width: 64, fixed: "left",
      render: (id: number) => <Badge dot={dirty.has(id)} offset={[4, 0]}><Button type="link" size="small" style={{ padding: 0 }} onClick={() => { setDrawerTab("detail"); setDrawerRow(id); }}>{id}</Button></Badge> },
    { title: t("workbench.col.status", "状态"), dataIndex: "status", width: 140, fixed: "left",
      render: (s: string, r) => <Select size="small" variant="borderless" value={s || "Pending"} style={{ width: 130 }} onChange={(v) => edit(r.id, "status", v)}
        options={STATUS_OPTIONS.map((o) => ({ value: o, label: <Tag color={STATUS_COLOR[o]}>{statusLabel(t, o)}</Tag> }))} /> },
    ...(factors.length ? [{ title: t("workbench.group.planned", "DOE 计划"), children: factors.map((k) => ({
      title: k, key: `p:${k}`, width: 100, render: (_v: unknown, r: WorkbenchRow) => <Typography.Text type="secondary">{r.planned_params?.[k] ?? "—"}</Typography.Text>,
    })) }] : []),
    ...(factors.length ? [{ title: t("workbench.group.actual", "实验实际值"), children: factors.map((k) => ({
      title: k, key: `a:${k}`, width: 110, render: (_v: unknown, r: WorkbenchRow) => (
        <InputNumber size="small" style={{ width: 100 }} value={r.actual_params?.[k] ?? null} min={bounds.get(k)?.low} max={bounds.get(k)?.high}
          onChange={(v) => edit(r.id, `actual:${k}`, v)} placeholder={r.planned_params?.[k] != null ? String(r.planned_params[k]) : undefined} />
      ),
    })) }] : []),
    { title: t("workbench.group.metrics", "性能指标"), children: objectives.map((o) => ({
      title: <Tooltip title={specText(o) ? t("workbench.spec_is", "规格 {{s}}", { s: specText(o) }) : undefined}>{`${o.display_name || o.metric}${o.unit ? ` (${o.unit})` : ""}`}</Tooltip>,
      key: `m:${o.metric}`, width: 140,
      render: (_v: unknown, r: WorkbenchRow) => {
        const v = r.measurements?.[o.metric];
        const ok = valueMeetsSpec(o, v);
        if (isRatingObjective(o)) {
          return <Select size="small" allowClear style={{ width: 110 }} value={v == null || v === "" ? undefined : String(v)} status={ok === false ? "error" : undefined}
            onChange={(x) => edit(r.id, `meas:${o.metric}`, x ?? null)} options={RATING_OPTIONS.map((x) => ({ value: x }))} />;
        }
        return <InputNumber size="small" style={{ width: 120 }} value={v == null || v === "" ? null : Number(v)} status={ok === false ? "error" : undefined}
          onChange={(x) => edit(r.id, `meas:${o.metric}`, x)} placeholder={r.predicted?.[o.metric] != null ? `≈${r.predicted[o.metric]}` : undefined} />;
      },
    })) },
    { title: t("workbench.col.extra", "备注 / 标签"), key: "extra", width: 200,
      render: (_v, r) => (
        <Space size={4} wrap>
          {r.ingested && <Tag color="green">{t("workbench.ingested", "已入训练")}</Tag>}
          {(r.tags ?? []).map((g) => <Tag key={g}>{g}</Tag>)}
          {r.note && <Tooltip title={r.note}><Typography.Text type="secondary" ellipsis style={{ maxWidth: 120 }}>{r.note}</Typography.Text></Tooltip>}
        </Space>
      ) },
    { title: t("workbench.col.attachments", "附件"), key: "att", width: 72, align: "center",
      render: (_v, r) => {
        const n = attCount(r.id);
        return n == null ? <Typography.Text type="secondary">—</Typography.Text> : (
          <Button type="link" size="small" icon={<PaperClipOutlined />} data-testid={`att-count-${r.id}`} style={{ padding: 0 }}
            onClick={() => openRow(r.id, "attachments")}>{n}</Button>
        );
      } },
    { title: t("common.actions", "操作"), key: "act", width: 90, fixed: "right",
      render: (_v, r) => (
        <Dropdown menu={{ items: rowMenuItems, onClick: ({ key }) => openRow(r.id, key) }}>
          <Button size="small">{t("workbench.more", "更多")}</Button>
        </Dropdown>
      ) },
  ];

  const leave = (to: string) => {
    if (dirty.size) modal.confirm({ title: t("workbench.leave_confirm", "有 {{n}} 行未保存，确定离开？", { n: dirty.size }), onOk: () => navigate(to) });
    else navigate(to);
  };

  return (
    <PageContainer
      title={d ? `#${d.campaign_id} · ${d.name}` : t("workbench.ledger", "实验台账")}
      onBack={() => leave("/cockpit/workbench")}
      subTitle={d && <Space size={4}><Tag>{d.strategy}</Tag><Tag>{d.status}</Tag></Space>}
      tags={(loop.rounds > 0 || tone !== "idle") ? <Tooltip title={loop.message || undefined}><Tag color={TONE_COLOR[tone]} data-testid="workbench-loop-status">
        {t("workbench.loop.rounds", "闭环 {{n}} 轮", { n: loop.rounds })}
        {tone === "converged" ? ` · ${t("workbench.loop.converged", "已收敛")}` : tone === "failed" ? ` · ${t("workbench.loop.failed", "失败")}`
          : tone === "paused" ? ` · ${t("workbench.loop.paused", "已暂停")}` : tone === "running" ? ` · ${t("workbench.loop.running", "进行中")}` : ""}
      </Tag></Tooltip> : undefined}
      extra={[
        <Button key="loop" onClick={() => leave(`/cockpit/optimization?tab=loop&campaign=${campaignId}`)}>{t("workbench.open_loop", "闭环迭代")}</Button>,
        <Button key="rounds" icon={<HistoryOutlined />} onClick={() => setRoundsOpen(true)}>{t("workbench.rounds_btn", "轮次历史")}</Button>,
        <Button key="csv" icon={<DownloadOutlined />} disabled={!rows.length}
          onClick={() => saveText(toCsv(rows, factors, objectives), `DOE-C${campaignId}-${new Date().toISOString().slice(0, 10)}.csv`)}>{t("workbench.export_csv", "导出 CSV")}</Button>,
        <Button key="save" type="primary" icon={<SaveOutlined />} loading={save.isPending} disabled={!rows.length} onClick={() => save.mutate()}>
          {dirty.size ? t("workbench.save_n", "保存台账并同步（{{n}}）", { n: dirty.size }) : t("workbench.save", "保存台账并同步")}
        </Button>,
      ]}
    >
      <TrainingDataBanner />
      <ErrorAlert error={detail.error ?? save.error ?? reconcile.error ?? train.error ?? patch.error} />
      {ledgerMode === "eln_required_down" && <Alert type="error" showIcon style={{ marginBottom: 12 }}
        message={t("workbench.eln_down", "ELN（Datalab）为必需但当前不可达，台账写入可能失败")} />}
      {ledgerMode === "eln" && <Alert type="info" showIcon style={{ marginBottom: 12 }} message={t("workbench.eln_on", "已连接外部 ELN：台账同时写入 Datalab，并保留逐次版本")} />}
      {!!d?.objectives_snapshot?.length && (
        <Alert type="info" style={{ marginBottom: 12 }} message={<Space wrap size={4}>{t("workbench.frozen", "本台账指标已冻结：")}
          {d.objectives_snapshot.map((o) => <Tag key={o.metric}>{o.display_name || o.metric}</Tag>)}</Space>} />
      )}
      {hint && !save.error && <Alert type="success" showIcon closable onClose={() => setHint(null)} style={{ marginBottom: 12 }} message={hint} data-testid="workbench-save-hint" />}
      {loopRun.running && <Alert type="info" showIcon style={{ marginBottom: 12 }}
        message={t("workbench.loop_following", "闭环进行中：{{m}}", { m: loopRun.event?.message || loopRun.event?.stage || "…" })} />}
      {loopRun.retry && !loopRun.running && (
        <Alert type="error" showIcon style={{ marginBottom: 12 }} data-testid="workbench-loop-retry"
          message={t("workbench.loop_failed", "闭环失败：{{e}}", { e: loopRun.error ?? "" })}
          action={projectId && <Button size="small" danger onClick={() => void runLoop(qc, projectId, campaignId)}>{t("workbench.loop_retry", "重试闭环")}</Button>} />
      )}
      {bias && (
        <Alert type="warning" closable onClose={() => setBias(null)} style={{ marginBottom: 12 }}
          message={t("workbench.bias_summary", "预测偏差校准（{{n}} 行，预测−实测）", { n: bias.n_rows })}
          description={<Space wrap size={4}>{Object.entries(bias.by_metric).map(([m, s]) => (
            <Tag key={m}>{m} · mean {s.mean_error > 0 ? `+${s.mean_error}` : s.mean_error} · rmse {s.rmse} · mae {s.mae} · n={s.n}</Tag>))}</Space>} />
      )}
      {quality.data && (quality.data.stale_count > 0 || quality.data.dropped_total > 0) && (
        <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={t("workbench.quality", "数据质量")}
          description={<Space wrap size={4}>
            {quality.data.stale_count > 0 && <Tag color="error">{t("workbench.quality.stale", "失效引用 {{n}}", { n: quality.data.stale_count })}</Tag>}
            {quality.data.errors_count > 0 && <Tag color="warning">{t("workbench.quality.errors", "探测失败 {{n}}", { n: quality.data.errors_count })}</Tag>}
            {quality.data.dropped_total > 0 && <Tag color="warning">{t("workbench.quality.dropped", "累计丢弃非法值 {{n}}", { n: quality.data.dropped_total })}</Tag>}
          </Space>}
          action={quality.data.stale_count > 0 && <Button size="small" loading={reconcile.isPending} onClick={() => reconcile.mutate()}>{t("workbench.reconcile", "清理失效引用")}</Button>} />
      )}

      <ProCard bordered>
        <Space wrap style={{ marginBottom: 12 }}>
          <Space.Compact>
            <Tooltip title={t("workbench.undo_tip", "撤销未保存的修改（Ctrl+Z）")}>
              <Button size="small" icon={<UndoOutlined />} disabled={!history.current.past.length} onClick={() => step("undo")}
                aria-label={t("workbench.undo", "撤销")} />
            </Tooltip>
            <Tooltip title={t("workbench.redo_tip", "重做（Ctrl+Shift+Z / Ctrl+Y）")}>
              <Button size="small" icon={<RedoOutlined />} disabled={!history.current.future.length} onClick={() => step("redo")}
                aria-label={t("workbench.redo", "重做")} />
            </Tooltip>
          </Space.Compact>
          <Select size="small" value={batchStatus} onChange={setBatchStatus} style={{ width: 130 }}
            options={STATUS_OPTIONS.map((o) => ({ value: o, label: statusLabel(t, o) }))} />
          <Button size="small" onClick={() => confirmBatch(t("workbench.batch_status_q", "将选中的 {{n}} 行状态设为「{{s}}」？", { n: selected.length, s: batchStatus }),
            () => applyToSelected((r) => ({ ...r, status: batchStatus })))}>{t("workbench.batch_status", "批量标状态")}</Button>
          <Button size="small" onClick={() => confirmBatch(t("workbench.batch_clear_q", "清空选中的 {{n}} 行的测量值？", { n: selected.length }),
            () => applyToSelected((r) => ({ ...r, measurements: {} })))}>{t("workbench.batch_clear", "清空选中测量")}</Button>
          <Button size="small" onClick={() => confirmBatch(t("workbench.batch_copy_q", "将选中的 {{n}} 行的计划参数复制为实际参数？", { n: selected.length }),
            () => applyToSelected((r) => ({ ...r, actual_params: { ...(r.planned_params ?? {}) } })))}>{t("workbench.batch_copy", "计划→实际")}</Button>
          <Button size="small" onClick={() => (selected.length === 2 ? setDiffOpen(true) : void message.warning(t("workbench.compare_two", "请勾选恰好 2 行进行对比")))}>
            {t("workbench.compare", "对比选中")}</Button>
          <Button size="small" icon={<SnippetsOutlined />} onClick={() => setPaste({ open: true, text: "", row: 0, col: 0 })}>{t("workbench.paste", "从 Excel 粘贴")}</Button>
          <Button size="small" icon={<BarChartOutlined />} type={surface.open ? "primary" : "default"} disabled={completed.length < 3 || factors.length < 2}
            onClick={() => setSurface((s) => ({ ...s, open: !s.open }))}>{t("workbench.contour", "响应曲面")}</Button>
        </Space>
        {surface.open && (
          <ProCard bordered size="small" style={{ marginBottom: 12 }}>
            <Space wrap style={{ marginBottom: 8 }}>
              <Select size="small" style={{ width: 160 }} value={sx} onChange={(x) => setSurface((s) => ({ ...s, x }))} options={factors.map((f) => ({ value: f }))} />
              ×
              <Select size="small" style={{ width: 160 }} value={sy} onChange={(y) => setSurface((s) => ({ ...s, y }))} options={factors.map((f) => ({ value: f }))} />
              →
              <Select size="small" style={{ width: 180 }} value={sm} onChange={(m) => setSurface((s) => ({ ...s, m }))} options={objectives.map((o) => ({ value: o.metric, label: o.display_name || o.metric }))} />
            </Space>
            <ContourChart points={sPoints} xLabel={sx ?? ""} yLabel={sy ?? ""} metric={sm ?? ""} height={380} />
          </ProCard>
        )}
        <Table<WorkbenchRow> size="small" rowKey="id" bordered loading={detail.isLoading} dataSource={rows} columns={columns}
          pagination={rows.length > 50 ? { pageSize: 50 } : false} scroll={{ x: "max-content" }}
          rowSelection={{ selectedRowKeys: selected, onChange: (k) => setSelected(k as number[]) }} components={tableComponents}
          rowClassName={(r) => (r.id === drawerRow ? "ant-table-row-selected" : "")} />
        <Space wrap style={{ marginTop: 12 }}>
          <Typography.Text type="secondary">
            {t("workbench.stats", "{{c}}/{{n}} 已完成 · {{m}} 有测量 · {{i}} 已入训练 · {{k}} 项指标", { c: stats.completed, n: stats.total, m: stats.measured, i: stats.ingested, k: objectives.length })}
          </Typography.Text>
          {dirty.size > 0 && <Tag color="warning">{t("workbench.unsaved", "{{n}} 行未保存", { n: dirty.size })}</Tag>}
        </Space>
      </ProCard>

      <ProCard bordered style={{ marginTop: 16 }} title={t("workbench.automation", "自动化（按项目保存）")} loading={!!projectId && project.isLoading}>
        {!projectId ? <Typography.Text type="secondary">{t("workbench.no_project", "在顶部选择项目后可设置项目级自动化开关")}</Typography.Text> : (
          <Space direction="vertical">
            <Space>
              <Switch checked={!!ws.auto_loop_on_sync} loading={patch.isPending} data-testid="workbench-auto-loop"
                onChange={(on) => toggleFlag("auto_loop_on_sync", on, t("workbench.auto_loop_confirm", "Completed 行保存后将触发 optimize + 下一轮 DOE。\n不会自动把 next_doe 写入台账（需在闭环页另开「自动采纳 DOE」）。"))} />
              {t("workbench.auto_loop", "保存后自动分析收敛并建议下一轮")}
            </Space>
            {!ws.auto_loop_on_sync && rows.some((r) => r.status === "Completed" && hasMeasurement(r)) && (
              <Typography.Text type="secondary">{t("workbench.auto_loop_suggest", "建议：已有 Completed + 测量行，可开启「保存后自动闭环」形成项目级飞轮（仍不自动采纳 DOE）。")}</Typography.Text>
            )}
            <Space>
              <Switch checked={!!ws.wiki_dossier_auto_patch} loading={patch.isPending}
                onChange={(on) => toggleFlag("wiki_dossier_auto_patch", on, t("workbench.dossier_confirm", "仅白名单事件（入库/DOE/台账/闭环等）刷新卷宗对应节；未知事件跳过。\n全局旗标仍可关。"))} />
              {t("workbench.dossier", "本项目卷宗事件自动更新")}
            </Space>
            <Space>
              <Switch checked={!!ws.prediction_bias_soft_correct} loading={patch.isPending}
                onChange={(on) => toggleFlag("prediction_bias_soft_correct", on, t("workbench.soft_confirm", "推荐/评分时会用台账 prediction_bias.mean_error 校正 predicted（predicted − mean_error）。\n不改实测值 / 台账行。"))} />
              {t("workbench.soft_correct", "本项目预测偏差软校准")}
              {ws.prediction_bias_soft_correct && <Tag color="purple">{t("workbench.soft_on", "已开（会改 predicted）")}</Tag>}
            </Space>
          </Space>
        )}
        <div style={{ marginTop: 16 }}>
          <Button loading={train.isPending} onClick={() => train.mutate()}>{t("workbench.train", "回灌实验结果并训练模型")}</Button>
          <Typography.Text type="secondary" style={{ marginLeft: 12 }}>
            {t("workbench.train_hint", "BayBE 主动 DOE 从台账 Completed 行读取实际参数与测量值；")}<Link to="/cockpit/models">{t("workbench.see_models", "查看模型")}</Link>
          </Typography.Text>
        </div>
      </ProCard>

      <div style={{ marginTop: 16 }}><BiasTrendCard campaignId={campaignId} /></div>

      <RowDrawer campaignId={campaignId} row={drawerData} objectives={objectives} factors={factors} tab={drawerTab} onTab={setDrawerTab}
        onClose={() => { setDrawerRow(null); if (focusRow != null) { search.delete("row"); setSearch(search, { replace: true }); } }}
        onAttachmentCount={(rowId, n) => qc.setQueryData<AttachmentCounts>(attKey, (old) => (old ? { ...old, counts: { ...old.counts, [String(rowId)]: n } } : old))}
        onRowSaved={(r) => {
          setRows((prev) => prev.map((x) => (x.id === r.id ? { ...x, note: r.note, tags: r.tags } : x)));
          void qc.invalidateQueries({ queryKey: ["training-status"] });
        }}
        onReload={() => {
          if (dirty.size) void message.info(t("workbench.reload_discard", "已从服务器重新加载，未保存的修改被丢弃"));
          void detail.refetch();
        }} />
      <RoundsDrawer campaignId={campaignId} open={roundsOpen} onClose={() => setRoundsOpen(false)} />
      <Modal open={diffOpen} onCancel={() => setDiffOpen(false)} footer={null} width={720} destroyOnHidden
        title={selRows.length === 2 ? t("workbench.diff_title", "对比 #{{a}} ↔ #{{b}}", { a: selRows[0].id, b: selRows[1].id }) : ""}>
        {selRows.length === 2 && <StructuredDiff changes={rowChanges(selRows[0], selRows[1])} />}
      </Modal>
      <Modal open={paste.open} onCancel={() => setPaste((p) => ({ ...p, open: false }))} title={t("workbench.paste", "从 Excel 粘贴")} destroyOnHidden
        okText={t("workbench.paste_apply", "填入")}
        onOk={() => {
          const cells = parseClipboard(paste.text);
          const res = applyPaste(rows, cells, paste.row, paste.col, cols, objectives);
          if (res.changed.length) remember();
          setRows(res.rows);
          setDirty((prev) => new Set([...prev, ...res.changed]));
          setPaste((p) => ({ ...p, open: false }));
          message.success(t("workbench.pasted", "已填入 {{n}} 行（未保存）", { n: res.changed.length }));
        }}>
        <Space wrap style={{ marginBottom: 8 }}>
          {t("workbench.paste_from_row", "起始行")}
          <Select size="small" style={{ width: 100 }} value={paste.row} onChange={(row) => setPaste((p) => ({ ...p, row }))}
            options={rows.map((r, i) => ({ value: i, label: `#${r.id}` }))} />
          {t("workbench.paste_from_col", "起始列")}
          <Select size="small" style={{ width: 200 }} value={paste.col} onChange={(col) => setPaste((p) => ({ ...p, col }))}
            options={cols.map((c, i) => ({ value: i, label: c === "status" ? t("workbench.col.status", "状态") : c.replace(/^actual:/, "⚙ ").replace(/^meas:/, "📏 ") }))} />
        </Space>
        <Input.TextArea rows={8} value={paste.text} onChange={(e) => setPaste((p) => ({ ...p, text: e.target.value }))}
          placeholder={t("workbench.paste_ph", "从 Excel / WPS 复制单元格后粘贴到这里（制表符分隔，每行一条记录）")} />
      </Modal>
    </PageContainer>
  );
}

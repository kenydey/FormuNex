import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Alert, App, Button, Divider, Drawer, Empty, Form, Input, InputNumber, Modal, Select, Space, Spin, Statistic, Table, Tag, Tooltip, Typography } from "antd";
import { ApartmentOutlined, BulbOutlined, DeleteOutlined, DownOutlined, DragOutlined, HistoryOutlined, UpOutlined } from "@ant-design/icons";
import { RevoGrid } from "@revolist/react-datagrid";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { call } from "@/api/client";
import { getAccessToken } from "@/api/auth";
import { useUi } from "@/app/store";
import { ErrorAlert, errorText } from "@/shared/feedback";
import type { ProjectDetail } from "@/features/spec/types";
import { ComplianceSummary, type Classification } from "@/features/plm/ComplianceSummary";
import {
  ROLES, ROLE_LABELS, applyAnnotations, extractAnnotations, fillWith, groupSubtotals, newRow, normalize, redLineViolations,
  rowCost, rowVoc, suggestRole, total, totalCost, totalVoc, type Row, type RowAnnotation,
} from "./formula";
import "./grid.css";
import { VersionHistory } from "@/features/formulation/VersionHistory";
import { ProvenanceDrawer } from "@/features/formulation/Provenance";
import { ExplainPanel } from "@/features/formulation/ExplainPanel";
import { ingredientsToEditorRows } from "@/features/formulation/logic";
import type { AdoptState } from "@/features/formulation/adopt";
import type { Formulation } from "@/features/formulation/types";

interface Lineage { lineage_id: string; versions: { id: string; version: number; name: string; domain: string; created_at: string | null; change_summary: string }[] }

/** 配方编辑器 (方案 §3.6): RevoGrid (paste from Excel, range edit) + live GB 30000 compliance side panel. */
export default function FormulationEditorPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const projectId = useUi((s) => s.projectId);
  const grid = useRef<HTMLRevoGridElement>(null);
  const [rows, setRows] = useState<Row[]>(() => [newRow(), newRow(), newRow()]);
  const [name, setName] = useState("");
  const [lineage, setLineage] = useState<{ lineage_id: string; parent: string } | null>(null);
  const [flash, setFlash] = useState<number | null>(null);
  const [loadOpen, setLoadOpen] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);
  const [cls, setCls] = useState<Classification | null>(null);
  const [clsError, setClsError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [form] = Form.useForm();
  const [historyOpen, setHistoryOpen] = useState(false);
  const [provOpen, setProvOpen] = useState(false);
  const [adopted, setAdopted] = useState<Formulation | null>(null);
  const location = useLocation();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const project = useQuery({
    queryKey: ["project", projectId], enabled: !!projectId,
    queryFn: () => call<ProjectDetail>("GET", `/api/projects/${projectId}`),
  });
  const spec = project.data?.workspace.spec_contract;
  const domain = project.data?.workspace.requirement?.domain ?? "anticorrosion_coating";

  // Editor-only annotations (cost / VOC / premix group): the backend version
  // snapshot drops unknown ingredient fields, so these live in localStorage,
  // keyed by lineage (see formula.ts). They are also sent in the save payload
  // for forward compatibility.
  const annKey = `formunex:editor-annotations:${lineage?.lineage_id ?? "new"}`;
  const readAnns = (key: string): Record<string, RowAnnotation> => {
    try { return JSON.parse(localStorage.getItem(key) ?? "{}") as Record<string, RowAnnotation>; }
    catch { return {}; }
  };
  useEffect(() => {
    const anns = extractAnnotations(rows);
    try {
      // Fresh mount shows three empty rows: don't wipe previously stored annotations.
      if (Object.keys(anns).length === 0 && localStorage.getItem(annKey) && !rows.some((r) => r.name.trim())) return;
      localStorage.setItem(annKey, JSON.stringify(anns));
    } catch { /* storage full / private mode: annotations stay session-only */ }
  }, [rows, annKey]);
  const patchRow = (uid: string, p: Partial<Row>) => setRows((rs) => rs.map((r) => (r.uid === uid ? { ...r, ...p } : r)));
  const moveRow = (uid: string, dir: -1 | 1) => setRows((rs) => {
    const i = rs.findIndex((r) => r.uid === uid);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= rs.length) return rs;
    const out = [...rs];
    [out[i], out[j]] = [out[j], out[i]];
    return out;
  });
  const reorderRow = (fromUid: string, toUid: string) => setRows((rs) => {
    const from = rs.findIndex((r) => r.uid === fromUid);
    const to = rs.findIndex((r) => r.uid === toUid);
    if (from < 0 || to < 0 || from === to) return rs;
    const out = [...rs];
    const [moved] = out.splice(from, 1);
    out.splice(out.findIndex((r) => r.uid === toUid), 0, moved);
    return out;
  });

  // Real-time compliance: WebSocket (/ws/formulations/{id}/compliance) with REST fallback.
  // The socket re-classifies on every edit; when it is down or not yet open, the debounced
  // REST call below takes over, so the panel never goes dead.
  const versionId = lineage?.parent ?? "new";
  const wsRef = useRef<WebSocket | null>(null);
  const wsAliveRef = useRef(false);
  useEffect(() => {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    // Browsers cannot set an Authorization header on a WebSocket handshake:
    // the access token (memory-only, see api/auth) travels as ?token=, and the
    // server closes the socket with 4401/4403 when it is missing or lacks
    // plm.document.write — the REST fallback below then applies the same gate.
    const token = getAccessToken();
    const wsBase = `${proto}//${window.location.host}/ws/formulations/${encodeURIComponent(versionId)}/compliance`;
    const ws = new WebSocket(token ? `${wsBase}?token=${encodeURIComponent(token)}` : wsBase);
    ws.onopen = () => { wsAliveRef.current = true; };
    ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data) as { type?: string; detail?: string } & Classification;
        if (data.type === "classification") { setCls(data); setClsError(null); setBusy(false); }
        else if (data.type === "error") { setClsError(data.detail ?? t("formula.compliance_failed", "实时合规失败")); setBusy(false); }
      } catch { /* ignore malformed frame */ }
    };
    ws.onerror = () => { wsAliveRef.current = false; };
    ws.onclose = () => { wsAliveRef.current = false; };
    wsRef.current = ws;
    return () => { ws.close(); wsRef.current = null; wsAliveRef.current = false; };
  }, [versionId, t]);

  // Debounced (400 ms) GB 30000 evaluation of the current composition.
  const filled = useMemo(() => rows.filter((r) => r.name.trim() && Number(r.weight_pct) > 0), [rows]);
  useEffect(() => {
    if (!filled.length) {
      setCls(null);
      return;
    }
    const h = setTimeout(async () => {
      const payload = {
        components: filled.map((r) => ({ name: r.zh_name || r.name, pct: Number(r.weight_pct), cas: r.cas_no || null })),
        properties: { phys_state: "liquid", ...(flash != null ? { flash_point_c: flash, sources: { flash_point_c: "measured" } } : {}) },
        include_transport: true,
      };
      // WebSocket path (preferred): send and let onmessage fill the panel.
      if (wsRef.current && wsAliveRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        setBusy(true);
        wsRef.current.send(JSON.stringify({ type: "classify", ...payload }));
        return;
      }
      // REST fallback: the socket is down, not yet open, or the browser blocks it.
      setBusy(true);
      try {
        setCls(await call<Classification>("POST", "/api/plm/ghs/classify", payload));
        setClsError(null);
      } catch (e) {
        setClsError(e);
      } finally {
        setBusy(false);
      }
    }, 400);
    return () => clearTimeout(h);
  }, [filled, flash]);

  const violations = useMemo(() => redLineViolations(rows, spec, cls ?? undefined), [rows, spec, cls]);
  const badUids = new Set(violations.map((v) => v.uid).filter(Boolean));
  const hazardByName = new Map((cls?.disclosure ?? []).map((d) => [d.name, d.hazards.map((h) => h.h_code ?? h.hazard_class).join(" ")]));
  const source = rows.map((r) => ({ ...r, hazards: hazardByName.get(r.zh_name || r.name) ?? "", _class: badUids.has(r.uid) ? "redline" : r.locked ? "locked" : "" }));
  const sum = total(rows);

  const pull = async () => {
    const data = (await grid.current?.getSource()) as Row[] | undefined;
    if (!data) return;
    // The grid does not carry the editor-only fields (cost / VOC / group):
    // merge its output with the previous rows by uid so edits are not lost.
    setRows((prev) => {
      const byUid = new Map(prev.map((r) => [r.uid, r]));
      return data.map((r) => {
        const old = byUid.get(r.uid);
        return { ...r, weight_pct: Number(r.weight_pct) || 0, locked: r.locked === true || String(r.locked) === "true",
          cost_per_kg: old?.cost_per_kg, voc_g_l: old?.voc_g_l, group: old?.group };
      });
    });
  };

  const loadVersion = async (versionId: string) => {
    const v = await call<{ snapshot: { name?: string; ingredients?: { name: string; zh_name?: string; cas_no?: string; role?: string; weight_pct: number }[] }; lineage_id: string; id: string; name: string }>(
      "GET", `/api/formulations/versions/detail/${versionId}`);
    setRows(applyAnnotations(
      (v.snapshot.ingredients ?? []).map((i) => ({ ...newRow(), name: i.name, zh_name: i.zh_name, cas_no: i.cas_no, role: i.role, weight_pct: i.weight_pct })),
      readAnns(`formunex:editor-annotations:${v.lineage_id}`),
    ));
    setName(v.name);
    setLineage({ lineage_id: v.lineage_id, parent: v.id });
    setLoadOpen(false);
  };

  // Entry points: ?version=<id> (from the lineage page) and router state { adopt } (from recommend / leaderboard).
  const versionParam = params.get("version");
  useEffect(() => {
    if (!versionParam) return;
    void loadVersion(versionParam).catch((e) => message.error(errorText(e)));
    setParams({}, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [versionParam]);
  useEffect(() => {
    const st = location.state as AdoptState | null;
    if (!st?.adopt) return;
    setRows(applyAnnotations(ingredientsToEditorRows(st.adopt.ingredients).map((i) => ({ ...newRow(), ...i })), readAnns("formunex:editor-annotations:new")));
    setName(st.adopt.name);
    setLineage(null);
    setAdopted(st.adopt);
    navigate(location.pathname, { replace: true, state: null });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.state]);
  const current: Formulation = { name: name || t("formula.untitled", "未命名配方"), domain, source: "manual",
    ingredients: filled.map((r) => ({ name: r.name, zh_name: r.zh_name, cas_no: r.cas_no || null, role: r.role || "other", weight_pct: Number(r.weight_pct),
      // Editor-only annotations (cost / VOC / group): sent for forward compatibility; the backend drops unknown fields.
      cost_per_kg: r.cost_per_kg, voc_g_l: r.voc_g_l, group: r.group })) };

  const save = useMutation({
    mutationFn: (v: { name: string; change_summary: string }) => call<{ id: string; lineage_id: string; version: number }>("POST", "/api/formulations/versions", {
      formulation: { name: v.name, domain, source: "manual",
        ingredients: filled.map((r) => ({ name: r.name, zh_name: r.zh_name, cas_no: r.cas_no || null, role: r.role || "other", weight_pct: Number(r.weight_pct),
          cost_per_kg: r.cost_per_kg, voc_g_l: r.voc_g_l, group: r.group })) },
      lineage_id: lineage?.lineage_id ?? null, parent_version_id: lineage?.parent ?? null,
      change_summary: v.change_summary, project_id: projectId,
    }),
    onSuccess: (out) => {
      setLineage({ lineage_id: out.lineage_id, parent: out.id });
      setName(form.getFieldValue("name"));
      setSaveOpen(false);
      message.success(t("formula.saved", "已保存为第 {{v}} 版", { v: out.version }));
    },
    onError: (e) => message.error(errorText(e)),
  });

  const columns = [
    { prop: "name", name: t("formula.name", "原料"), size: 180 },
    { prop: "zh_name", name: t("formula.zh_name", "中文名"), size: 140 },
    { prop: "cas_no", name: "CAS", size: 120 },
    { prop: "role", name: t("formula.role", "角色"), size: 110 },
    { prop: "weight_pct", name: t("formula.pct", "质量分数 %"), size: 110, columnType: "numeric" },
    { prop: "locked", name: t("formula.locked", "锁定"), size: 70 },
    { prop: "hazards", name: t("formula.hazards", "危害"), size: 160, readonly: true },
  ];

  return (
    <PageContainer
      title={name || t("formula.untitled", "未命名配方")}
      subTitle={lineage ? t("formula.lineage", "版本链 {{id}}", { id: lineage.lineage_id.slice(0, 8) }) : t("formula.new", "新配方")}
      extra={<Space wrap>
        <Link to="/lab/recommend"><Button icon={<BulbOutlined />}>{t("formulation.editor.recommend", "AI 推荐")}</Button></Link>
        <Button icon={<HistoryOutlined />} onClick={() => setHistoryOpen(true)}>{t("formulation.editor.history", "版本历史与对比")}</Button>
        <Tooltip title={!lineage ? t("formulation.editor.prov_need_save", "保存为版本后可查看证据谱系") : undefined}>
          <Button icon={<ApartmentOutlined />} disabled={!lineage} onClick={() => setProvOpen(true)}>{t("formulation.prov.open", "证据谱系")}</Button>
        </Tooltip>
        <Button onClick={() => setLoadOpen(true)}>{t("formula.open", "打开版本")}</Button>
        <Tooltip title={violations.some((v) => v.uid) ? t("formula.blocked", "命中合规红线，不能保存") : undefined}>
          <Button type="primary" disabled={!filled.length || violations.some((v) => v.uid)}
            onClick={() => { form.setFieldsValue({ name, change_summary: "" }); setSaveOpen(true); }}>{t("formula.save", "保存为新版本")}</Button>
        </Tooltip>
      </Space>}
    >
      {!projectId && <Alert type="info" showIcon style={{ marginBottom: 12 }} message={t("formula.no_project", "未选择项目：不检查项目的合规红线")} />}
      {adopted && <Alert type="success" showIcon closable style={{ marginBottom: 12 }} onClose={() => setAdopted(null)}
        message={t("formulation.editor.adopted", "已载入推荐配方「{{n}}」，调整后保存为新版本即开始记录谱系。", { n: adopted.name })}
        description={(adopted.explain || adopted.score != null) ? <ExplainPanel explain={adopted.explain} score={adopted.score} /> : undefined} />}
      <ProCard split="vertical" bordered>
        <ProCard>
          <div className="fx-grid" style={{ height: 420 }}>
            <RevoGrid ref={grid} source={source} columns={columns} range resize rowClass="_class" theme="compact"
              onAfteredit={() => void pull()} />
          </div>
          <Space style={{ marginTop: 12 }} wrap>
            <Statistic title={t("formula.total", "合计")} value={sum} suffix="%" valueStyle={{ color: Math.abs(sum - 100) <= 0.01 ? "#389e0d" : "#cf1322" }} />
            <Button onClick={() => setRows([...rows, newRow()])}>{t("formula.add", "添加行")}</Button>
            <Button onClick={() => { try { setRows(normalize(rows)); } catch (e) { message.error(errorText(e)); } }}>{t("formula.normalize", "按比例归一化（不含锁定行）")}</Button>
            <Select style={{ width: 220 }} placeholder={t("formula.fill", "以某组分补足到 100%")} value={null}
              options={filled.map((r) => ({ value: r.uid, label: r.zh_name || r.name }))}
              onChange={(uid: string) => { try { setRows(fillWith(rows, uid)); } catch (e) { message.error(errorText(e)); } }} />
            <Button danger onClick={() => setRows(rows.filter((r) => r.name.trim() || Number(r.weight_pct) > 0))}>{t("formula.prune", "删除空行")}</Button>
          </Space>
          <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>
            {t("formula.grid_help", "可直接从 Excel 粘贴区域；角色可选：{{roles}}；锁定列填 true 的行不参与归一化。", { roles: ROLES.join(" / ") })}
          </Typography.Paragraph>
          <DetailPanel
            rows={rows} onPatch={patchRow} onMove={moveRow} onReorder={reorderRow}
            onRemove={(uid) => setRows((rs) => rs.filter((r) => r.uid !== uid))} />
        </ProCard>
        <ProCard colSpan={380} title={<Space>{t("formula.compliance", "实时合规")}{busy && <Spin size="small" />}</Space>}>
          <Form layout="inline" style={{ marginBottom: 12 }}>
            <Form.Item label={t("formula.flash", "闭杯闪点 ℃")}><InputNumber value={flash ?? undefined} onChange={(v) => setFlash(v ?? null)} /></Form.Item>
          </Form>
          {violations.length > 0 && <Alert type="error" showIcon style={{ marginBottom: 12 }} message={t("formula.redlines", "合规红线")}
            description={violations.map((v, i) => <div key={i}>{v.message}</div>)} />}
          <ErrorAlert error={clsError} />
          {cls ? <ComplianceSummary cls={cls} compact /> : <Empty description={t("formula.empty", "输入组分后自动评估")} />}
        </ProCard>
      </ProCard>
      <Drawer open={historyOpen} onClose={() => setHistoryOpen(false)} width={780} destroyOnHidden
        title={t("formulation.editor.history", "版本历史与对比")}
        extra={lineage && <Link to={`/lab/lineage/${lineage.lineage_id}`}>{t("formulation.ver.open_page", "在谱系页打开")}</Link>}>
        {lineage || name ? <VersionHistory lineageId={lineage?.lineage_id} formulation={filled.length ? current : undefined}
          onLineage={(l) => { setLineage(l); setName(current.name); }}
          onOpenVersion={(id) => { setHistoryOpen(false); void loadVersion(id); }} />
          : <Empty description={t("formulation.editor.no_lineage", "尚未打开或保存任何版本")} />}
      </Drawer>
      {lineage && <ProvenanceDrawer open={provOpen} onClose={() => setProvOpen(false)} nodeType="formulation" nodeId={lineage.parent} title={name} />}
      <LoadModal open={loadOpen} onClose={() => setLoadOpen(false)} onPick={(id) => void loadVersion(id)} />
      <Modal open={saveOpen} title={t("formula.save", "保存为新版本")} onCancel={() => setSaveOpen(false)} confirmLoading={save.isPending}
        onOk={async () => save.mutate(await form.validateFields())}>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label={t("formula.formula_name", "配方名称")} rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="change_summary" label={t("formula.change_summary", "修改说明（为空则自动生成）")}><Input.TextArea rows={3} /></Form.Item>
        </Form>
      </Modal>
    </PageContainer>
  );
}

/** Per-row detail panel: role dropdown + suggestion, cost / VOC / premix group, drag-sort. */
function DetailPanel({ rows, onPatch, onMove, onReorder, onRemove }: {
  rows: Row[]; onPatch: (uid: string, p: Partial<Row>) => void; onMove: (uid: string, dir: -1 | 1) => void;
  onReorder: (fromUid: string, toUid: string) => void; onRemove: (uid: string) => void;
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [dragOver, setDragOver] = useState<string | null>(null);
  const named = useMemo(() => rows.filter((r) => r.name.trim()), [rows]);
  const groups = useMemo(() => groupSubtotals(rows), [rows]);
  const groupNames = useMemo(() => [...new Set(rows.map((r) => (r.group || "").trim()).filter(Boolean))], [rows]);
  const suggest = (r: Row) => {
    const role = suggestRole(r);
    onPatch(r.uid, { role });
    message.success(t("formula.role_suggested", "已建议角色：{{role}}", { role: ROLE_LABELS[role] ?? role }));
  };
  return (
    <div style={{ marginTop: 16 }}>
      <Divider orientation="left" plain>{t("formula.detail_title", "组分明细（角色 / 成本 / VOC / 预混料组）")}</Divider>
      <Typography.Paragraph type="secondary">
        {t("formula.detail_help", "拖拽行可排序；成本与 VOC 为本机编辑辅助，保存在本机浏览器，不随版本快照上传服务器；VOC 合计为按质量加权的估算（假设各组分密度相近）。")}
      </Typography.Paragraph>
      <Table<Row> size="small" pagination={false} rowKey="uid" dataSource={named}
        rowClassName={(r) => (dragOver === r.uid ? "fx-drop-target" : "")}
        onRow={(r) => ({
          draggable: true,
          onDragStart: (e) => { e.dataTransfer.setData("text/plain", r.uid); e.dataTransfer.effectAllowed = "move"; },
          onDragOver: (e) => { e.preventDefault(); setDragOver(r.uid); },
          onDragLeave: () => setDragOver((d) => (d === r.uid ? null : d)),
          onDrop: (e) => {
            e.preventDefault();
            const from = e.dataTransfer.getData("text/plain");
            setDragOver(null);
            if (from && from !== r.uid) onReorder(from, r.uid);
          },
        })}
        columns={[
          { title: t("formula.col_order", "排序"), width: 110, render: (_d, r) => (
            <Space size={2}>
              <span title={t("formula.drag", "拖拽排序")} style={{ cursor: "grab" }}><DragOutlined /></span>
              <Button size="small" type="text" icon={<UpOutlined />} aria-label={t("formula.move_up", "上移")} onClick={() => onMove(r.uid, -1)} />
              <Button size="small" type="text" icon={<DownOutlined />} aria-label={t("formula.move_down", "下移")} onClick={() => onMove(r.uid, 1)} />
            </Space>
          ) },
          { title: t("formula.name", "原料"), width: 150, ellipsis: true, render: (_d, r) => r.zh_name || r.name },
          { title: t("formula.role", "角色"), width: 210, render: (_d, r) => (
            <Space.Compact>
              <Select style={{ width: 130 }} value={r.role || undefined} placeholder={t("formula.role_ph", "选择角色")}
                options={ROLES.map((x) => ({ value: x, label: `${ROLE_LABELS[x] ?? x} (${x})` }))}
                onChange={(v) => onPatch(r.uid, { role: v })} />
              <Button size="small" onClick={() => suggest(r)}>{t("formula.suggest", "建议")}</Button>
            </Space.Compact>
          ) },
          { title: t("formula.cost_per_kg", "成本 元/kg"), width: 130, render: (_d, r) => (
            <InputNumber size="small" min={0} style={{ width: "100%" }} value={r.cost_per_kg ?? undefined}
              placeholder="—" onChange={(v) => onPatch(r.uid, { cost_per_kg: v ?? undefined })} />
          ) },
          { title: t("formula.voc_per_l", "VOC g/L"), width: 120, render: (_d, r) => (
            <InputNumber size="small" min={0} style={{ width: "100%" }} value={r.voc_g_l ?? undefined}
              placeholder="—" onChange={(v) => onPatch(r.uid, { voc_g_l: v ?? undefined })} />
          ) },
          { title: t("formula.group", "预混料组"), width: 130, render: (_d, r) => (
            <Select size="small" style={{ width: "100%" }} mode="tags" maxCount={1} value={r.group ? [r.group] : []}
              placeholder={t("formula.group_ph", "输入组名")} options={groupNames.map((g) => ({ value: g, label: g }))}
              onChange={(v) => onPatch(r.uid, { group: v[0] || undefined })} />
          ) },
          { title: t("formula.cost_subtotal", "成本小计 元/kg配方"), width: 130, render: (_d, r) => rowCost(r).toFixed(2) },
          { title: t("formula.voc_contrib", "VOC 贡献 g/L"), width: 110, render: (_d, r) => rowVoc(r).toFixed(1) },
          { title: "", width: 40, render: (_d, r) => (
            <Button size="small" type="text" danger icon={<DeleteOutlined />} aria-label={t("common.delete", "删除")} onClick={() => onRemove(r.uid)} />
          ) },
        ]} />
      <Space style={{ marginTop: 12 }} wrap>
        <Statistic title={t("formula.total_cost", "配方成本")} value={totalCost(rows)} suffix={t("formula.cost_unit", "元/kg")} precision={2} />
        <Statistic title={t("formula.total_voc", "VOC 估算")} value={totalVoc(rows)} suffix="g/L" precision={1} />
      </Space>
      {groups.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <Typography.Text type="secondary">{t("formula.group_subtotals", "预混料组小计：")}</Typography.Text>
          <Space wrap style={{ marginLeft: 8 }}>
            {groups.map((g) => (
              <Tag key={g.group || "—"}>{g.group || t("formula.ungrouped", "未分组")} · {g.weight_pct.toFixed(1)}% · {g.cost.toFixed(2)} {t("formula.cost_unit", "元/kg")} · {g.voc.toFixed(1)} g/L</Tag>
            ))}
          </Space>
        </div>
      )}
    </div>
  );
}

function LoadModal({ open, onClose, onPick }: { open: boolean; onClose: () => void; onPick: (versionId: string) => void }) {
  const { t } = useTranslation();
  const [q, setQ] = useState("");
  const lineages = useQuery({
    queryKey: ["formulations", "lineages", q], enabled: open,
    queryFn: () => call<Lineage[]>("GET", `/api/formulations/versions?limit=50&name=${encodeURIComponent(q)}`),
  });
  return (
    <Modal open={open} onCancel={onClose} footer={null} width={720} title={t("formula.open", "打开版本")}>
      <Input.Search placeholder={t("formula.search", "按配方名称查找")} onSearch={setQ} allowClear style={{ marginBottom: 12 }} />
      <ErrorAlert error={lineages.error} />
      {(lineages.data ?? []).map((l) => (
        <ProCard key={l.lineage_id} size="small" bordered style={{ marginBottom: 8 }} title={l.versions[0]?.name}>
          <Space wrap>{l.versions.map((v) => <Button key={v.id} size="small" onClick={() => onPick(v.id)}>v{v.version} {v.change_summary?.slice(0, 24)}</Button>)}</Space>
        </ProCard>
      ))}
      {lineages.data && !lineages.data.length && <Empty />}
    </Modal>
  );
}

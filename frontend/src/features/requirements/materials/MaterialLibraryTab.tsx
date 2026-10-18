import { DownloadOutlined, PlusOutlined, ThunderboltOutlined, UploadOutlined } from "@ant-design/icons";
import { ProTable } from "@ant-design/pro-components";
import { Alert, App, Button, Dropdown, Input, Modal, Select, Space, Switch, Table, Tag, Tooltip, Typography, Upload } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call, download } from "@/api/client";
import { useUi } from "@/app/store";
import { ErrorAlert, errorText } from "@/shared/feedback";
import type { ProjectDetail } from "@/features/spec/types";
import { AVAIL_COLOR, availabilityLabel } from "../labels";
import { MaterialEditDrawer } from "./MaterialEditDrawer";
import { AVAILABILITY, ROLES, nextAvailability, type MaterialView } from "./materialForm";

interface ImportPreview {
  dry_run: boolean; batch_id: string; total: number; creates: number; updates: number; errors: number;
  rows: { name: string; action: string; reason?: string | null; matched_by?: string | null; existing_name?: string | null }[];
}
interface EnrichResult { enriched: number; scanned: number; remaining: number; available: boolean }

export const materialsKey = ["materials"];

function listUrl(q: string, role: string, avail: string, archived: boolean) {
  const p = new URLSearchParams({ limit: "1000" });
  if (q) p.set("q", q);
  if (role) p.set("role", role);
  if (avail) p.set("availability", avail);
  if (archived) p.set("include_archived", "true");
  return `/api/materials?${p}`;
}

/**
 * 材料列表 (legacy MaterialsPanel "library" tab): search / filter, create & edit, availability cycling, archive /
 * restore, import (preview → commit), export, template, catalogue enrichment, promotion from the project requirement.
 */
export function MaterialLibraryTab({ onChanged }: { onChanged?: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const projectId = useUi((s) => s.projectId);
  const [q, setQ] = useState("");
  const [role, setRole] = useState("");
  const [avail, setAvail] = useState("");
  const [archived, setArchived] = useState(false);
  const [editing, setEditing] = useState<MaterialView | null | undefined>(undefined); // undefined = closed, null = new
  const [importFile, setImportFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [enrichMsg, setEnrichMsg] = useState<string | null>(null);

  const res = useQuery({
    queryKey: [...materialsKey, "library", q, role, avail, archived],
    queryFn: () => call<{ total: number; materials: MaterialView[]; store_enabled: boolean }>("GET", listUrl(q, role, avail, archived)),
  });
  const refresh = () => { void qc.invalidateQueries({ queryKey: materialsKey }); onChanged?.(); };

  const setAvailability = useMutation({
    mutationFn: (m: MaterialView) => call("POST", "/api/materials/availability", { name: m.name, availability: nextAvailability(m.availability) }),
    onSuccess: refresh, onError: (e) => message.error(errorText(e)),
  });
  const archive = useMutation({
    mutationFn: ({ name, archived: a }: { name: string; archived: boolean }) => call("POST", "/api/materials/archive", { name, archived: a }),
    onSuccess: (_d, v) => { message.success(v.archived ? t("requirements.mat.archived", "已归档（可恢复）") : t("requirements.mat.restored", "已恢复")); refresh(); },
    onError: (e) => message.error(errorText(e)),
  });
  const runImport = useMutation({
    mutationFn: ({ file, dry }: { file: File; dry: boolean }) => {
      const fd = new FormData();
      fd.append("file", file);
      return call<ImportPreview>("POST", `/api/materials/import?dry_run=${dry ? "true" : "false"}`, fd);
    },
    onSuccess: (p) => {
      if (p.dry_run) { setPreview(p); return; }
      message.success(t("requirements.mat.imported", "已导入：新增 {{c}} · 更新 {{u}} · 错误 {{e}}", { c: p.creates, u: p.updates, e: p.errors }));
      setPreview(null); setImportFile(null); refresh();
    },
    onError: (e) => message.error(errorText(e)),
  });
  const enrich = useMutation({
    mutationFn: () => call<EnrichResult>("POST", "/api/materials/enrich?limit=20", {}),
    onSuccess: (r) => {
      setEnrichMsg(!r.available ? t("requirements.mat.enrich_unavailable", "属性补全不可用：后端未安装 pubchempy")
        : r.scanned === 0 ? t("requirements.mat.enrich_complete", "所有材料的 SMILES / 分子量已齐全")
        : r.remaining > 0 ? t("requirements.mat.enrich_more", "本次查询 {{s}} 条，更新 {{e}} 条；还有 {{r}} 条待补全，可再点一次", { s: r.scanned, e: r.enriched, r: r.remaining })
        : t("requirements.mat.enrich_done", "本次查询 {{s}} 条，更新 {{e}} 条", { s: r.scanned, e: r.enriched }));
      refresh();
    },
    onError: (e) => message.error(errorText(e)),
  });
  const promote = useMutation({
    mutationFn: async () => {
      const p = await call<ProjectDetail>("GET", `/api/projects/${encodeURIComponent(projectId!)}`);
      if (!p.workspace.requirement) throw new Error(t("requirements.mat.no_requirement", "当前项目尚无需求"));
      return call<Record<string, unknown>>("POST", "/api/materials/promote-from-requirement", { requirement: p.workspace.requirement });
    },
    onSuccess: (r) => {
      message.success(t("requirements.mat.promoted", "需求材料已提升：入库 {{u}} · 待入库 {{p}} · 跳过 {{s}}", { u: Number(r.upserted ?? 0), p: Number(r.pending ?? 0), s: Number(r.skipped ?? 0) }));
      refresh();
    },
    onError: (e) => message.error(errorText(e)),
  });
  const exportAs = (fmt: "csv" | "json" | "xlsx") => {
    const p = new URLSearchParams({ format: fmt });
    if (q) p.set("q", q);
    if (role) p.set("role", role);
    if (avail) p.set("availability", avail);
    if (archived) p.set("include_archived", "true");
    download(`/api/materials/export?${p}`, `materials.${fmt}`).catch((e) => message.error(errorText(e)));
  };
  const s = (m: MaterialView, k: string) => { const v = m.spec?.[k]; return v === undefined || v === null || v === "" ? "—" : String(v); };

  return (
    <>
      {res.data && res.data.store_enabled === false && (
        <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={t("requirements.mat.store_disabled", "材料空间未启用：只能浏览内置种子材料，编辑 / 导入 / 归档不可用。")} />
      )}
      {enrichMsg && <Alert type="info" showIcon closable onClose={() => setEnrichMsg(null)} style={{ marginBottom: 12 }} message={enrichMsg} />}
      <ErrorAlert error={res.error} />
      <ProTable<MaterialView>
        rowKey="name" search={false} loading={res.isLoading} dataSource={res.data?.materials} options={{ reload: () => void res.refetch() }}
        pagination={{ pageSize: 50 }} scroll={{ x: 1200 }}
        headerTitle={<Space wrap>
          <Input.Search allowClear enterButton placeholder={t("requirements.mat.search", "名称 / CAS / 中文名")} onSearch={setQ} style={{ width: 280 }} />
          <Select allowClear placeholder={t("requirements.mat.all_roles", "全部角色")} style={{ width: 140 }} value={role || undefined} onChange={(v) => setRole(v ?? "")}
            options={ROLES.map((r) => ({ value: r, label: r }))} />
          <Select allowClear placeholder={t("requirements.mat.all_status", "全部状态")} style={{ width: 120 }} value={avail || undefined} onChange={(v) => setAvail(v ?? "")}
            options={AVAILABILITY.map((a) => ({ value: a, label: availabilityLabel(t, a) }))} />
          <Space size={4}><Switch size="small" checked={archived} onChange={setArchived} />{t("requirements.mat.show_archived", "含已归档")}</Space>
        </Space>}
        toolBarRender={() => [
          <Button key="new" type="primary" icon={<PlusOutlined />} onClick={() => setEditing(null)}>{t("requirements.mat.new", "新材料")}</Button>,
          <Upload key="imp" accept=".json,.csv,.xlsx,.xls" showUploadList={false}
            beforeUpload={(f) => { setImportFile(f); runImport.mutate({ file: f, dry: true }); return false; }}>
            <Button icon={<UploadOutlined />} loading={runImport.isPending && !!runImport.variables?.dry}>{t("requirements.mat.import", "导入")}</Button>
          </Upload>,
          <Dropdown key="exp" menu={{
            items: [
              { key: "csv", label: t("requirements.mat.export_csv", "导出 CSV") },
              { key: "json", label: t("requirements.mat.export_json", "导出 JSON") },
              { key: "xlsx", label: t("requirements.mat.export_xlsx", "导出 Excel") },
              { type: "divider" },
              { key: "tpl", label: t("requirements.mat.template", "下载 CSV 导入模板") },
            ],
            onClick: ({ key }) => key === "tpl"
              ? download("/api/materials/import-template?format=csv", "materials_template.csv").catch((e) => message.error(errorText(e)))
              : exportAs(key as "csv" | "json" | "xlsx"),
          }}>
            <Button icon={<DownloadOutlined />}>{t("requirements.mat.export", "导出")}</Button>
          </Dropdown>,
          <Tooltip key="prom" title={projectId ? t("requirements.mat.promote_tip", "把当前项目需求中的材料提升到材料库 / 待入库") : t("requirements.mat.promote_need", "请先在顶部选择项目")}>
            <Button disabled={!projectId} loading={promote.isPending} onClick={() => promote.mutate()}>{t("requirements.mat.promote", "从需求提升")}</Button>
          </Tooltip>,
          <Tooltip key="enr" title={t("requirements.mat.enrich_tip", "为缺失 SMILES / 摩尔质量的材料批量补全属性（PubChem，每次 20 条）")}>
            <Button icon={<ThunderboltOutlined />} loading={enrich.isPending} onClick={() => enrich.mutate()}>{t("requirements.mat.enrich", "批量补全属性")}</Button>
          </Tooltip>,
        ]}
        columns={[
          { title: t("requirements.mat.name", "名称"), dataIndex: "name", fixed: "left", width: 200,
            render: (_d, m) => <Space size={4}>{m.name}{!!m.spec?.smiles && <Tag bordered={false}>SMILES</Tag>}{m.archived && <Tag>{t("requirements.mat.archived_tag", "已归档")}</Tag>}</Space> },
          { title: t("requirements.mat.origin", "来源"), dataIndex: "origin", width: 90, render: (_d, m) => <Typography.Text type="secondary">{m.origin || "seed"}</Typography.Text> },
          { title: t("requirements.mat.zh_name", "中文名"), width: 140, render: (_d, m) => s(m, "zh_name") },
          { title: t("requirements.mat.role", "角色"), dataIndex: "role", width: 110, render: (_d, m) => <Tag>{m.role || "—"}</Tag> },
          { title: "CAS", width: 120, render: (_d, m) => s(m, "cas_no") },
          { title: t("requirements.mat.formula", "分子式"), width: 120, render: (_d, m) => s(m, "formula") },
          { title: "M (g/mol)", width: 100, render: (_d, m) => s(m, "molar_mass") },
          { title: t("requirements.mat.price", "价格 ¥/kg"), width: 100, render: (_d, m) => (m.spec?.price_cny_per_kg != null ? `¥${String(m.spec.price_cny_per_kg)}` : "—") },
          { title: t("requirements.mat.status", "状态"), dataIndex: "availability", width: 100,
            render: (_d, m) => (
              <Tooltip title={t("requirements.mat.cycle_tip", "点击切换 在库 → 受限 → 停产（驱动替代推荐）")}>
                <Tag color={AVAIL_COLOR[m.availability ?? "in_stock"]} style={{ cursor: "pointer" }}
                  onClick={() => !setAvailability.isPending && setAvailability.mutate(m)}>{availabilityLabel(t, m.availability)}</Tag>
              </Tooltip>
            ) },
          { title: t("common.actions", "操作"), valueType: "option", width: 130, fixed: "right",
            render: (_d, m) => [
              <a key="e" onClick={() => setEditing(m)}>{t("common.edit", "编辑")}</a>,
              m.archived
                ? <a key="r" onClick={() => archive.mutate({ name: m.name, archived: false })}>{t("requirements.mat.restore", "恢复")}</a>
                : <a key="a" onClick={() => archive.mutate({ name: m.name, archived: true })}>{t("requirements.mat.archive", "归档")}</a>,
            ] },
        ]}
        locale={{ emptyText: t("requirements.mat.empty", "无匹配材料 — 点「新材料」录入，或在「结构搜索」中按结构发现候选") }}
      />
      <Typography.Text type="secondary">{t("requirements.mat.footer", "共 {{n}} 条 · 材料状态会驱动配方替代推荐（替代时优先在库）。", { n: res.data?.total ?? 0 })}</Typography.Text>
      <MaterialEditDrawer open={editing !== undefined} material={editing ?? null} onClose={() => setEditing(undefined)} onSaved={refresh} />
      <Modal open={!!preview} width={720} title={t("requirements.mat.preview_title", "导入预览 · {{f}}", { f: importFile?.name ?? "" })}
        onCancel={() => { setPreview(null); setImportFile(null); }}
        okText={t("requirements.mat.commit", "确认导入")} okButtonProps={{ disabled: !importFile || !preview || preview.creates + preview.updates === 0 }}
        confirmLoading={runImport.isPending} onOk={() => importFile && runImport.mutate({ file: importFile, dry: false })} destroyOnHidden>
        {preview && (
          <>
            <Typography.Paragraph>
              {t("requirements.mat.preview_sum", "批次 {{b}} · 共 {{n}} 行：新增 {{c}} · 更新 {{u}} · 错误 {{e}}", { b: preview.batch_id.slice(0, 8), n: preview.total, c: preview.creates, u: preview.updates, e: preview.errors })}
            </Typography.Paragraph>
            <Table size="small" rowKey="_i" dataSource={preview.rows.map((r, i) => ({ ...r, _i: i }))} pagination={{ pageSize: 10 }}
              columns={[
                { title: t("requirements.mat.action", "动作"), dataIndex: "action", width: 90, render: (a: string) => <Tag color={a === "error" ? "error" : a === "create" ? "success" : "processing"}>{a}</Tag> },
                { title: t("requirements.mat.name", "名称"), dataIndex: "name", render: (n: string) => n || "—" },
                { title: t("requirements.mat.matched", "匹配"), dataIndex: "matched_by", render: (_v, r) => [r.matched_by, r.existing_name].filter(Boolean).join(" → ") || "—" },
                { title: t("requirements.mat.reason", "说明"), dataIndex: "reason", render: (v?: string | null) => v || "—" },
              ]} />
          </>
        )}
      </Modal>
    </>
  );
}

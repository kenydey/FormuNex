import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Alert, App, Button, Checkbox, Dropdown, Empty, Form, Input, InputNumber, List, Modal, Result, Select, Space, Spin, Tag, Typography, theme } from "antd";
import { DownOutlined, PlusOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useSession } from "@/app/session";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { SCREENING_COLOR, splitList } from "./logic";
import { WithProject, getTextDownload } from "./shared";
import type { DupGroup, ImportResult, Library, LibraryItem } from "./types";

const FLAG_ATTR = "literature_library_enabled";
const SCREENINGS = ["match", "no_match", "uncertain", "unset"];

interface Draft { title: string; doi: string; year: number | null; authors: string; tags: string; notes: string; screening: string; collection_ids: string[] }

function toDraft(it: LibraryItem): Draft {
  return {
    title: it.title || "", doi: it.doi || "", year: it.year ?? null, authors: (it.authors || []).join("; "), tags: (it.tags || []).join(", "),
    notes: it.notes || "", screening: it.screening || "unset", collection_ids: [...(it.collection_ids || [])],
  };
}

function LibraryBody({ projectId }: { projectId: string }) {
  const { t } = useTranslation();
  const { message, modal } = App.useApp();
  const { can } = useSession();
  const qc = useQueryClient();
  const [filters, setFilters] = useState({ q: "", tag: "", collection_id: "", screening: "" });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [importText, setImportText] = useState("");
  const [dups, setDups] = useState<DupGroup[]>([]);
  const [scope, setScope] = useState<"library" | "frozen" | "collection">("library");
  const [nameDialog, setNameDialog] = useState<{ mode: "create" | "rename"; value: string } | null>(null);
  const [form] = Form.useForm<Draft>();
  const { token } = theme.useToken();

  const key = ["literature", "library", projectId, filters] as const;
  const lib = useQuery({
    queryKey: key,
    queryFn: () => {
      const qs = new URLSearchParams();
      for (const [k, v] of Object.entries(filters)) if (v) qs.set(k, v);
      return call<Library>("GET", `/api/wiki/literature/${encodeURIComponent(projectId)}/library${qs.size ? `?${qs}` : ""}`);
    },
  });
  const items = lib.data?.items ?? [];
  const collections = lib.data?.collections ?? [];
  const frozenIds = new Set(lib.data?.frozen?.item_ids ?? []);
  const selected = items.find((i) => i.id === selectedId) ?? items[0] ?? null;
  useEffect(() => { if (selected) form.setFieldsValue(toDraft(selected)); }, [selected, form]);

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["literature", "library", projectId] });
    void qc.invalidateQueries({ queryKey: ["literature", "manifest", projectId] });
  };
  const run = useMutation({
    mutationFn: async ({ fn }: { label: string; fn: () => Promise<unknown> }) => fn(),
    onSuccess: (r, v) => { message.success(typeof r === "string" ? r : v.label); refresh(); },
    onError: (e) => message.error(errorText(e)),
  });
  const act = (label: string, fn: () => Promise<unknown>) => run.mutate({ label, fn });
  const importMsg = (r: ImportResult) => t("library.lib.import_result", "新增 {{a}} / 跳过 {{s}} / 失败 {{f}}", { a: r.added.length, s: r.skipped.length, f: r.failures.length });
  const exportFile = (fmt: "bib" | "ris") => act(t("library.lib.exported", "已导出"), () => {
    const qs = new URLSearchParams({ scope });
    if (scope === "collection" && filters.collection_id) qs.set("collection_id", filters.collection_id);
    return getTextDownload(`/api/wiki/literature/${encodeURIComponent(projectId)}/export.${fmt}?${qs}`, `${projectId}-library.${fmt}`,
      fmt === "bib" ? "application/x-bibtex" : "application/x-research-info-systems");
  });
  const currentCollection = collections.find((c) => c.id === filters.collection_id);

  const save = (d: Draft) => act(t("common.saved", "已保存"), () => call("PATCH", `/api/wiki/literature/items/${encodeURIComponent(selected!.id)}`, {
    project_id: projectId, title: d.title, doi: d.doi || null, year: d.year ?? null, authors: splitList(d.authors, ";"), tags: splitList(d.tags),
    notes: d.notes, screening: d.screening, collection_ids: d.collection_ids,
  }));

  return (
    <>
      <Space wrap style={{ marginBottom: 12 }}>
        <Button loading={run.isPending} onClick={() => act(t("library.manifest.captured", "已捕获"), () => call("POST", "/api/wiki/literature/capture", { project_id: projectId }))}>
          {t("library.manifest.capture", "捕获 Capture")}</Button>
        <Button loading={run.isPending} onClick={() => act(t("library.manifest.frozen_ok", "已冻结"), () => call("POST", "/api/wiki/literature/freeze", { project_id: projectId, actor: "hub-library" }))}>
          {t("library.manifest.freeze", "冻结 Freeze")}</Button>
        <Button loading={run.isPending} onClick={() => act(t("library.manifest.enriched", "OA 补全文完成"),
          () => call("POST", "/api/wiki/literature/enrich-oa", { project_id: projectId, scope: "missing_fulltext", limit: 20 }))}>
          {t("library.manifest.enrich", "补全文")}</Button>
        <Space.Compact>
          <Select value={scope} onChange={setScope} style={{ width: 150 }} options={[
            { value: "library", label: t("library.lib.scope_all", "导出：全部库") },
            { value: "frozen", label: t("library.lib.scope_frozen", "导出：仅冻结") },
            { value: "collection", label: t("library.lib.scope_collection", "导出：当前集合") },
          ]} />
          <Dropdown disabled={scope === "collection" && !filters.collection_id} menu={{ items: [{ key: "bib", label: "BibTeX" }, { key: "ris", label: "RIS" }],
            onClick: ({ key: k }) => exportFile(k as "bib" | "ris") }}>
            <Button>{t("library.lib.export", "导出")} <DownOutlined /></Button>
          </Dropdown>
        </Space.Compact>
        <Button loading={run.isPending} onClick={() => act(t("library.lib.dup_done", "查重完成"), async () => {
          const d = await call<{ groups: DupGroup[] }>("GET", `/api/wiki/literature/${encodeURIComponent(projectId)}/duplicates`);
          setDups(d.groups ?? []);
          return t("library.lib.dup_found", "重复组 {{n}}", { n: d.groups?.length ?? 0 });
        })}>{t("library.lib.dedupe", "查重")}</Button>
        <Typography.Text type="secondary">{t("library.lib.count", "{{n}} 条", { n: items.length })}</Typography.Text>
      </Space>

      <ProCard split="vertical" bordered style={{ marginBottom: 12 }}>
        <ProCard colSpan="60%">
          <Input.TextArea rows={3} value={importText} onChange={(e) => setImportText(e.target.value)}
            placeholder={t("library.lib.import_ph", "粘贴 DOI / ChemRxiv 链接或 chemrxiv:<uuid>（不支持 arXiv），或 BibTeX / RIS 文本")} />
        </ProCard>
        <ProCard>
          <Space wrap>
            <Button type="primary" disabled={!importText.trim() || !can("knowledge.write")} loading={run.isPending} onClick={() => act("", async () => {
              const r = await call<ImportResult>("POST", "/api/wiki/literature/import-ids", { project_id: projectId, text: importText });
              setImportText("");
              return importMsg(r);
            })}>{t("library.lib.import_ids", "导入 DOI / ChemRxiv")}</Button>
            {(["bibtex", "ris"] as const).map((fmt) => (
              <Button key={fmt} disabled={!importText.trim() || !can("knowledge.write")} loading={run.isPending} onClick={() => act("", async () => {
                const r = await call<ImportResult>("POST", "/api/wiki/literature/import", { project_id: projectId, format: fmt, text: importText });
                setImportText("");
                return importMsg(r);
              })}>{fmt === "bibtex" ? t("library.lib.import_bib", "导入为 BibTeX") : t("library.lib.import_ris", "导入为 RIS")}</Button>
            ))}
          </Space>
        </ProCard>
      </ProCard>

      {dups.length > 0 && (
        <List size="small" bordered style={{ marginBottom: 12 }} header={t("library.lib.dup_found", "重复组 {{n}}", { n: dups.length })} dataSource={dups}
          renderItem={(g) => (
            <List.Item key={g.id} actions={[
              <Button key="m" size="small" onClick={() => act(t("library.lib.merged", "已合并"), async () => {
                await call("POST", "/api/wiki/literature/merge", { project_id: projectId, item_ids: g.item_ids });
                setDups((p) => p.filter((x) => x.id !== g.id));
              })}>{t("library.lib.merge", "合并")}</Button>,
            ]}>
              <Space><span>{g.title}</span><Tag>{g.match}</Tag><Typography.Text type="secondary">×{g.item_ids.length}</Typography.Text></Space>
            </List.Item>
          )} />
      )}

      <Space wrap style={{ marginBottom: 12 }}>
        <Input.Search allowClear placeholder={t("library.lib.search", "搜索标题 / DOI / 标签…")} style={{ width: 260 }}
          onSearch={(v) => setFilters((f) => ({ ...f, q: v }))} />
        <Input.Search allowClear placeholder={t("library.lib.tag", "标签")} style={{ width: 140 }} onSearch={(v) => setFilters((f) => ({ ...f, tag: v }))} />
        <Select allowClear placeholder={t("library.lib.all_collections", "全部集合")} style={{ width: 180 }} value={filters.collection_id || undefined}
          onChange={(v) => setFilters((f) => ({ ...f, collection_id: v ?? "" }))} options={collections.map((c) => ({ value: c.id, label: c.name }))} />
        <Select allowClear placeholder={t("library.lib.screening", "筛选态")} style={{ width: 130 }} value={filters.screening || undefined}
          onChange={(v) => setFilters((f) => ({ ...f, screening: v ?? "" }))} options={SCREENINGS.map((s) => ({ value: s, label: s }))} />
        <Button icon={<PlusOutlined />} onClick={() => setNameDialog({ mode: "create", value: "" })}>{t("library.lib.new_collection", "集合")}</Button>
        {currentCollection && (
          <>
            <Button onClick={() => setNameDialog({ mode: "rename", value: currentCollection.name })}>{t("library.lib.rename", "重命名")}</Button>
            <Button danger onClick={() => modal.confirm({
              title: t("library.lib.delete_collection_q", "删除集合「{{n}}」？其中的文献不会被删除。", { n: currentCollection.name }),
              okButtonProps: { danger: true },
              onOk: () => act(t("library.lib.collection_deleted", "集合已删除"), async () => {
                await call("DELETE", `/api/wiki/literature/collections/${encodeURIComponent(currentCollection.id)}`, { project_id: projectId });
                setFilters((f) => ({ ...f, collection_id: "" }));
              }),
            })}>{t("library.lib.delete_collection", "删除集合")}</Button>
          </>
        )}
      </Space>

      <ErrorAlert error={lib.error} />
      <ProCard split="vertical" bordered>
        <ProCard colSpan="45%" style={{ maxHeight: 640, overflow: "auto" }}>
          <List loading={lib.isLoading} dataSource={items}
            locale={{ emptyText: <Empty description={t("library.lib.empty", "暂无条目 — 先 Capture 或导入 DOI / ChemRxiv")} /> }}
            renderItem={(it) => (
              <List.Item key={it.id} onClick={() => setSelectedId(it.id)}
                style={{ cursor: "pointer", paddingInline: 12, background: selected?.id === it.id ? token.controlItemBgActive : undefined }}>
                <List.Item.Meta title={<Space size={4}>{it.title || it.id}{frozenIds.has(it.id) && <Tag color="cyan">{t("library.lib.frozen", "已冻结")}</Tag>}</Space>}
                  description={<Space size={4} wrap>{it.year && <span>{it.year}</span>}{(it.doi || it.chemrxiv_id) && <span>{it.doi || it.chemrxiv_id}</span>}
                    <Tag color={SCREENING_COLOR[it.screening || "unset"]}>{it.screening || "unset"}</Tag>{it.has_fulltext && <Tag color="blue">{t("library.manifest.fulltext", "全文")}</Tag>}</Space>} />
              </List.Item>
            )} />
        </ProCard>
        <ProCard title={t("library.lib.edit", "编辑元数据")}>
          {!selected ? <Empty description={t("library.lib.pick", "选择左侧条目编辑元数据")} /> : (
            <Form form={form} layout="vertical" onFinish={save}>
              <Form.Item name="title" label={t("library.lib.f_title", "标题")}><Input /></Form.Item>
              <Space style={{ display: "flex" }} align="start">
                <Form.Item name="doi" label="DOI" style={{ flex: 1 }}><Input /></Form.Item>
                <Form.Item name="year" label={t("library.lib.f_year", "年份")}><InputNumber min={1800} max={2100} /></Form.Item>
                <Form.Item name="screening" label={t("library.lib.screening", "筛选态")}><Select style={{ width: 130 }} options={SCREENINGS.map((s) => ({ value: s, label: s }))} /></Form.Item>
              </Space>
              <Form.Item name="authors" label={t("library.lib.f_authors", "作者（; 分隔）")}><Input /></Form.Item>
              <Form.Item name="tags" label={t("library.lib.f_tags", "标签（, 分隔）")}><Input /></Form.Item>
              <Form.Item name="notes" label={t("library.lib.f_notes", "笔记")}><Input.TextArea rows={3} /></Form.Item>
              <Form.Item name="collection_ids" label={t("library.lib.f_collections", "所属集合")}>
                {collections.length ? <Checkbox.Group options={collections.map((c) => ({ value: c.id, label: c.name }))} />
                  : <Typography.Text type="secondary">{t("library.lib.no_collections", "暂无集合 — 先点「+ 集合」")}</Typography.Text>}
              </Form.Item>
              <Button type="primary" htmlType="submit" loading={run.isPending}>{t("common.save", "保存")}</Button>
            </Form>
          )}
        </ProCard>
      </ProCard>

      <Modal open={!!nameDialog} destroyOnHidden title={nameDialog?.mode === "rename" ? t("library.lib.rename", "重命名") : t("library.lib.new_collection_title", "新建集合")}
        onCancel={() => setNameDialog(null)} okButtonProps={{ disabled: !nameDialog?.value.trim() }}
        onOk={() => {
          const name = nameDialog!.value.trim();
          if (nameDialog!.mode === "create") act(t("library.lib.collection_created", "集合已创建"), () => call("POST", "/api/wiki/literature/collections", { project_id: projectId, name }));
          else if (currentCollection && name !== currentCollection.name) act(t("library.lib.collection_renamed", "集合已重命名"),
            () => call("PATCH", `/api/wiki/literature/collections/${encodeURIComponent(currentCollection.id)}`, { project_id: projectId, name }));
          setNameDialog(null);
        }}>
        <Input autoFocus value={nameDialog?.value} placeholder={t("library.lib.collection_name", "集合名称")}
          onChange={(e) => setNameDialog((d) => (d ? { ...d, value: e.target.value } : d))} />
      </Modal>
    </>
  );
}

/** Literature library (Wave E-Lit): catalog, DOI / ChemRxiv / BibTeX / RIS import, dedupe + merge, collections, BibTeX / RIS export. */
export default function LiteratureLibraryPage() {
  const { t } = useTranslation();
  const flags = useQuery({ queryKey: ["settings", "env-flags"], queryFn: () => call<{ flags: { attr: string; value: boolean }[] }>("GET", "/api/settings/env-flags") });
  const on = flags.data?.flags?.find((f) => f.attr === FLAG_ATTR)?.value;
  return (
    <PageContainer subTitle={t("library.lib.sub", "编目 · ChemRxiv / DOI · BibTeX / RIS · 查重")}>
      <WithProject>{(projectId) => (
        flags.isLoading ? <Spin /> : on ? <LibraryBody key={projectId} projectId={projectId} /> : (
          <Result status="info" title={t("library.lib.disabled", "文献库未启用")}
            subTitle={<>{t("library.lib.disabled_hint", "在系统设置的环境变量中开启以下开关后可编目、导入 DOI / ChemRxiv、查重与导出 BibTeX / RIS：")} <Typography.Text code>{FLAG_ATTR}</Typography.Text></>}
            extra={flags.error ? <Alert type="error" message={errorText(flags.error)} /> : undefined} />
        )
      )}</WithProject>
    </PageContainer>
  );
}

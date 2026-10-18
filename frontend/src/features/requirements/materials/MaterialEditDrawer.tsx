import { SearchOutlined } from "@ant-design/icons";
import { App, AutoComplete, Button, Col, Drawer, Form, Input, InputNumber, Row, Space, Tooltip, Typography } from "antd";
import { WarningOutlined } from "@ant-design/icons";
import { useMutation } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { EditableRows } from "@/shared/EditableRows";
import { SmilesDrawing } from "@/shared/Markdown";
import { StructureEditorModal } from "@/shared/StructureEditor";
import { errorText } from "@/shared/feedback";
import {
  EMPTY_DRAFT, ROLES, applyLookup, draftFromView, lookupQuery, lookupSource, payloadFromDraft,
  type ChemicalLookup, type MaterialDraft, type MaterialView, type Supplier,
} from "./materialForm";

/** 录入 / 编辑材料 (legacy MaterialsPanel edit modal): spec fields, chemical lookup auto-fill, supplier rows. */
export function MaterialEditDrawer({ open, material, onClose, onSaved }: {
  open: boolean;
  /** null = create a new material. */
  material: MaterialView | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [d, setD] = useState<MaterialDraft>(EMPTY_DRAFT);
  const [lookupMsg, setLookupMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  useEffect(() => {
    if (open) { setD(material ? draftFromView(material) : EMPTY_DRAFT); setLookupMsg(null); }
  }, [open, material]);
  const set = (patch: Partial<MaterialDraft>) => setD((x) => ({ ...x, ...patch }));

  const lookup = useMutation({
    mutationFn: (q: string) => call<ChemicalLookup>("GET", `/api/chemical/lookup?q=${encodeURIComponent(q)}`),
    onSuccess: (r) => {
      if (!r.found) { setLookupMsg({ ok: false, text: t("requirements.mat.lookup_none", "未找到：{{q}}", { q: r.query }) }); return; }
      setD((x) => applyLookup(x, r));
      const src = lookupSource(r);
      setLookupMsg({ ok: true, text: src.kind === "surechembl"
        ? t("requirements.mat.lookup_surechembl", "已自动填充 · 来源 专利化学库 (SureChEMBL) {{id}} · 公开专利标注，仅供研发参考", { id: [src.id, src.freq != null ? `×${src.freq}` : null].filter(Boolean).join(" ") })
        : src.kind === "catalog" ? t("requirements.mat.lookup_catalog", "已自动填充 · 来源 材料目录")
        : t("requirements.mat.lookup_from", "已自动填充 · 来源 {{s}}", { s: src.label }) });
    },
    onError: (e) => setLookupMsg({ ok: false, text: errorText(e) }),
  });
  const save = useMutation({
    mutationFn: () => call<MaterialView>("POST", "/api/materials", payloadFromDraft(d)),
    onSuccess: () => { message.success(t("common.saved", "已保存")); onSaved(); onClose(); },
    onError: (e) => message.error(errorText(e)),
  });
  const runLookup = () => {
    const q = lookupQuery(d);
    if (!q) { setLookupMsg({ ok: false, text: t("requirements.mat.lookup_need", "请先填写名称、CAS 或 SMILES") }); return; }
    setLookupMsg(null);
    lookup.mutate(q);
  };
  const numField = (k: "molar_mass" | "price_cny_per_kg" | "voc_contrib" | "density_gcm3", label: string) => (
    <Col span={12}>
      <Form.Item label={label}><InputNumber min={0} style={{ width: "100%" }} value={d[k] ?? null} onChange={(v) => set({ [k]: v })} /></Form.Item>
    </Col>
  );
  const valid = !!d.name.trim() && !!d.role.trim();

  return (
    <Drawer open={open} onClose={onClose} width={860} destroyOnHidden
      title={material ? t("requirements.mat.edit_title", "编辑材料：{{n}}", { n: material.name }) : t("requirements.mat.new_title", "录入新材料")}
      extra={<Space>
        <Tooltip title={t("requirements.mat.lookup_tip", "用名称 / CAS / SMILES 查询（材料目录 → PubChem → SureChEMBL）并自动填充")}>
          <Button icon={<SearchOutlined />} loading={lookup.isPending} onClick={runLookup}>{t("requirements.mat.lookup", "化学查询填充")}</Button>
        </Tooltip>
        <Button onClick={onClose}>{t("requirements.cancel", "取消")}</Button>
        <Button type="primary" disabled={!valid} loading={save.isPending} onClick={() => save.mutate()}>{t("common.save", "保存")}</Button>
      </Space>}>
      {lookupMsg && <Typography.Paragraph type={lookupMsg.ok ? "success" : "warning"}>{lookupMsg.text}</Typography.Paragraph>}
      <Form layout="vertical">
        <Row gutter={12}>
          <Col span={12}>
            <Form.Item label={t("requirements.mat.name", "名称")} required>
              <Input value={d.name} disabled={!!material} onChange={(e) => set({ name: e.target.value })} />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item label={t("requirements.mat.role", "角色")} required>
              <AutoComplete value={d.role} onChange={(v) => set({ role: v })} options={ROLES.map((r) => ({ value: r }))} placeholder="resin / additive / inhibitor…" />
            </Form.Item>
          </Col>
          <Col span={12}><Form.Item label={t("requirements.mat.zh_name", "中文名")}><Input value={d.zh_name ?? ""} onChange={(e) => set({ zh_name: e.target.value })} /></Form.Item></Col>
          <Col span={12}><Form.Item label="CAS No."><Input value={d.cas_no ?? ""} onChange={(e) => set({ cas_no: e.target.value })} /></Form.Item></Col>
          <Col span={12}><Form.Item label={t("requirements.mat.formula", "分子式")}><Input value={d.formula ?? ""} onChange={(e) => set({ formula: e.target.value })} /></Form.Item></Col>
          <Col span={12}><Form.Item label="SMILES">
            <Space.Compact style={{ width: "100%" }}>
              <Input value={d.smiles ?? ""} onChange={(e) => set({ smiles: e.target.value })} />
              <Button onClick={() => setEditorOpen(true)}>{t("requirements.mat.draw", "绘制…")}</Button>
            </Space.Compact>
          </Form.Item></Col>
          <StructureEditorModal
            open={editorOpen}
            initialSmiles={d.smiles ?? undefined}
            title={t("requirements.mat.draw_title", "绘制分子结构")}
            actions={[{ key: "use", label: t("requirements.mat.use_structure", "使用此结构"), primary: true }]}
            onApply={(s) => { set({ smiles: s.smiles }); setEditorOpen(false); }}
            onClose={() => setEditorOpen(false)}
          />
          {numField("molar_mass", t("requirements.mat.molar_mass", "摩尔质量 g/mol"))}
          {numField("price_cny_per_kg", t("requirements.mat.price", "价格 ¥/kg"))}
          {numField("voc_contrib", t("requirements.mat.voc", "VOC 贡献 g/L"))}
          {numField("density_gcm3", t("requirements.mat.density", "密度 g/cm³"))}
        </Row>
        {d.smiles?.trim() && <Form.Item label={t("requirements.mat.structure", "结构")}><SmilesDrawing smiles={d.smiles.trim()} /></Form.Item>}
      </Form>
      <Typography.Title level={5}>{t("requirements.mat.suppliers", "供应商（{{n}} 家）", { n: d.suppliers_json.length })}</Typography.Title>
      <Typography.Paragraph type="secondary">{t("requirements.mat.suppliers_help", "价格 / 交期 / MOQ 仅手工录入；保存时同步写入归一化供应商表。")}</Typography.Paragraph>
      <EditableRows<Supplier>
        value={d.suppliers_json} onChange={(rows) => set({ suppliers_json: rows })}
        newRow={() => ({ name: "", currency: "CNY", price_source: "manual" })}
        addLabel={t("requirements.mat.add_supplier", "添加供应商")}
        columns={[
          { title: t("requirements.mat.s_name", "供应商"), dataIndex: "name", width: 140, formItemProps: { rules: [{ required: true }] },
            render: (_d, r) => <Space size={4}>{r.name}{r.stale_price && <Tooltip title={t("requirements.mat.stale", "价格偏旧或缺少观测日，请核对后更新观测日")}><WarningOutlined /></Tooltip>}</Space> },
          { title: t("requirements.mat.s_url", "官网"), dataIndex: "url", width: 140 },
          { title: t("requirements.mat.s_product", "产品页"), dataIndex: "product_url", width: 140 },
          { title: t("requirements.mat.s_country", "国家/地区"), dataIndex: "country", width: 90 },
          { title: t("requirements.mat.s_price", "价格 CNY/kg"), dataIndex: "price_cny_per_kg", valueType: "digit", width: 100 },
          { title: t("requirements.mat.s_observed", "观测日"), dataIndex: "price_observed_at", width: 120, fieldProps: { placeholder: "YYYY-MM-DD" } },
          { title: t("requirements.mat.s_lead", "交期(天)"), dataIndex: "lead_time_days", valueType: "digit", width: 80 },
          { title: "MOQ", dataIndex: "moq", width: 80 },
          { title: t("requirements.mat.s_pack", "包装规格"), dataIndex: "pack_size", width: 90 },
        ]} />
    </Drawer>
  );
}

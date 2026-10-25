import { Alert, App, Button, Checkbox, Divider, Form, Input, InputNumber, Modal, Select, Space, Tag, Typography } from "antd";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { EditableRows } from "@/shared/EditableRows";
import { errorText } from "@/shared/feedback";
import type { DocSummary, ProductDetail, Revision } from "./types";
import { loadLocks } from "./fieldLocks";

interface Candidates { sds_properties: Record<string, { value: number; method: string; source: string }>; notes?: string[] }
interface Secret { cas: string; generic: string }
interface TdsProp { key: string; label?: string; value: number | null; unit?: string; method?: string }
interface TsIndicator { key: string; label?: string; unit?: string; spec_min?: number | null; spec_max?: number | null; spec_target?: number | null; test_method?: string; is_factory_inspection?: boolean }

const LABEL_SIZES: [number, number][] = [[50, 75], [75, 100], [100, 150], [150, 200], [200, 300]];

/** P1-22: text-area input is one string; split it the way the backend does (、 ， ； , ;). */
const splitList = (v: unknown): string[] | undefined =>
  typeof v === "string" ? v.split(/[、，,；;]+/).map((s) => s.trim()).filter(Boolean) : undefined;

/** Generate a new version of SDS / TDS / COA / label (never edits an existing version). */
export function GenerateDocModal({ revision, open, onClose }: { revision: Revision; open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const [form] = Form.useForm();
  const docType = Form.useWatch("doc_type", form) as string | undefined;
  const [secrets, setSecrets] = useState<Secret[]>([]);
  const [tdsProps, setTdsProps] = useState<TdsProp[]>([]);
  const [tsIndicators, setTsIndicators] = useState<TsIndicator[]>([]);
  const product = useQuery({ queryKey: ["plm", "product", revision.product.id], enabled: open,
    queryFn: () => call<ProductDetail>("GET", `/api/plm/products/${revision.product.id}`) });
  const templates = useQuery({ queryKey: ["plm", "label-templates"], enabled: open && docType === "label",
    queryFn: () => call<{ id: string; name: string; lang: string; is_default: boolean }[]>("GET", "/api/plm/label-templates") });
  const tsTemplates = useQuery({ queryKey: ["plm", "ts-templates"], enabled: open && docType === "ts",
    queryFn: () => call<{ id: string; category: string; name: string; version: number; indicators: TsIndicator[] }[]>("GET", "/api/plm/ts-templates") });
  const candidates = useMutation({
    mutationFn: () => call<Candidates>("GET", `/api/plm/revisions/${revision.id}/property-candidates`),
    onSuccess: (c) => {
      const p = c.sds_properties;
      form.setFieldsValue({ sds_density: p.density_g_cm3?.value, sds_ph: p.ph?.value, sds_solids: p.solids_content_pct?.value,
        sds_voc: p.voc_g_l?.value, flash_point_c: p.flash_point_c?.value ?? form.getFieldValue("flash_point_c") });
      setTdsProps(Object.entries(p).map(([key, v]) => ({ key, label: key, value: v.value, method: v.method })));
      (c.notes ?? []).forEach((n) => message.info(n));
      message.success(t("doc.candidates_loaded", "已带入 {{n}} 项复核过的实测值", { n: Object.keys(p).length }));
    },
    onError: (e) => message.error(errorText(e)),
  });
  const gen = useMutation({
    mutationFn: (v: Record<string, any>) => {
      const props = { flash_point_c: v.flash_point_c, initial_boiling_point_c: v.initial_boiling_point_c, ph: v.ph,
        kinematic_viscosity_40c: v.kinematic_viscosity_40c, phys_state: v.phys_state ?? "liquid" };
      const measured = (k: string, x: unknown) => (x == null ? {} : { [k]: { value: x, source: "measured" } });
      return call<{ id: string; documents?: { id: string }[]; missing: string[]; warnings: string[] }>("POST", `/api/plm/revisions/${revision.id}/documents`, {
        doc_type: v.doc_type, lang: v.lang,
        regulations: ["sds", "label"].includes(v.doc_type) ? v.regulations : undefined,
        properties: ["sds", "label"].includes(v.doc_type) ? props : null,
        sds_properties: { ...measured("density_g_cm3", v.sds_density), ...measured("ph", v.sds_ph), ...measured("solids_content_pct", v.sds_solids), ...measured("voc_g_l", v.sds_voc) },
        trade_secrets: Object.fromEntries(secrets.filter((s) => s.cas && s.generic).map((s) => [s.cas, s.generic])),
        product: { recommended_use: v.recommended_use, restricted_use: v.restricted_use, name_en: revision.product.name_en },
        change_summary: v.change_summary,
        tds: { product: { description: v.description, features: splitList(v.features), applications: splitList(v.applications) }, properties: tdsProps.filter((p) => p.key && p.value != null),
          application: { method: v.app_method, substrates: v.substrates, film_thickness: v.film_thickness, cure: v.cure },
          storage: { shelf_life: v.shelf_life, conditions: v.storage_conditions, packaging: v.packaging } },
        coa: { batch_id: v.batch_id, expiry: v.expiry },
        label: { template_id: v.template_id, container_volume_l: v.container_volume_l, size_mm: v.size ? JSON.parse(v.size) : undefined,
          qr_payload: v.qr_payload, variables: { batch_no: v.var_batch, production_date: v.var_date, net_content: v.var_net } },
        ts: { template_id: v.ts_template_id, indicators: tsIndicators.filter((i) => i.key),
          properties: tdsProps.filter((p) => p.key).map((p) => ({ key: p.key, label: p.label, value: p.value, unit: p.unit, method: p.method })),
          inspection: { sampling: v.ts_sampling } },
      });
    },
    onSuccess: (d) => {
      if (d.missing.length) message.warning(t("doc.missing", "缺项：{{m}}", { m: d.missing.join("、") }));
      onClose();
      if (d.documents?.length) {
        message.success(t("doc.generated_multi", "已生成 {{n}} 个法规版本", { n: d.documents.length }));
        navigate(`/plm/documents/${d.documents[0].id}`);
      } else navigate(`/plm/documents/${d.id}`);
    },
    onError: (e) => message.error(errorText(e)),
  });
  const sds = docType === "sds" || docType === "label";
  // G2: default the regulation selection to the default company's
  // applicable_regulations (once per open, when the data arrives); an
  // empty default leaves the boxes unchecked and validation requires a pick.
  const companies = useQuery({ queryKey: ["plm", "companies"], enabled: open && sds,
    queryFn: () => call<{ id: string; is_default: boolean; applicable_regulations: string[] }[]>("GET", "/api/plm/companies") });
  const regulations = useQuery({ queryKey: ["plm", "regulations"], enabled: open && sds,
    queryFn: () => call<{ code: string; name: string; standard: string }[]>("GET", "/api/plm/regulations") });
  const regInit = useRef(false);
  useEffect(() => {
    if (!open) { regInit.current = false; return; }
    if (!sds || regInit.current || !companies.data) return;
    form.setFieldsValue({ regulations: companies.data.find((c) => c.is_default)?.applicable_regulations ?? [] });
    regInit.current = true;
  }, [open, sds, companies.data, form]);
  // Locked fields of the latest same-type document of this revision: regeneration
  // should preserve them — surface them here (the lock lives in the browser).
  const docs = useQuery({ queryKey: ["plm", "docs", revision.id], enabled: open,
    queryFn: () => call<DocSummary[]>("GET", `/api/plm/revisions/${revision.id}/documents`) });
  const latestOfType = (docs.data ?? []).filter((x) => x.doc_type === docType).sort((a, b) => b.version_no - a.version_no)[0];
  const lockedFields = latestOfType ? (loadLocks()[latestOfType.id] ?? []) : [];
  return (
    <Modal open={open} width={760} title={t("doc.generate", "生成文档")} onCancel={onClose} confirmLoading={gen.isPending} destroyOnHidden
      onOk={async () => gen.mutate(await form.validateFields())}>
      <Form form={form} layout="vertical" initialValues={{ doc_type: "sds", lang: "zh-CN", phys_state: "liquid" }}>
        {lockedFields.length > 0 && (
          <Alert type="warning" showIcon style={{ marginBottom: 12 }}
            message={t("doc.locked_notice", "上一版本有 {{n}} 个锁定字段，重新生成时请核对保留", { n: lockedFields.length })}
            description={<Space direction="vertical" size={4}>
              <Space wrap>{lockedFields.map((l) => <Tag key={l.fieldKey} color="gold">{l.label}：{l.value}</Tag>)}</Space>
              <Button size="small" onClick={() => {
                void navigator.clipboard?.writeText(JSON.stringify(lockedFields, null, 2));
                message.success(t("doc.locked_copied", "锁定字段已复制"));
              }}>{t("doc.locked_copy", "复制锁定字段值")}</Button>
            </Space>} />
        )}        <Space>
          <Form.Item name="doc_type" label={t("doc.type", "类型")}><Select style={{ width: 140 }} options={[
            { value: "sds", label: "SDS" }, { value: "tds", label: "TDS" }, { value: "label", label: t("doc.label", "标签") }, { value: "coa", label: "COA" },
            { value: "ts", label: "TS" }]} /></Form.Item>
          <Form.Item name="lang" label={t("doc.lang", "语言")}><Select style={{ width: 120 }} options={[{ value: "zh-CN", label: "中文" }, { value: "en-US", label: "English" }]} /></Form.Item>
          {(docType === "sds" || docType === "tds" || docType === "ts") && <Button style={{ marginTop: 8 }} loading={candidates.isPending} onClick={() => candidates.mutate()}>{t("doc.load_measured", "带入复核实测值")}</Button>}
        </Space>
        {sds && <Form.Item name="regulations" label={t("doc.regulations", "适用法规")}
          rules={[{ validator: (_, v) => (!v || v.length === 0)
            ? Promise.reject(new Error(t("doc.regulations_required", "请至少选择一个适用法规")))
            : Promise.resolve() }]}>
          <Checkbox.Group options={(regulations.data ?? []).map((r) => ({ label: `${r.name} (${r.code})`, value: r.code }))} />
        </Form.Item>}
        {sds && <>
          <Divider orientation="left" plain>{t("doc.classification_inputs", "分类输入（实测物性）")}</Divider>
          <Space wrap>
            <Form.Item name="flash_point_c" label={t("doc.flash", "闭杯闪点 ℃")}><InputNumber /></Form.Item>
            <Form.Item name="initial_boiling_point_c" label={t("doc.ibp", "初沸点 ℃")}><InputNumber /></Form.Item>
            <Form.Item name="ph" label="pH"><InputNumber min={0} max={14} /></Form.Item>
            <Form.Item name="kinematic_viscosity_40c" label={t("doc.visc40", "40 ℃ 运动黏度 mm²/s")}><InputNumber min={0} /></Form.Item>
          </Space>
        </>}
        {docType === "sds" && <>
          <Divider orientation="left" plain>{t("doc.section9", "第 9 部分理化值")}</Divider>
          <Space wrap>
            <Form.Item name="sds_density" label={t("doc.density", "密度 g/cm³")}><InputNumber /></Form.Item>
            <Form.Item name="sds_ph" label="pH"><InputNumber /></Form.Item>
            <Form.Item name="sds_solids" label={t("doc.solids", "固含 %")}><InputNumber /></Form.Item>
            <Form.Item name="sds_voc" label="VOC g/L"><InputNumber /></Form.Item>
          </Space>
          <Form.Item name="recommended_use" label={t("doc.use", "推荐用途")}><Input /></Form.Item>
          <Form.Item name="restricted_use" label={t("doc.restricted", "限制用途")}><Input /></Form.Item>
          <Typography.Text strong>{t("doc.secrets", "商业机密组分（GB/T 17519：以通用名 + 浓度区间披露）")}</Typography.Text>
          <EditableRows<Secret> value={secrets} onChange={setSecrets} newRow={() => ({ cas: "", generic: "" })} columns={[
            { title: "CAS", dataIndex: "cas" }, { title: t("doc.generic", "通用化学名"), dataIndex: "generic" }]} />
          <Form.Item name="change_summary" label={t("doc.change_summary", "修订说明（第 16 部分）")}><Input.TextArea rows={2} /></Form.Item>
        </>}
        {docType === "tds" && <>
          <Form.Item name="description" label={t("doc.description", "产品说明")} rules={[{ required: true }]}><Input.TextArea rows={2} /></Form.Item>
          <Form.Item name="features" label={t("doc.features", "特点")}><Input.TextArea rows={2} placeholder="多个特点用顿号、逗号或分号分隔" /></Form.Item>
          <Form.Item name="applications" label={t("doc.applications", "应用领域")}><Input.TextArea rows={2} placeholder="多个应用领域用顿号、逗号或分号分隔" /></Form.Item>
          <Typography.Text strong>{t("doc.typical", "典型物性")}</Typography.Text>
          <EditableRows<TdsProp> value={tdsProps} onChange={setTdsProps} newRow={() => ({ key: "", value: null })} columns={[
            { title: "key", dataIndex: "key" }, { title: t("doc.item", "项目"), dataIndex: "label" }, { title: t("measure.value", "数值"), dataIndex: "value", valueType: "digit" },
            { title: t("spec.unit", "单位"), dataIndex: "unit" }, { title: t("measure.method", "方法"), dataIndex: "method" }]} />
          <Space wrap style={{ marginTop: 8 }}>
            <Form.Item name="app_method" label={t("doc.app_method", "施工方式")}><Input /></Form.Item>
            <Form.Item name="substrates" label={t("doc.substrates", "适用基材")}><Input /></Form.Item>
            <Form.Item name="film_thickness" label={t("doc.film", "推荐膜厚")}><Input /></Form.Item>
            <Form.Item name="cure" label={t("doc.cure", "固化条件")}><Input /></Form.Item>
            <Form.Item name="shelf_life" label={t("doc.shelf", "保质期")}><Input /></Form.Item>
            <Form.Item name="storage_conditions" label={t("doc.storage", "储存条件")}><Input /></Form.Item>
            <Form.Item name="packaging" label={t("doc.packaging", "包装规格")}><Input /></Form.Item>
          </Space>
        </>}
        {docType === "ts" && <>
          <Divider orientation="left" plain>TS 质量指标（限值由质量部门填写，模板只提供结构）</Divider>
          <Space wrap>
            <Form.Item name="ts_template_id" label="TS 模板">
              <Select allowClear style={{ width: 260 }} placeholder="不使用模板" options={(tsTemplates.data ?? []).map((x) => ({ value: x.id, label: `${x.category} · ${x.name} v${x.version}` }))}
                onChange={(id) => { const tpl = (tsTemplates.data ?? []).find((x) => x.id === id); setTsIndicators(tpl ? tpl.indicators : []); }} />
            </Form.Item>
            <Form.Item name="ts_sampling" label="出厂检验抽样规则"><Input style={{ width: 260 }} placeholder="如：每批抽检 1 件" /></Form.Item>
          </Space>
          <EditableRows<TsIndicator> value={tsIndicators} onChange={setTsIndicators} newRow={() => ({ key: "" })} columns={[
            { title: "key", dataIndex: "key" }, { title: "指标", dataIndex: "label" }, { title: "单位", dataIndex: "unit" },
            { title: "下限", dataIndex: "spec_min", valueType: "digit" }, { title: "上限", dataIndex: "spec_max", valueType: "digit" },
            { title: "目标值", dataIndex: "spec_target", valueType: "digit" }, { title: "检验方法", dataIndex: "test_method" },
            { title: "出厂检验", dataIndex: "is_factory_inspection", valueType: "checkbox" }]} />
          <Typography.Text strong style={{ display: "block", marginTop: 12 }}>理化特性（复核实测值带入）</Typography.Text>
          <EditableRows<TdsProp> value={tdsProps} onChange={setTdsProps} newRow={() => ({ key: "", value: null })} columns={[
            { title: "key", dataIndex: "key" }, { title: "项目", dataIndex: "label" }, { title: "数值", dataIndex: "value", valueType: "digit" },
            { title: "单位", dataIndex: "unit" }, { title: "方法", dataIndex: "method" }]} />
        </>}
        {docType === "coa" && <Space wrap>
          <Form.Item name="batch_id" label={t("plm.batch_no", "批号")} rules={[{ required: true }]}>
            <Select style={{ width: 220 }} options={(product.data?.batches ?? []).map((b) => ({ value: b.id, label: b.batch_no }))} />
          </Form.Item>
          <Form.Item name="expiry" label={t("doc.expiry", "有效期至")}><Input placeholder="YYYY-MM-DD" /></Form.Item>
        </Space>}
        {docType === "label" && <>
          <Space wrap>
            <Form.Item name="template_id" label={t("doc.layout", "标签版式")}>
              <Select allowClear style={{ width: 220 }} placeholder={t("doc.default_layout", "默认版式")} options={(templates.data ?? []).map((x) => ({ value: x.id, label: `${x.name}${x.is_default ? " ★" : ""}` }))} />
            </Form.Item>
            <Form.Item name="size" label={t("doc.size", "尺寸（无版式时）")}><Select allowClear style={{ width: 150 }} options={LABEL_SIZES.map((s) => ({ value: JSON.stringify(s), label: `${s[0]}×${s[1]} mm` }))} /></Form.Item>
            <Form.Item name="container_volume_l" label={t("doc.volume", "容器容积 L（GB 15258 尺寸校核）")}><InputNumber min={0} /></Form.Item>
          </Space>
          <Space wrap>
            <Form.Item name="var_batch" label={t("doc.var_batch", "批号变量")}><Input /></Form.Item>
            <Form.Item name="var_date" label={t("doc.var_date", "生产日期变量")}><Input /></Form.Item>
            <Form.Item name="var_net" label={t("doc.var_net", "净含量变量")}><Input /></Form.Item>
            <Form.Item name="qr_payload" label={t("doc.qr", "二维码内容")}><Input /></Form.Item>
          </Space>
        </>}
      </Form>
    </Modal>
  );
}

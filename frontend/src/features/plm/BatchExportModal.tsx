import { Alert, App, Button, Checkbox, Form, Input, Modal, Select, Space } from "antd";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { errorText } from "@/shared/feedback";
import type { Product } from "./types";

interface BatchResult { id: string; status: string; file_count: number; missing: { product_code: string | null; doc_type: string | null; reason: string }[] }

/** 批量文档导出（整合方案 §4）：多产品 × 多文档类型 → 一个 ZIP；缺失文档如实报告。 */
export function BatchExportModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [result, setResult] = useState<BatchResult | null>(null);
  const products = useQuery({ queryKey: ["plm", "products"], enabled: open,
    queryFn: () => call<Product[]>("GET", "/api/plm/products?limit=1000") });
  const run = useMutation({
    mutationFn: (v: Record<string, unknown>) => call<BatchResult>("POST", "/api/plm/documents/batch-export", v),
    onSuccess: (r) => { setResult(r); message.success(t("plm.batch_done", "批量导出完成：{{n}} 个文件", { n: r.file_count })); },
    onError: (e) => message.error(errorText(e)),
  });
  return (
    <Modal open={open} width={640} title={t("plm.batch_export", "批量导出文档")} onCancel={() => { onClose(); setResult(null); }}
      footer={result ? [<Button key="dl" type="primary" href={`/api/plm/documents/batch-export/${result.id}/download`}>{t("common.download", "下载 ZIP")}</Button>,
        <Button key="close" onClick={() => { onClose(); setResult(null); }}>{t("common.close", "关闭")}</Button>]
        : [<Button key="cancel" onClick={onClose}>{t("common.cancel", "取消")}</Button>,
          <Button key="ok" type="primary" loading={run.isPending} onClick={async () => run.mutate(await form.validateFields())}>{t("plm.export", "导出")}</Button>]}
      destroyOnHidden>
      <Form form={form} layout="vertical" initialValues={{ doc_types: ["sds", "tds", "ts"], lang: "zh-CN", filename_template: "{company}_{product}_{doc_type}_{lang}_{version}.{ext}", include_docx: false }}>
        <Form.Item name="product_ids" label={t("plm.products", "产品（最多 20 个）")} rules={[{ required: true }]}>
          <Select mode="multiple" placeholder={t("plm.select_products", "选择产品")} options={(products.data ?? []).map((p) => ({ value: p.id, label: `${p.code} ${p.name_zh}` }))} />
        </Form.Item>
        <Form.Item name="doc_types" label={t("doc.types", "文档类型")} rules={[{ required: true }]}>
          <Checkbox.Group options={[{ value: "sds", label: "SDS" }, { value: "tds", label: "TDS" }, { value: "ts", label: "TS" }, { value: "label", label: t("doc.label", "标签") }, { value: "coa", label: "COA" }]} />
        </Form.Item>
        <Space>
          <Form.Item name="lang" label={t("doc.lang", "语言")}><Select style={{ width: 120 }} options={[{ value: "zh-CN", label: "中文" }, { value: "en-US", label: "English" }]} /></Form.Item>
          <Form.Item name="include_docx" valuePropName="checked" label=" "><Checkbox>同时导出 Word</Checkbox></Form.Item>
        </Space>
        <Form.Item name="filename_template" label={t("plm.filename_template", "文件名模板")} rules={[{ required: true }]}>
          <Input placeholder="{company}_{product}_{doc_type}_{lang}_{version}.{ext}" />
        </Form.Item>
      </Form>
      {result && result.missing.length > 0 && (
        <Alert type="warning" showIcon message={t("plm.batch_missing", "以下文档不存在，未导出：")}
          description={<ul style={{ margin: 0, paddingLeft: 18 }}>{result.missing.map((m, i) => (
            <li key={i}>{m.product_code ?? "—"} · {m.doc_type ?? "—"}：{m.reason}</li>))}</ul>} />
      )}
    </Modal>
  );
}

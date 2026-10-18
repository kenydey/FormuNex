import { PageContainer, ProTable, type ProColumns } from "@ant-design/pro-components";
import { Alert, App, Button, Checkbox, Form, Input, Modal, Space } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { errorText } from "@/shared/feedback";

interface Mapping { id: string; source: string; source_ref: string; product_id: string | null; product_code: string | null; company_name: string | null; formulation_version_id: string | null; created_at: string }
interface ImportReport { created: { product_code: string }[]; skipped: { product_code: string; reason: string }[]; errors: { index: number; product_code?: string; error: string }[]; warnings: string[]; dry_run?: boolean }

/** 配方数据库映射（整合方案 §6）：MSDS-Generator 配方记录 → FormuNex 产品 + 配方版本。 */
export default function FormulaMappingsPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const [importOpen, setImportOpen] = useState(false);
  const [form] = Form.useForm();
  const [report, setReport] = useState<ImportReport | null>(null);
  const mappings = useQuery({ queryKey: ["plm", "formula-mappings"], queryFn: () => call<Mapping[]>("GET", "/api/plm/formula-mappings") });
  const doImport = useMutation({
    mutationFn: (v: { json: string; dry_run: boolean }) => {
      let records: unknown;
      try { records = JSON.parse(v.json); } catch { throw new Error("JSON 解析失败"); }
      if (!Array.isArray(records)) throw new Error("顶层必须是记录数组");
      return call<ImportReport>("POST", "/api/plm/formula-mappings/import", { records, dry_run: v.dry_run });
    },
    onSuccess: (r) => { setReport(r); if (!r.dry_run) void qc.invalidateQueries({ queryKey: ["plm", "formula-mappings"] }); },
    onError: (e) => message.error(errorText(e)),
  });
  const createRevision = useMutation({
    mutationFn: (id: string) => call<{ id: string }>("POST", `/api/plm/formula-mappings/${id}/create-revision`, {}),
    onSuccess: () => { message.success("已创建产品修订版"); void qc.invalidateQueries({ queryKey: ["plm", "formula-mappings"] }); },
    onError: (e) => message.error(errorText(e)),
  });
  const columns: ProColumns<Mapping>[] = [
    { title: t("plm.code", "产品代码"), dataIndex: "product_code", render: (_d, r) => r.product_id ? <Link to={`/plm/products/${r.product_id}`}>{r.product_code}</Link> : r.product_code },
    { title: "公司", dataIndex: "company_name" },
    { title: "来源", dataIndex: "source" },
    { title: "外部记录", dataIndex: "source_ref" },
    { title: "配方版本", dataIndex: "formulation_version_id", render: (v) => v ? String(v).slice(0, 8) : "—" },
    { title: t("common.created", "创建时间"), dataIndex: "created_at", valueType: "dateTime" },
    { title: t("common.actions", "操作"), valueType: "option", render: (_d, r) => [
      <a key="rev" onClick={() => createRevision.mutate(r.id)}>创建修订版</a>,
    ] },
  ];
  return (
    <PageContainer>
      <ProTable<Mapping> rowKey="id" search={false} columns={columns} dataSource={mappings.data} loading={mappings.isLoading}
        toolBarRender={() => [<Button key="import" type="primary" onClick={() => { setReport(null); setImportOpen(true); }}>导入配方记录</Button>]}
        options={{ reload: () => void mappings.refetch() }} />
      <Modal open={importOpen} width={720} title="导入 MSDS-Generator 配方记录" destroyOnHidden
        onCancel={() => setImportOpen(false)} confirmLoading={doImport.isPending}
        onOk={async () => doImport.mutate(await form.validateFields())}
        footer={report && !report.dry_run ? [<Button key="close" type="primary" onClick={() => setImportOpen(false)}>关闭</Button>] : undefined}>
        <Form form={form} layout="vertical" initialValues={{ dry_run: true }}>
          <Form.Item name="json" label="配方记录 JSON 数组" rules={[{ required: true }]}>
            <Input.TextArea rows={8} placeholder='[{"product_code": "FX-100", "components": [{"name": "水", "pct": 100}], "source_ref": "msds-gen-001"}]' />
          </Form.Item>
          <Form.Item name="dry_run" valuePropName="checked"><Checkbox>仅校验（dry-run，不写入）</Checkbox></Form.Item>
        </Form>
        {report && <Space direction="vertical" style={{ width: "100%" }}>
          <Alert type="info" showIcon message={`创建 ${report.created.length} 条 · 跳过 ${report.skipped.length} 条 · 错误 ${report.errors.length} 条${report.dry_run ? "（dry-run，未写入）" : ""}`} />
          {report.warnings.length > 0 && <Alert type="warning" showIcon message="警告" description={report.warnings.join("；")} />}
          {report.errors.length > 0 && <Alert type="error" showIcon message="错误" description={report.errors.map((e) => `#${e.index} ${e.product_code ?? ""}：${e.error}`).join("；")} />}
          {report.skipped.length > 0 && <Alert type="warning" showIcon message="跳过（已存在，不覆盖）" description={report.skipped.map((s) => `${s.product_code}：${s.reason}`).join("；")} />}
        </Space>}
      </Modal>
    </PageContainer>
  );
}

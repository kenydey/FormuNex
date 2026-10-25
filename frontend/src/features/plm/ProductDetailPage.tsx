import { PageContainer, ProCard, ProTable } from "@ant-design/pro-components";
import { App, Button, DatePicker, Form, Input, InputNumber, Modal, Select, Space, Tag } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { EditableRows } from "@/shared/EditableRows";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { StatusTag } from "@/shared/tags";
import type { ProjectDetail } from "@/features/spec/types";
import type { ProductDetail } from "./types";
import { ProductLineageChainCard } from "./ProductLineageChain";

interface Lineage { lineage_id: string; versions: { id: string; version: number; name: string }[] }
interface QcRow { metric: string; value: number | null; unit?: string; test_method?: string }

export default function ProductDetailPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { productId = "" } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const p = useQuery({ queryKey: ["plm", "product", productId], queryFn: () => call<ProductDetail>("GET", `/api/plm/products/${productId}`) });
  const lineages = useQuery({ queryKey: ["formulations", "lineages", ""], queryFn: () => call<Lineage[]>("GET", "/api/formulations/versions?limit=50") });
  const project = useQuery({ queryKey: ["project", p.data?.project_id], enabled: !!p.data?.project_id,
    queryFn: () => call<ProjectDetail>("GET", `/api/projects/${p.data!.project_id}`) });
  const [revOpen, setRevOpen] = useState(false);
  const [batchOpen, setBatchOpen] = useState(false);
  const [qcBatch, setQcBatch] = useState<string | null>(null);
  const [qcRows, setQcRows] = useState<QcRow[]>([]);
  const [rForm] = Form.useForm();
  const [bForm] = Form.useForm();
  const refresh = () => void qc.invalidateQueries({ queryKey: ["plm"] });
  const createRev = useMutation({
    mutationFn: (v: { formulation_version_id: string; copy_spec: boolean }) => {
      const sc = project.data?.workspace.spec_contract ?? {};
      const spec = v.copy_spec ? { release_specs: sc.release_specs ?? [], acceptance: sc.acceptance ?? [], red_lines: sc.red_lines ?? [] } : {};
      return call<{ id: string }>("POST", `/api/plm/products/${productId}/revisions`, { formulation_version_id: v.formulation_version_id, spec_snapshot: spec });
    },
    onSuccess: (r) => navigate(`/plm/revisions/${r.id}`),
    onError: (e) => message.error(errorText(e)),
  });
  const createBatch = useMutation({
    mutationFn: (v: { batch_no: string; revision_id?: string; produced_at?: { format: (f: string) => string }; quantity_kg?: number }) =>
      call("POST", `/api/plm/products/${productId}/batches`, { ...v, produced_at: v.produced_at?.format("YYYY-MM-DD") }),
    onSuccess: () => { setBatchOpen(false); refresh(); }, onError: (e) => message.error(errorText(e)),
  });
  const addQc = useMutation({
    mutationFn: () => call("POST", `/api/plm/batches/${qcBatch}/qc-results`, { results: qcRows.filter((r) => r.metric && r.value != null) }),
    onSuccess: () => { message.success(t("common.saved", "已保存")); setQcBatch(null); setQcRows([]); }, onError: (e) => message.error(errorText(e)),
  });
  if (p.error) return <PageContainer><ErrorAlert error={p.error} /></PageContainer>;
  const d = p.data;
  return (
    <PageContainer loading={!d} title={d ? `${d.code} · ${d.name_zh}` : ""} subTitle={d && <Space>
      <StatusTag status={d.lifecycle_state} />
      {d.category_identifier && <Tag>编码 Category：{d.category_identifier}</Tag>}
      {d.legacy_code && <Tag>旧码：{d.legacy_code}</Tag>}
    </Space>}>
      <ProCard gutter={16} ghost wrap>
        <ProCard colSpan={{ xs: 24, md: 14 }} bordered title={t("plm.revisions", "修订版")}
          extra={<Button type="primary" onClick={() => setRevOpen(true)}>{t("plm.new_revision", "新建修订版")}</Button>}>
          <ProTable rowKey="id" search={false} options={false} pagination={false} dataSource={d?.revisions} columns={[
            { title: t("plm.rev", "修订"), dataIndex: "rev", render: (_d, r) => <Link to={`/plm/revisions/${r.id}`}>{r.rev}</Link> },
            { title: t("plm.state", "状态"), dataIndex: "state", render: (_d, r) => <StatusTag status={r.state} /> },
            { title: t("plm.frozen", "冻结时间"), dataIndex: "frozen_at", valueType: "dateTime" },
          ]} />
        </ProCard>
        <ProCard colSpan={{ xs: 24, md: 10 }} bordered title={t("plm.batches", "批次")}
          extra={<Button onClick={() => setBatchOpen(true)}>{t("plm.new_batch", "新建批次")}</Button>}>
          <ProTable rowKey="id" search={false} options={false} pagination={false} dataSource={d?.batches} columns={[
            { title: t("plm.batch_no", "批号"), dataIndex: "batch_no" },
            { title: t("plm.state", "状态"), dataIndex: "status" },
            { title: "", valueType: "option", render: (_d, r) => [<a key="qc" onClick={() => { setQcBatch(r.id); setQcRows([{ metric: "", value: null }]); }}>{t("plm.qc", "录入质检结果")}</a>] },
          ]} />
        </ProCard>
      </ProCard>
      <ProductLineageChainCard productId={productId} />
      {/* preserve={false} + initialValues: the Form is destroyOnHidden, so
          calling form methods while the modal is closed hits an unconnected
          instance (antd useForm warning), and deferring them to
          afterOpenChange races with user input. Fresh mount per open gives
          the copy_spec default without touching the form from outside. */}
      <Modal open={revOpen} title={t("plm.new_revision", "新建修订版")} onCancel={() => setRevOpen(false)} confirmLoading={createRev.isPending}
        onOk={async () => createRev.mutate(await rForm.validateFields())} destroyOnHidden>
        <Form form={rForm} layout="vertical" preserve={false} initialValues={{ copy_spec: true }}>
          <Form.Item name="formulation_version_id" label={t("winning.version", "配方版本")} rules={[{ required: true }]}>
            <Select showSearch optionFilterProp="label" options={(lineages.data ?? []).flatMap((l) => l.versions.map((v) => ({ value: v.id, label: `${v.name} v${v.version}` })))} />
          </Form.Item>
          <Form.Item name="copy_spec" label={t("plm.copy_spec", "从项目规格契约复制出厂指标 / 验收指标 / 红线")}>
            <Select options={[{ value: true, label: t("common.yes", "是") }, { value: false, label: t("common.no", "否") }]} disabled={!d?.project_id} />
          </Form.Item>
        </Form>
      </Modal>
      <Modal open={batchOpen} title={t("plm.new_batch", "新建批次")} onCancel={() => setBatchOpen(false)} confirmLoading={createBatch.isPending}
        onOk={async () => createBatch.mutate(await bForm.validateFields())} destroyOnHidden>
        <Form form={bForm} layout="vertical" preserve={false}>
          <Form.Item name="batch_no" label={t("plm.batch_no", "批号")} rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="revision_id" label={t("plm.rev", "修订")}><Select allowClear options={(d?.revisions ?? []).map((r) => ({ value: r.id, label: r.rev }))} /></Form.Item>
          <Space>
            <Form.Item name="produced_at" label={t("plm.produced", "生产日期")}><DatePicker /></Form.Item>
            <Form.Item name="quantity_kg" label={t("plm.qty", "批量（kg）")}><InputNumber min={0} /></Form.Item>
          </Space>
        </Form>
      </Modal>
      <Modal open={!!qcBatch} width={760} title={t("plm.qc", "录入质检结果")} onCancel={() => setQcBatch(null)} confirmLoading={addQc.isPending} onOk={() => addQc.mutate()}>
        <EditableRows<QcRow> value={qcRows} onChange={setQcRows} newRow={() => ({ metric: "", value: null })} columns={[
          { title: t("tasks.metric", "指标（与出厂指标一致）"), dataIndex: "metric" },
          { title: t("measure.value", "数值"), dataIndex: "value", valueType: "digit" },
          { title: t("spec.unit", "单位"), dataIndex: "unit" },
          { title: t("measure.method", "方法"), dataIndex: "test_method" },
        ]} />
      </Modal>
    </PageContainer>
  );
}

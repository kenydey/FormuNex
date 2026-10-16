import { PageContainer, ProCard, ProTable } from "@ant-design/pro-components";
import { UploadOutlined } from "@ant-design/icons";
import { Alert, App, Button, Descriptions, Form, Input, InputNumber, Modal, Select, Space, Table, Tag, Timeline, Upload } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useSession } from "@/app/session";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { StatusTag } from "@/shared/tags";
import type { Instrument, LabTask, Measurement, Method } from "./types";

interface Entry { metric: string; value: number | null; method_code?: string; instrument_id?: string; note?: string }

export default function TaskDetailPage() {
  const { t } = useTranslation();
  const { message, modal } = App.useApp();
  const { taskId = "" } = useParams();
  const { me, can } = useSession();
  const qc = useQueryClient();
  const task = useQuery({ queryKey: ["lab", "task", taskId], queryFn: () => call<LabTask>("GET", `/api/lab/tasks/${taskId}`) });
  const methods = useQuery({ queryKey: ["lab", "methods"], queryFn: () => call<Method[]>("GET", "/api/lab/methods") });
  const instruments = useQuery({ queryKey: ["lab", "instruments"], queryFn: () => call<Instrument[]>("GET", "/api/lab/instruments") });
  const [entries, setEntries] = useState<Entry[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [history, setHistory] = useState<{ id: string; rows: { field: string; old: string; new: string; reason: string; at: string }[] } | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const refresh = () => { void qc.invalidateQueries({ queryKey: ["lab"] }); };
  const act = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: unknown }) => call<{ warnings?: string[] }>("POST", path, body),
    onSuccess: (out) => { setWarnings(out?.warnings ?? []); refresh(); },
    onError: (e) => message.error(errorText(e)),
  });
  const data = task.data;
  if (task.error) return <PageContainer><ErrorAlert error={task.error} /></PageContainer>;
  if (!data) return <PageContainer loading />;
  const mine = data.assignee_id === me?.id;
  const editable = (data.status === "open" && !data.assignee_id) || (data.status === "in_progress" && mine);
  const startEntries = () => setEntries(data.requested_metrics.map((r) => ({
    metric: r.metric, value: null, method_code: r.method_code ?? methods.data?.find((m) => m.metric === r.metric)?.code,
  })));

  const amend = (m: Measurement) => {
    let value: number | null = m.value;
    let reason = "";
    modal.confirm({
      title: t("measure.amend", "修改测量值"),
      content: <Space direction="vertical" style={{ width: "100%" }}>
        <InputNumber defaultValue={m.value} onChange={(v) => { value = v; }} style={{ width: "100%" }} />
        <Input.TextArea placeholder={t("measure.reason", "修改理由（必填，保留原值）")} onChange={(e) => { reason = e.target.value; }} />
      </Space>,
      onOk: () => act.mutateAsync({ path: `/api/lab/measurements/${m.id}/amend`, body: { value, reason } }),
    });
  };

  return (
    <PageContainer title={data.task_no} subTitle={<StatusTag status={data.status} />}
      extra={<Space>
        {data.status === "open" && can("lab.execute") && <Button onClick={() => act.mutate({ path: `/api/lab/tasks/${taskId}/claim` })}>{t("tasks.claim", "领取")}</Button>}
        {data.status === "in_progress" && mine && <Button type="primary" onClick={() => act.mutate({ path: `/api/lab/tasks/${taskId}/submit` })}>{t("tasks.submit", "提交复核")}</Button>}
        {["open", "in_progress", "pending_review"].includes(data.status) && can("doe.write") &&
          <Button danger onClick={() => {
            let reason = "";
            modal.confirm({ title: t("tasks.cancel", "取消任务单"), content: <Input onChange={(e) => { reason = e.target.value; }} placeholder={t("tasks.cancel_reason", "取消理由")} />,
              onOk: () => act.mutateAsync({ path: `/api/lab/tasks/${taskId}/cancel`, body: { reason } }) });
          }}>{t("tasks.cancel", "取消任务单")}</Button>}
      </Space>}>
      <ProCard gutter={16} ghost wrap>
        <ProCard colSpan={{ xs: 24, md: 8 }} bordered title={t("tasks.info", "任务信息")}>
          <Descriptions column={1} size="small">
            <Descriptions.Item label={t("tasks.sample", "样品号")}><Tag>{data.sample_no}</Tag></Descriptions.Item>
            <Descriptions.Item label={t("tasks.run", "试验点")}>{data.run_index ?? "—"}</Descriptions.Item>
            {Object.entries(data.factors).map(([k, v]) => <Descriptions.Item key={k} label={k}>{String(v)}</Descriptions.Item>)}
          </Descriptions>
          <Table size="small" pagination={false} rowKey="metric" dataSource={data.requested_metrics} columns={[
            { title: t("tasks.metric", "待测指标"), dataIndex: "metric" },
            { title: t("tasks.predicted", "模型预测"), dataIndex: "predicted", render: (v) => v ?? "—" },
            { title: t("tasks.reviewed_value", "复核值"), render: (_v, r) => data.measured?.[r.metric] ?? "—" },
          ]} />
        </ProCard>
        <ProCard colSpan={{ xs: 24, md: 16 }} bordered title={t("measure.entry", "录入")}
          extra={editable && <Space>
            <Button onClick={startEntries}>{t("measure.prefill", "按待测指标生成录入行")}</Button>
            <Button icon={<UploadOutlined />} onClick={() => setImportOpen(true)}>{t("measure.import", "导入仪器文件")}</Button>
          </Space>}>
          {!editable && <Alert type="info" showIcon message={t("measure.locked", "只有领取人在任务进行中时可以录入")} style={{ marginBottom: 8 }} />}
          {warnings.length > 0 && <Alert type="warning" showIcon closable style={{ marginBottom: 8 }} message={warnings.map((w, i) => <div key={i}>{w}</div>)} onClose={() => setWarnings([])} />}
          {editable && entries.length > 0 && (
            <>
              <Table size="small" pagination={false} rowKey="metric" dataSource={entries} columns={[
                { title: t("tasks.metric", "指标"), dataIndex: "metric" },
                { title: t("measure.value", "数值"), render: (_v, r, i) => <InputNumber value={r.value ?? undefined} onChange={(v) => setEntries(entries.map((e, j) => (j === i ? { ...e, value: v } : e)))} /> },
                { title: t("measure.method", "方法"), render: (_v, r, i) => <Select style={{ width: 200 }} value={r.method_code}
                  options={(methods.data ?? []).filter((m) => m.active && (!m.metric || m.metric === r.metric)).map((m) => ({ value: m.code, label: `${m.code} ${m.unit}` }))}
                  onChange={(v) => setEntries(entries.map((e, j) => (j === i ? { ...e, method_code: v } : e)))} /> },
                { title: t("measure.instrument", "仪器"), render: (_v, r, i) => <Select allowClear style={{ width: 180 }} value={r.instrument_id}
                  options={(instruments.data ?? []).filter((x) => x.active).map((x) => ({ value: x.id, label: `${x.asset_no} ${x.name}`, disabled: x.calibration_state === "overdue" }))}
                  onChange={(v) => setEntries(entries.map((e, j) => (j === i ? { ...e, instrument_id: v } : e)))} /> },
              ]} />
              <Button type="primary" style={{ marginTop: 8 }} loading={act.isPending}
                onClick={() => act.mutate({ path: `/api/lab/tasks/${taskId}/measurements`, body: {
                  measurements: entries.filter((e) => e.value != null).map((e) => ({ metric: e.metric, value: e.value, method_code: e.method_code, instrument_id: e.instrument_id })) } },
                  { onSuccess: () => setEntries([]) })}>{t("measure.record", "保存录入")}</Button>
            </>
          )}
        </ProCard>
      </ProCard>
      <ProCard bordered style={{ marginTop: 16 }} title={t("measure.list", "测量记录（全部保留）")}>
        <ProTable<Measurement> rowKey="id" search={false} options={false} pagination={false} dataSource={data.measurements} columns={[
          { title: t("tasks.metric", "指标"), dataIndex: "metric" },
          { title: t("measure.value", "数值"), dataIndex: "value", render: (_d, r) => `${r.value} ${r.unit}` },
          { title: t("measure.method", "方法"), dataIndex: "test_method" },
          { title: t("measure.operator", "录入人"), dataIndex: "operator" },
          { title: t("measure.status", "复核"), dataIndex: "review_status", render: (_d, r) => <StatusTag status={r.review_status} /> },
          { title: t("measure.note", "备注"), dataIndex: "note", ellipsis: true },
          { title: t("common.actions", "操作"), valueType: "option", render: (_d, r) => [
            (r.operator_id === me?.id || can("lab.review")) && r.review_status !== "legacy" && <a key="amend" onClick={() => amend(r)}>{t("measure.amend", "修改")}</a>,
            r.review_status === "reviewed" && can("lab.review") && <a key="pick" onClick={() => act.mutate({ path: `/api/lab/measurements/${r.id}/pick`, body: { reason: "" } })}>{t("measure.pick", "指定为代表值")}</a>,
            <a key="hist" onClick={async () => setHistory({ id: r.id, rows: await call("GET", `/api/lab/measurements/${r.id}/history`) })}>{t("measure.history", "留痕")}</a>,
          ] },
        ]} />
      </ProCard>
      <Modal open={!!history} footer={null} onCancel={() => setHistory(null)} title={t("measure.history", "修改留痕")}>
        <Timeline items={(history?.rows ?? []).map((h) => ({ children: <span>{h.field}：{h.old} → {h.new}<br />{h.reason} · {h.at.slice(0, 19).replace("T", " ")}</span> }))} />
      </Modal>
      <ImportModal open={importOpen} onClose={() => setImportOpen(false)} methods={methods.data ?? []} instruments={instruments.data ?? []} onDone={refresh} />
    </PageContainer>
  );
}

function ImportModal({ open, onClose, methods, instruments, onDone }: { open: boolean; onClose: () => void; methods: Method[]; instruments: Instrument[]; onDone: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [file, setFile] = useState<File | null>(null);
  const [form] = Form.useForm();
  const parsers = useQuery({ queryKey: ["lab", "parsers"], enabled: open,
    queryFn: () => call<Record<string, { label: string; columns?: Record<string, { metric: string }> }>>("GET", "/api/lab/parsers") });
  const template = Form.useWatch("template", form) as string | undefined;
  const metrics = Object.values(parsers.data?.[template ?? ""]?.columns ?? {}).map((c) => c.metric);
  const submit = useMutation({
    mutationFn: async (v: { template: string; instrument_id?: string; methods: Record<string, string> }) => {
      const fd = new FormData();
      fd.append("file", file!);
      fd.append("template", v.template);
      fd.append("methods", JSON.stringify(Object.fromEntries(Object.entries(v.methods ?? {}).filter(([, x]) => x))));
      if (v.instrument_id) fd.append("instrument_id", v.instrument_id);
      return call<{ measurements: unknown[]; errors: { sample_no: string; error: string }[]; warnings: string[] }>("POST", "/api/lab/measurements/import", fd);
    },
    onSuccess: (out) => {
      message.success(t("measure.imported", "已导入 {{n}} 条", { n: out.measurements.length }));
      out.errors.forEach((e) => message.warning(`${e.sample_no}：${e.error}`));
      onDone();
      onClose();
    },
    onError: (e) => message.error(errorText(e)),
  });
  return (
    <Modal open={open} title={t("measure.import", "导入仪器文件")} onCancel={onClose} confirmLoading={submit.isPending}
      okButtonProps={{ disabled: !file }} onOk={async () => submit.mutate(await form.validateFields())} destroyOnHidden>
      <Form form={form} layout="vertical">
        <Form.Item label={t("measure.file", "文件（CSV / Excel，样品号须与任务单一致）")}>
          <Upload beforeUpload={(f) => { setFile(f); return false; }} maxCount={1} onRemove={() => setFile(null)}>
            <Button icon={<UploadOutlined />}>{t("measure.choose", "选择文件")}</Button>
          </Upload>
        </Form.Item>
        <Form.Item name="template" label={t("measure.template", "解析模板")} rules={[{ required: true }]}>
          <Select options={Object.entries(parsers.data ?? {}).map(([k, v]) => ({ value: k, label: v.label }))} />
        </Form.Item>
        <Form.Item name="instrument_id" label={t("measure.instrument", "仪器")}>
          <Select allowClear options={instruments.map((x) => ({ value: x.id, label: `${x.asset_no} ${x.name}`, disabled: x.calibration_state === "overdue" }))} />
        </Form.Item>
        {metrics.map((m) => (
          <Form.Item key={m} name={["methods", m]} label={t("measure.method_for", "{{m}} 的检测方法（不选则跳过该列）", { m })}>
            <Select allowClear options={methods.filter((x) => x.metric === m || !x.metric).map((x) => ({ value: x.code, label: `${x.code} ${x.name_zh}` }))} />
          </Form.Item>
        ))}
      </Form>
    </Modal>
  );
}

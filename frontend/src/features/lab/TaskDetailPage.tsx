import { PageContainer, ProCard, ProTable } from "@ant-design/pro-components";
import { UploadOutlined } from "@ant-design/icons";
import { RevoGrid } from "@revolist/react-datagrid";
import { Alert, App, Button, Descriptions, Form, Input, InputNumber, Modal, Select, Space, Table, Tag, Timeline, Typography, Upload } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call, fetchBlobUrl } from "@/api/client";
import { useSession } from "@/app/session";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { StatusTag } from "@/shared/tags";
import type { Instrument, LabTask, Measurement, Method } from "./types";
import { siblingNav } from "./matrixNav";
import "./grid.css";

interface Entry { metric: string; value: number | null; method_code?: string; instrument_id?: string; note?: string }

interface TaskAttachment { id: string; filename: string; content_type: string; size_bytes: number; sha256: string; uploaded_by?: string | null; created_at?: string | null }

export default function TaskDetailPage() {
  const { t } = useTranslation();
  const { message, modal } = App.useApp();
  const { taskId = "" } = useParams();
  const navigate = useNavigate();
  const { me, can } = useSession();
  const qc = useQueryClient();
  const task = useQuery({ queryKey: ["lab", "task", taskId], queryFn: () => call<LabTask>("GET", `/api/lab/tasks/${taskId}`) });
  const methods = useQuery({ queryKey: ["lab", "methods"], queryFn: () => call<Method[]>("GET", "/api/lab/methods") });
  const instruments = useQuery({ queryKey: ["lab", "instruments"], queryFn: () => call<Instrument[]>("GET", "/api/lab/instruments") });
  const [entries, setEntries] = useState<Entry[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  // 补齐计划 2-14: 批量录入用 RevoGrid（可从 Excel 粘贴区域），与配方编辑器同款。
  const entryGrid = useRef<HTMLRevoGridElement>(null);
  const attachments = useQuery({ queryKey: ["lab", "task-attachments", taskId],
    queryFn: () => call<TaskAttachment[]>("GET", `/api/lab/tasks/${taskId}/attachments`) });
  // 补齐计划 3-17：所属 DOE 矩阵总点数 + 同计划任务单（按矩阵顺序导航）。
  const planId = task.data?.doe_plan_id;
  const plan = useQuery({ queryKey: ["lab", "task-plan", planId], enabled: !!planId,
    queryFn: () => call<{ runs: { run_id: number }[] }>("GET", `/api/doe/plans/${planId}`) });
  const siblings = useQuery({ queryKey: ["lab", "task-siblings", planId], enabled: !!planId,
    queryFn: () => call<LabTask[]>("GET", `/api/lab/tasks?doe_plan_id=${planId}`) });
  const nav = planId ? siblingNav(siblings.data ?? [], taskId) : { prev: null, next: null };
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
        {nav.prev && <Button onClick={() => navigate(`/lab/tasks/${nav.prev!.id}`)}>{t("tasks.prev_run", "上一运行")}</Button>}
        {nav.next && <Button onClick={() => navigate(`/lab/tasks/${nav.next!.id}`)}>{t("tasks.next_run", "下一运行")}</Button>}
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
            {/* 补齐计划 3-17：试验点序号 + 所属矩阵总点数 + 回到矩阵总览。 */}
            <Descriptions.Item label={t("tasks.run", "试验点")}>
              {data.run_index ?? "—"}{data.doe_plan_id && (plan.data?.runs.length ?? 0) > 0
                ? ` / ${t("tasks.matrix_total", "共 {{n}} 点", { n: plan.data!.runs.length })}` : ""}
            </Descriptions.Item>
            {data.doe_plan_id && (
              <Descriptions.Item label={t("tasks.plan", "所属 DOE 计划")}>
                <a onClick={() => navigate(`/lab/tasks?plan=${data.doe_plan_id}`)}>{data.doe_plan_id.slice(0, 8)}</a>
              </Descriptions.Item>
            )}
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
              <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
                {t("measure.grid_help", "批量录入：可从 Excel 直接粘贴数值区域；方法列填方法代码，仪器列填资产编号，备注列填文字。服务端校验方法与仪器校准，未知方法将被拒绝。")}
              </Typography.Paragraph>
              <div className="fx-grid" style={{ height: Math.min(400, 48 + entries.length * 34) }}>
                <RevoGrid ref={entryGrid} source={entries} range resize theme="compact"
                  columns={[
                    { prop: "metric", name: t("tasks.metric", "指标"), size: 160, readonly: true },
                    { prop: "value", name: t("measure.value", "数值"), size: 120, columnType: "numeric" },
                    { prop: "method_code", name: t("measure.method", "方法"), size: 160 },
                    { prop: "instrument_id", name: t("measure.instrument", "仪器"), size: 160 },
                    { prop: "note", name: t("measure.note", "备注"), size: 200 },
                  ]}
                  onAfteredit={() => void (async () => {
                    const data = (await entryGrid.current?.getSource()) as Entry[] | undefined;
                    if (data) setEntries(data.map((e) => ({ ...e, value: e.value == null || (e.value as unknown) === "" ? null : Number(e.value) })));
                  })()} />
              </div>
              <Button type="primary" style={{ marginTop: 8 }} loading={act.isPending}
                onClick={async () => {
                  const data = ((await entryGrid.current?.getSource()) as Entry[] | undefined) ?? entries;
                  const measurements = data.filter((e) => e.value != null && (e.value as unknown) !== "").map((e) => ({
                    metric: e.metric, value: Number(e.value), method_code: e.method_code || undefined,
                    instrument_id: e.instrument_id || undefined, note: e.note || undefined }));
                  await act.mutateAsync({ path: `/api/lab/tasks/${taskId}/measurements`, body: { measurements } });
                  setEntries([]);
                }}>{t("measure.record", "保存录入")}</Button>
            </>
          )}
        </ProCard>
      </ProCard>
      {/* 补齐计划 2-14: 任务附件（原始记录 / 图谱 / 现场照片） */}
      <ProCard bordered style={{ marginTop: 16 }} title={t("tasks.attachments", "任务附件")}
        extra={<Upload showUploadList={false} customRequest={async ({ file }) => {
          try {
            const fd = new FormData();
            fd.append("file", file as File);
            await call("POST", `/api/lab/tasks/${taskId}/attachments`, fd);
            message.success(t("tasks.uploaded", "附件已上传"));
            void qc.invalidateQueries({ queryKey: ["lab", "task-attachments", taskId] });
          } catch (e) { message.error(errorText(e)); }
        }}>
          <Button icon={<UploadOutlined />}>{t("tasks.upload", "上传附件")}</Button>
        </Upload>}>
        <Table size="small" pagination={false} rowKey="id" dataSource={attachments.data ?? []} columns={[
          { title: t("tasks.file", "文件名"), dataIndex: "filename" },
          { title: t("tasks.size", "大小"), dataIndex: "size_bytes", width: 120, render: (v: number) => `${(v / 1024).toFixed(1)} KB` },
          { title: t("tasks.uploaded_at", "上传时间"), dataIndex: "created_at", width: 180, render: (v: string | null) => v?.slice(0, 19).replace("T", " ") ?? "—" },
          { title: t("common.actions", "操作"), width: 140, render: (_d, r: TaskAttachment) => [
            <a key="dl" onClick={async () => {
              try {
                const url = await fetchBlobUrl(`/api/lab/tasks/${taskId}/attachments/${r.id}/download`);
                window.open(url, "_blank", "noopener");
              } catch (e) { message.error(errorText(e)); }
            }}>{t("tasks.download", "下载")}</a>,
            can("doe.write") && <a key="del" onClick={() => modal.confirm({
              title: t("tasks.del_att", "删除附件"), content: r.filename,
              onOk: async () => {
                await call("DELETE", `/api/lab/tasks/${taskId}/attachments/${r.id}`);
                void qc.invalidateQueries({ queryKey: ["lab", "task-attachments", taskId] });
              },
            })}>{t("common.delete", "删除")}</a>,
          ] },
        ]} />
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

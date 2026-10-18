import { PageContainer, ProTable } from "@ant-design/pro-components";
import { App, Button, DatePicker, Form, Input, InputNumber, Modal, Switch, Tabs } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useSession } from "@/app/session";
import { errorText } from "@/shared/feedback";
import { StatusTag } from "@/shared/tags";
import type { Instrument, Method } from "./types";

/** 方法库与仪器台账 (方案 §5.3.1). */
export default function MethodsPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { can } = useSession();
  const qc = useQueryClient();
  const methods = useQuery({ queryKey: ["lab", "methods"], queryFn: () => call<Method[]>("GET", "/api/lab/methods") });
  const instruments = useQuery({ queryKey: ["lab", "instruments"], queryFn: () => call<Instrument[]>("GET", "/api/lab/instruments") });
  const [editM, setEditM] = useState<Partial<Method> | null>(null);
  const [editI, setEditI] = useState<Partial<Instrument> | null>(null);
  const [mForm] = Form.useForm();
  const [iForm] = Form.useForm();
  const manage = can("lab.review");
  const done = () => { void qc.invalidateQueries({ queryKey: ["lab"] }); message.success(t("common.saved", "已保存")); };
  const saveM = useMutation({
    mutationFn: (v: Record<string, unknown>) => editM?.id ? call("PUT", `/api/lab/methods/${editM.id}`, v) : call("POST", "/api/lab/methods", v),
    onSuccess: () => { setEditM(null); done(); }, onError: (e) => message.error(errorText(e)),
  });
  const saveI = useMutation({
    mutationFn: (v: Record<string, unknown>) => editI?.id ? call("PUT", `/api/lab/instruments/${editI.id}`, v) : call("POST", "/api/lab/instruments", v),
    onSuccess: () => { setEditI(null); done(); }, onError: (e) => message.error(errorText(e)),
  });
  const seed = useMutation({ mutationFn: () => call<{ added: number }>("POST", "/api/lab/methods/seed"),
    onSuccess: (o) => { message.success(t("methods.seeded", "新增 {{n}} 项预置方法", { n: o.added })); void qc.invalidateQueries({ queryKey: ["lab"] }); } });

  return (
    <PageContainer>
      <Tabs items={[
        { key: "methods", label: t("methods.title", "检测方法库"), children: (
          <ProTable<Method> rowKey="id" search={false} dataSource={methods.data} loading={methods.isLoading} options={{ reload: () => void methods.refetch() }}
            toolBarRender={() => manage ? [
              <Button key="seed" onClick={() => seed.mutate()}>{t("methods.seed", "补齐预置方法")}</Button>,
              <Button key="new" type="primary" onClick={() => { mForm.resetFields(); setEditM({}); }}>{t("methods.new", "新增方法")}</Button>] : []}
            columns={[
              { title: t("methods.code", "标准号"), dataIndex: "code" },
              { title: t("methods.name", "名称"), dataIndex: "name_zh" },
              { title: t("tasks.metric", "指标"), dataIndex: "metric" },
              { title: t("spec.unit", "单位"), dataIndex: "unit", width: 80 },
              { title: t("methods.decimals", "修约位数"), render: (_d, r) => r.rounding?.decimals ?? "—", width: 90 },
              { title: t("methods.range", "有效范围"), render: (_d, r) => `${r.valid_min ?? "-∞"} ～ ${r.valid_max ?? "+∞"}` },
              { title: t("methods.active", "启用"), dataIndex: "active", render: (_d, r) => (r.active ? "✓" : "—"), width: 70 },
              { title: t("common.actions", "操作"), valueType: "option", render: (_d, r) => manage ? [<a key="e" onClick={() => {
                mForm.setFieldsValue({ ...r, decimals: r.rounding?.decimals }); setEditM(r); }}>{t("common.edit", "编辑")}</a>] : [] },
            ]} />
        ) },
        { key: "instruments", label: t("instruments.title", "仪器台账"), children: (
          <ProTable<Instrument> rowKey="id" search={false} dataSource={instruments.data} loading={instruments.isLoading} options={{ reload: () => void instruments.refetch() }}
            toolBarRender={() => manage ? [<Button key="new" type="primary" onClick={() => { iForm.resetFields(); setEditI({}); }}>{t("instruments.new", "新增仪器")}</Button>] : []}
            columns={[
              { title: t("instruments.asset", "资产编号"), dataIndex: "asset_no" },
              { title: t("instruments.name", "名称"), dataIndex: "name" },
              { title: t("instruments.model", "型号"), dataIndex: "model" },
              { title: t("instruments.due", "校准有效期至"), dataIndex: "calibration_due", valueType: "date" },
              { title: t("instruments.state", "校准状态"), dataIndex: "calibration_state", render: (_d, r) => <StatusTag status={r.calibration_state} /> },
              { title: t("common.actions", "操作"), valueType: "option", render: (_d, r) => manage ? [<a key="e" onClick={() => {
                iForm.setFieldsValue({ ...r, calibrated_at: r.calibrated_at ? dayjs(r.calibrated_at) : null, calibration_due: r.calibration_due ? dayjs(r.calibration_due) : null });
                setEditI(r); }}>{t("common.edit", "编辑")}</a>] : [] },
            ]} />
        ) },
      ]} />
      <Modal open={!!editM} title={t("methods.title", "检测方法")} onCancel={() => setEditM(null)} confirmLoading={saveM.isPending} destroyOnHidden
        onOk={async () => {
          const v = await mForm.validateFields();
          saveM.mutate({ ...v, rounding: v.decimals == null ? {} : { decimals: v.decimals } });
        }}>
        <Form form={mForm} layout="vertical" initialValues={{ active: true }}>
          <Form.Item name="code" label={t("methods.code", "标准号")} rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="name_zh" label={t("methods.name", "名称")}><Input /></Form.Item>
          <Form.Item name="name_en" label="English"><Input /></Form.Item>
          <Form.Item name="metric" label={t("tasks.metric", "指标")}><Input /></Form.Item>
          <Form.Item name="unit" label={t("spec.unit", "单位")}><Input /></Form.Item>
          <Form.Item name="decimals" label={t("methods.decimals", "修约位数（GB/T 8170）")}><InputNumber min={0} max={6} /></Form.Item>
          <Form.Item name="valid_min" label={t("methods.min", "有效范围下限")}><InputNumber /></Form.Item>
          <Form.Item name="valid_max" label={t("methods.max", "有效范围上限")}><InputNumber /></Form.Item>
          <Form.Item name="active" label={t("methods.active", "启用")} valuePropName="checked"><Switch /></Form.Item>
        </Form>
      </Modal>
      <Modal open={!!editI} title={t("instruments.title", "仪器")} onCancel={() => setEditI(null)} confirmLoading={saveI.isPending} destroyOnHidden
        onOk={async () => {
          const v = await iForm.validateFields();
          saveI.mutate({ ...v, calibrated_at: v.calibrated_at?.toISOString() ?? null, calibration_due: v.calibration_due?.toISOString() ?? null });
        }}>
        <Form form={iForm} layout="vertical" initialValues={{ active: true }}>
          <Form.Item name="asset_no" label={t("instruments.asset", "资产编号")} rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="name" label={t("instruments.name", "名称")}><Input /></Form.Item>
          <Form.Item name="model" label={t("instruments.model", "型号")}><Input /></Form.Item>
          <Form.Item name="location" label={t("instruments.location", "位置")}><Input /></Form.Item>
          <Form.Item name="calibrated_at" label={t("instruments.calibrated", "最近校准")}><DatePicker /></Form.Item>
          <Form.Item name="calibration_due" label={t("instruments.due", "校准有效期至")}><DatePicker /></Form.Item>
          <Form.Item name="active" label={t("methods.active", "启用")} valuePropName="checked"><Switch /></Form.Item>
        </Form>
      </Modal>
    </PageContainer>
  );
}

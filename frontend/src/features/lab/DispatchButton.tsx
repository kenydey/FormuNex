import { App, Button, Form, Modal, Select } from "antd";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useUi } from "@/app/store";
import { errorText } from "@/shared/feedback";
import type { ProjectDetail } from "@/features/spec/types";

interface Method { code: string; metric: string; name_zh: string }

/** Dispatch a stored DOE plan as lab tasks (one per run). */
export function DispatchButton({ planId }: { planId: string }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const projectId = useUi((s) => s.projectId);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const project = useQuery({ queryKey: ["project", projectId], enabled: !!projectId && open,
    queryFn: () => call<ProjectDetail>("GET", `/api/projects/${projectId}`) });
  const methods = useQuery({ queryKey: ["lab", "methods"], enabled: open, queryFn: () => call<Method[]>("GET", "/api/lab/methods") });
  const metricOptions = Array.from(new Set([
    ...(project.data?.workspace.requirement?.objectives ?? []).map((o) => o.metric),
    ...(project.data?.workspace.spec_contract?.acceptance ?? []).map((a) => a.metric),
    ...(methods.data ?? []).map((m) => m.metric),
  ].filter(Boolean))).map((m) => ({ value: m, label: m }));
  const dispatch = useMutation({
    mutationFn: (v: { metrics: string[] }) => call<{ created: unknown[]; skipped: unknown[] }>("POST", "/api/lab/tasks/dispatch", {
      doe_plan_id: planId, metrics: v.metrics, project_id: projectId,
      domain: project.data?.workspace.requirement?.domain ?? "anticorrosion_coating",
    }),
    onSuccess: (out) => {
      message.success(t("doe.dispatched", "已生成 {{n}} 张任务单（跳过 {{s}}）", { n: out.created.length, s: out.skipped.length }));
      setOpen(false);
      navigate(`/lab/tasks?plan=${planId}`);
    },
    onError: (e) => message.error(errorText(e)),
  });
  return (
    <>
      <Button type="primary" onClick={() => setOpen(true)}>{t("doe.dispatch", "下发为实验任务")}</Button>
      <Modal open={open} title={t("doe.dispatch", "下发为实验任务")} onCancel={() => setOpen(false)} confirmLoading={dispatch.isPending}
        onOk={async () => dispatch.mutate(await form.validateFields())}>
        <Form form={form} layout="vertical">
          <Form.Item name="metrics" label={t("doe.metrics", "待测指标")} rules={[{ required: true }]}>
            <Select mode="tags" options={metricOptions} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

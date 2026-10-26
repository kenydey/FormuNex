import { App, Form, Input, Modal, Select } from "antd";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { call } from "@/api/client";
import { useUi } from "@/app/store";
import { errorText } from "@/shared/feedback";
import { DOMAINS, type ProjectDetail } from "./types";

/** Shared project-creation dialog (used by the header shortcut and the project list). */
export function ProjectCreateModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t, i18n } = useTranslation();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const setProject = useUi((s) => s.setProject);
  const qc = useQueryClient();
  const [form] = Form.useForm();
  const create = useMutation({
    mutationFn: (v: { title: string; domain: string }) =>
      call<ProjectDetail>("POST", "/api/projects", { title: v.title, requirement: { domain: v.domain, objectives: [] } }),
    onSuccess: (p) => {
      void qc.invalidateQueries({ queryKey: ["projects"] });
      setProject(p.id);
      onClose();
      navigate(`/spec/projects/${p.id}`);
    },
    onError: (e) => message.error(errorText(e)),
  });
  return (
    <Modal open={open} title={t("spec.new_project", "新建项目")} onCancel={onClose} confirmLoading={create.isPending}
      onOk={async () => create.mutate(await form.validateFields())} destroyOnHidden>
      <Form form={form} layout="vertical" initialValues={{ domain: "anticorrosion_coating" }}>
        <Form.Item name="title" label={t("spec.project", "项目")} rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item name="domain" label={t("spec.domain", "产品领域")}>
          <Select options={DOMAINS.map((d) => ({ value: d[0], label: d[i18n.language === "en-US" ? 2 : 1] }))} />
        </Form.Item>
      </Form>
    </Modal>
  );
}

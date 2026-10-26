import { App, Form, Input, Modal } from "antd";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { errorText } from "@/shared/feedback";

export interface ProjectRef { id: string; title: string }

/** Rename a project (title only; the workspace is untouched). */
export function ProjectRenameModal({ open, onClose, project }: { open: boolean; onClose: () => void; project: ProjectRef | null }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const [form] = Form.useForm();
  const rename = useMutation({
    mutationFn: (v: { title: string }) => call("PUT", `/api/projects/${project?.id}`, { title: v.title }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["projects"] });
      void qc.invalidateQueries({ queryKey: ["project", project?.id] });
      message.success(t("spec.renamed", "项目名称已更新"));
      onClose();
    },
    onError: (e) => message.error(errorText(e)),
  });
  return (
    <Modal key={project?.id ?? "none"} open={open} title={t("spec.rename_project", "编辑项目")} onCancel={onClose}
      confirmLoading={rename.isPending} onOk={async () => rename.mutate(await form.validateFields())} destroyOnHidden>
      <Form form={form} layout="vertical" initialValues={{ title: project?.title ?? "" }}>
        <Form.Item name="title" label={t("spec.project", "项目")} rules={[{ required: true }]}><Input /></Form.Item>
      </Form>
    </Modal>
  );
}

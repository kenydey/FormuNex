import { App, Modal, Radio, Typography } from "antd";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { errorText } from "@/shared/feedback";
import type { ProjectRef } from "./ProjectRenameModal";

/**
 * Delete a project. Business data (DOE campaigns, experiments, measurements,
 * attachments) is always removed; the knowledge base follows the choice below
 * (backend default is "delete"). The project row itself is soft-deleted.
 */
export function ProjectDeleteModal({ open, onClose, project, onDeleted }: {
  open: boolean; onClose: () => void; project: ProjectRef | null; onDeleted?: (id: string) => void;
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const [knowledge, setKnowledge] = useState<"delete" | "global">("delete");
  const remove = useMutation({
    mutationFn: () => call("DELETE", `/api/projects/${project?.id}?knowledge=${knowledge}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["projects"] });
      message.success(t("spec.deleted", "项目已删除"));
      if (project) onDeleted?.(project.id);
      onClose();
    },
    onError: (e) => message.error(errorText(e)),
  });
  return (
    <Modal open={open} title={t("spec.delete_project", "删除项目")} onCancel={onClose} okButtonProps={{ danger: true }}
      confirmLoading={remove.isPending} onOk={() => remove.mutate()} destroyOnHidden>
      <Typography.Paragraph>
        {t("spec.delete_confirm", "确定删除项目“{{title}}”吗？DOE 试验、实验、测量数据将始终删除，不可恢复。", { title: project?.title ?? "" })}
      </Typography.Paragraph>
      <Radio.Group value={knowledge} onChange={(e) => setKnowledge(e.target.value)}>
        <Radio value="delete">{t("spec.delete_kb_delete", "项目知识库一并删除")}</Radio>
        <Radio value="global">{t("spec.delete_kb_global", "项目知识库保留为全局知识库")}</Radio>
      </Radio.Group>
    </Modal>
  );
}

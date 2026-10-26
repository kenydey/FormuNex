import { ProTable } from "@ant-design/pro-components";
import { App, Button, Empty, Popconfirm, Upload } from "antd";
import { UploadOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import dayjs from "dayjs";
import { call, download } from "@/api/client";
import { useSession } from "@/app/session";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { saveToShelf, shelfPath } from "./artifactApi";
import { formatBytes, shelfFilename } from "./lib";
import { useResearch } from "./store";
import type { ExportFile } from "./types";

/** Project export shelf: files saved per project (deep report, uploads), download / delete. */
export function ExportShelf({ projectId }: { projectId: string | null }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { can } = useSession();
  const qc = useQueryClient();
  const report = useResearch((s) => s.report);
  const key = ["research", "exports", projectId];
  const files = useQuery({ queryKey: key, enabled: !!projectId, queryFn: () => call<ExportFile[]>("GET", shelfPath(projectId!)) });
  const refresh = () => void qc.invalidateQueries({ queryKey: key });
  const onError = (e: unknown) => message.error(errorText(e));
  const remove = useMutation({ mutationFn: (name: string) => call("DELETE", shelfPath(projectId!, name)), onSuccess: refresh, onError });
  const upload = useMutation({
    mutationFn: (f: File) => {
      const fd = new FormData();
      fd.append("file", f);
      return call<ExportFile>("POST", `${shelfPath(projectId!)}/upload`, fd);
    },
    onSuccess: refresh, onError,
  });
  const saveReport = useMutation({
    mutationFn: () => saveToShelf(projectId!, shelfFilename("deep_report", "md"), report!.report_markdown),
    onSuccess: refresh, onError,
  });
  if (!projectId) return <Empty description={t("research.need_project", "请先在顶部选择项目，才能保存到货架或版本库。")} />;
  return (
    <>
      <ErrorAlert error={files.error} />
      <ProTable<ExportFile>
        rowKey="name" search={false} size="small" loading={files.isLoading} dataSource={files.data ?? []} pagination={false}
        options={{ reload: () => void files.refetch() }}
        headerTitle={t("research.shelf.title", "导出货架（项目级落盘）")}
        locale={{ emptyText: t("research.shelf.empty", "货架为空。可保存深度研究报告或上传文件。") }}
        toolBarRender={() => [
          <Button key="r" disabled={!report || !can("project.write")} loading={saveReport.isPending} onClick={() => saveReport.mutate()}>{t("research.shelf.save_report", "保存深度报告")}</Button>,
          <Upload key="u" showUploadList={false} accept=".pdf,.xlsx,.xls,.docx,.pptx,.png,.jpg,.jpeg,.zip,.md,.json,.csv"
            beforeUpload={(f) => { if (!can("project.write")) return false; upload.mutate(f); return false; }}>
            <Button icon={<UploadOutlined />} disabled={!can("project.write")} loading={upload.isPending}>{t("research.shelf.upload", "上传文件")}</Button>
          </Upload>,
        ]}
        columns={[
          { title: t("research.shelf.name", "文件"), dataIndex: "name", copyable: true },
          { title: t("research.shelf.size", "大小"), dataIndex: "size", width: 100, render: (_d, f) => formatBytes(f.size) },
          { title: t("research.shelf.updated", "更新时间"), dataIndex: "updated_at", width: 170, render: (_d, f) => dayjs(f.updated_at).format("YYYY-MM-DD HH:mm") },
          { title: t("common.actions", "操作"), valueType: "option", width: 140, render: (_d, f) => [
            <a key="d" onClick={() => void download(shelfPath(projectId, f.name), f.name).catch(onError)}>{t("research.shelf.download", "下载")}</a>,
            <Popconfirm key="x" title={t("common.confirm_delete", "确定删除？")} onConfirm={() => remove.mutate(f.name)}>
              {can("project.write") ? <a>{t("common.delete", "删除")}</a> : <a style={{ color: "rgba(0,0,0,0.25)", cursor: "not-allowed" }}>{t("common.delete", "删除")}</a>}
            </Popconfirm>,
          ] },
        ]}
      />
    </>
  );
}

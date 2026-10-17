import { App, Button, Select, Space, Tooltip } from "antd";
import { FileTextOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ApiError, call } from "@/api/client";
import { useUi } from "@/app/store";
import { errorText } from "@/shared/feedback";
import { postForFile, saveBlob } from "./artifactApi";

export type TechReportKind = "formulation" | "doe" | "optimization";
export type TechReportFormat = "docx" | "pdf" | "html" | "md";
const FORMATS: TechReportFormat[] = ["docx", "pdf", "html", "md"];

/**
 * 技术报告导出 (W3-13): format picker → POST /api/reports/export → file. Exports pass the publication preflight gate;
 * a 409 offers to open the preflight panel of that report kind. Reusable by other pages (``kind`` prop).
 */
export function TechReportExport({ kind, projectId: pid }: { kind: TechReportKind; projectId?: string | null }) {
  const { t } = useTranslation();
  const { message, modal } = App.useApp();
  const navigate = useNavigate();
  const current = useUi((s) => s.projectId);
  const projectId = pid ?? current;
  const [format, setFormat] = useState<TechReportFormat>("docx");
  const [busy, setBusy] = useState(false);
  const caps = useQuery({ queryKey: ["research", "report-caps"], staleTime: 300_000,
    queryFn: () => call<Record<string, unknown>>("GET", "/api/reports/capabilities") });
  const labels: Record<TechReportKind, string> = {
    formulation: t("research.tr.formulation", "配方技术报告"), doe: t("research.tr.doe", "DOE 技术报告"), optimization: t("research.tr.optimization", "优化技术报告"),
  };
  const run = async () => {
    if (!projectId) {
      message.warning(t("research.need_project", "请先在顶部选择项目，才能保存到货架或版本库。"));
      return;
    }
    setBusy(true);
    try {
      const { blob, filename } = await postForFile("/api/reports/export", { kind, format, project_id: projectId }, `${kind}_report.${format}`);
      saveBlob(blob, filename);
      message.success(t("research.tr.done", "已导出 {{f}}", { f: filename }));
    } catch (e) {
      const blocked = e instanceof ApiError && e.status === 409;
      if (blocked) {
        modal.confirm({
          title: t("research.tr.blocked", "发布预检未通过"),
          content: t("research.tr.blocked_body", "存在未处理的阻断项，导出被拒绝。是否打开发布预检处理？"),
          okText: t("research.tr.open_preflight", "打开发布预检"),
          onOk: () => navigate(`/knowledge/research/artifacts?tab=preflight&kind=tech_report_${kind}`),
        });
      } else message.error(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Space.Compact>
      <Select<TechReportFormat> value={format} onChange={setFormat} disabled={busy} style={{ width: 90 }}
        options={FORMATS.map((f) => ({ value: f, label: f.toUpperCase(), disabled: caps.data ? caps.data[f] === false : false }))} />
      <Tooltip title={t("research.tr.tip", "导出{{k}}（经发布预检门禁）", { k: labels[kind] })}>
        <Button icon={<FileTextOutlined />} loading={busy} onClick={() => void run()}>{labels[kind]}</Button>
      </Tooltip>
    </Space.Compact>
  );
}

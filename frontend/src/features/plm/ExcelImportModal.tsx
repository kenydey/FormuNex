import { App, Button, Checkbox, Modal, Space, Tag, Upload } from "antd";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { errorText } from "@/shared/feedback";

interface ImportReport {
  dry_run: boolean; total: number;
  created: { product_code?: string; name?: string }[];
  skipped: { product_code?: string; name?: string; reason?: string }[];
  errors: { row: number; product_code?: string; name?: string; error: string }[];
  warnings: string[];
}

/** G7 Excel import (formulas + companies). Materials already has its own
 * import; this modal covers only the two legs the review found missing. */
export default function ExcelImportModal({ open, onClose, kind }: {
  open: boolean; onClose: () => void; kind: "formulas" | "companies";
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [dryRun, setDryRun] = useState(true);
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<ImportReport | null>(null);
  const label = kind === "formulas" ? "配方" : "公司";

  const submit = async () => {
    if (!file) { message.warning("请先选择 .xlsx 文件"); return; }
    const fd = new FormData();
    fd.append("file", file);
    setBusy(true);
    try {
      const r = await fetch(`/api/plm/import/${kind}?dry_run=${dryRun}`, { method: "POST", body: fd, credentials: "include" });
      const body = await r.json();
      if (!r.ok) throw new Error(typeof body?.detail === "string" ? body.detail : `导入失败 (${r.status})`);
      setReport(body as ImportReport);
      if (!dryRun) {
        message.success(t("common.saved", "已保存"));
        void qc.invalidateQueries({ queryKey: kind === "formulas" ? ["plm", "products"] : ["companies"] });
      }
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} title={`Excel 导入${label}`} destroyOnHidden width={640}
      onCancel={() => { onClose(); setReport(null); setFile(null); }}
      footer={<Space>
        <a href={`/api/plm/import/templates/${kind}`} download>下载{label}模板</a>
        <Checkbox checked={dryRun} onChange={(e) => setDryRun(e.target.checked)}>仅预览（不写入）</Checkbox>
        <Button onClick={() => { onClose(); setReport(null); setFile(null); }}>关闭</Button>
        <Button type="primary" loading={busy} disabled={!file} onClick={() => void submit()}>{dryRun ? "预览" : "确认导入"}</Button>
      </Space>}>
      <Upload accept=".xlsx" maxCount={1} beforeUpload={(f) => { setFile(f); return false; }}
        onRemove={() => setFile(null)}>
        <Button>选择 .xlsx 文件</Button>
      </Upload>
      {file && <div style={{ marginTop: 8 }}>{file.name}</div>}
      {report && <div style={{ marginTop: 16 }}>
        <Space wrap>
          <span>共 {report.total} 行</span>
          <Tag color="green">新增 {report.created.length}</Tag>
          <Tag>跳过 {report.skipped.length}</Tag>
          <Tag color={report.errors.length ? "red" : "green"}>错误 {report.errors.length}</Tag>
          {report.dry_run && <Tag color="gold">预览，未写入</Tag>}
        </Space>
        {report.errors.length > 0 && <ul>
          {report.errors.map((e, i) => <li key={i}>第 {e.row} 行{e.product_code || e.name ? `（${e.product_code || e.name}）` : ""}：{e.error}</li>)}
        </ul>}
        {report.warnings.length > 0 && <ul>
          {report.warnings.map((w, i) => <li key={i}>{w}</li>)}
        </ul>}
      </div>}
    </Modal>
  );
}

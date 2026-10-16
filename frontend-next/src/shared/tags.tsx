import { Tag } from "antd";
import { useTranslation } from "react-i18next";

const SOURCE_COLORS: Record<string, string> = {
  computed: "blue", measured: "green", manual: "purple", template: "default", product: "cyan", company: "cyan",
  batch: "geekblue", legacy: "orange", supplier_sds: "lime", cn_catalog: "blue", pubchem: "gold", inferred: "red",
};

export function SourceBadge({ source }: { source?: string | null }) {
  const { t } = useTranslation();
  if (!source) return null;
  return <Tag color={SOURCE_COLORS[source] ?? "default"}>{t(`source.${source}`, source)}</Tag>;
}

const STATUS_COLORS: Record<string, string> = {
  draft: "default", candidate: "processing", under_review: "warning", released: "success", superseded: "default",
  withdrawn: "error", approved: "success", rejected: "error", pending: "processing", open: "default",
  in_progress: "processing", pending_review: "warning", done: "success", cancelled: "default", unreviewed: "warning",
  reviewed: "success", pass: "success", fail: "error", waived: "warning", not_applicable: "default",
  PASS: "success", FAIL: "error", INCOMPLETE: "warning", gate_failed: "error", ok: "success", overdue: "error",
  due_soon: "warning", unknown: "default", conflict: "error", resolved: "success", active: "processing",
};

export function StatusTag({ status }: { status?: string | null }) {
  const { t } = useTranslation();
  if (!status) return null;
  return <Tag color={STATUS_COLORS[status] ?? "default"}>{t(`status.${status}`, STATUS_ZH[status] ?? status)}</Tag>;
}

export const STATUS_ZH: Record<string, string> = {
  draft: "草稿", candidate: "候选", under_review: "签批中", released: "已发布", superseded: "已替代",
  withdrawn: "已撤回", approved: "已审核", rejected: "已驳回", pending: "待处理", open: "待领取",
  in_progress: "进行中", pending_review: "待复核", done: "已完成", cancelled: "已取消", unreviewed: "待复核",
  reviewed: "已复核", pass: "通过", fail: "未通过", waived: "已豁免", not_applicable: "不适用",
  PASS: "合格", FAIL: "不合格", INCOMPLETE: "未完成", gate_failed: "门禁未通过", ok: "正常", overdue: "已过期",
  due_soon: "即将到期", unknown: "未知", conflict: "冲突", resolved: "已处理", active: "进行中", legacy: "历史迁移",
};

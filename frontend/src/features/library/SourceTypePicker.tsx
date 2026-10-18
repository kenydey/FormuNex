import { Badge, Space, Tag, Tooltip } from "antd";
import { useTranslation } from "react-i18next";
import { sourceStatusColor } from "./logic";
import type { SearchSourceType, SourceStatus } from "./types";

export const SOURCE_TYPES: SearchSourceType[] = ["patents", "surechembl", "literature", "internet", "local", "notebooklm"];

export function useSourceTypeLabel() {
  const { t } = useTranslation();
  return (id: string): string => ({
    patents: t("library.type.patents", "专利"),
    surechembl: t("library.type.surechembl", "专利化学 SureChEMBL"),
    literature: t("library.type.literature", "文献"),
    internet: t("library.type.internet", "互联网"),
    local: t("library.type.local", "本地文件"),
    notebooklm: "NotebookLM",
  }[id] ?? id);
}

/** Search-source multi-select with an availability dot per source (green online / yellow offline seed / red missing). */
export function SourceTypePicker({ value, onChange, status = {}, types = SOURCE_TYPES }: {
  value: SearchSourceType[]; onChange: (v: SearchSourceType[]) => void; status?: Record<string, SourceStatus>; types?: SearchSourceType[];
}) {
  const { t } = useTranslation();
  const label = useSourceTypeLabel();
  const tip = (st?: SourceStatus) => {
    if (!st) return t("library.type.unknown", "状态未知");
    if (st.hint) return st.hint;
    const c = sourceStatusColor(st);
    return c === "success" ? t("library.type.online", "在线可用") : c === "warning" ? t("library.type.offline", "离线种子回退") : t("library.type.missing", "未安装或未配置");
  };
  return (
    <Space wrap size={[4, 8]}>
      {types.map((id) => (
        <Tooltip key={id} title={tip(status[id])}>
          <Tag.CheckableTag checked={value.includes(id)}
            onChange={(on) => onChange(on ? [...value, id] : value.filter((x) => x !== id))}>
            <Badge status={sourceStatusColor(status[id])} /> {label(id)}
          </Tag.CheckableTag>
        </Tooltip>
      ))}
    </Space>
  );
}

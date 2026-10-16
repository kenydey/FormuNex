import { Alert, Collapse, Descriptions, List, Space, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";
import { HazardTag, PictogramRow, SignalWord } from "@/shared/ghs";

export interface Classification {
  rule_set_version: string;
  rule_set_status?: string;
  hazards: { hazard_class: string; category: string; h_codes: string[]; basis: string }[];
  pictograms: { code: string }[];
  signal_word: string | null;
  h_codes: string[];
  p_codes: string[];
  statements?: Record<string, { zh: string; en: string }>;
  data_gaps: { code: string; message: string; severity: string }[];
  unknown_toxicity_pct?: Record<string, number>;
  transport?: { regulated: boolean; un_number?: string; proper_shipping_name_zh?: string; transport_class?: string; packing_group?: string } | null;
  disclosure?: { name: string; cas: string | null; pct: number; hazards: { hazard_class: string; category: string; h_code: string | null }[] }[];
  inputs_hash?: string;
}

/** GB 30000 result: pictograms, signal word, H statements, data gaps, transport, contributing ingredients. */
export function ComplianceSummary({ cls, compact }: { cls: Classification; compact?: boolean }) {
  const { t, i18n } = useTranslation();
  const text = (c: string) => {
    const s = cls.statements?.[c];
    return (i18n.language === "en-US" ? s?.en : s?.zh) || s?.zh || "";
  };
  const blocking = cls.data_gaps.filter((g) => g.severity === "blocking");
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <PictogramRow codes={cls.pictograms.map((p) => p.code)} size={compact ? 36 : 48} />
      <Space wrap><SignalWord word={cls.signal_word} />{cls.hazards.map((h) => <HazardTag key={h.hazard_class + h.category} hazardClass={h.hazard_class} category={h.category} />)}</Space>
      {!cls.hazards.length && <Typography.Text type="secondary">{t("ghs.not_classified", "根据 GB 30000 计算，不分类")}</Typography.Text>}
      <List size="small" dataSource={cls.h_codes} renderItem={(c) => <List.Item style={{ padding: "2px 0" }}><Tag>{c}</Tag>{text(c)}</List.Item>} />
      {blocking.length > 0 && <Alert type="error" showIcon message={t("ghs.blocking_gaps", "阻断性数据缺口")} description={blocking.map((g) => <div key={g.message}>{g.message}</div>)} />}
      <Collapse size="small" items={[
        { key: "gaps", label: t("ghs.data_gaps", "数据缺口（{{n}}）", { n: cls.data_gaps.length }),
          children: cls.data_gaps.map((g) => <div key={g.code + g.message}><Tag color={g.severity === "blocking" ? "red" : "gold"}>{g.code}</Tag>{g.message}</div>) },
        { key: "contrib", label: t("ghs.contributors", "贡献组分（SDS 第 3 部分）"),
          children: (cls.disclosure ?? []).map((d) => <div key={d.name}>{d.name}（{d.cas ?? "—"}，{d.pct}%）{d.hazards.map((h) => <Tag key={h.hazard_class}>{h.h_code ?? h.hazard_class}</Tag>)}</div>) },
        { key: "transport", label: t("ghs.transport", "运输分类"), children: cls.transport?.regulated
          ? <Descriptions size="small" column={1}>
              <Descriptions.Item label="UN">{cls.transport.un_number}</Descriptions.Item>
              <Descriptions.Item label={t("ghs.shipping_name", "运输名称")}>{cls.transport.proper_shipping_name_zh}</Descriptions.Item>
              <Descriptions.Item label={t("ghs.class", "类别 / 包装类别")}>{cls.transport.transport_class} / {cls.transport.packing_group}</Descriptions.Item>
            </Descriptions>
          : t("ghs.not_regulated", "不受危险货物运输法规管制") },
      ]} />
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {t("ghs.ruleset", "规则集 {{v}}（{{s}}）· 计算结果需专业人员复核", { v: cls.rule_set_version, s: cls.rule_set_status ?? "" })}
      </Typography.Text>
    </Space>
  );
}

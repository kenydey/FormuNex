import { PageContainer, ProTable } from "@ant-design/pro-components";
import { Input, Space, Tag } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { ErrorAlert } from "@/shared/feedback";
import { SourceBadge } from "@/shared/tags";

interface Substance {
  id: string; cas_no: string | null; name_zh: string; name_en: string; hazardous_catalog_no: number | null;
  highly_toxic: boolean | null; precursor_control: boolean | null; explosive_precursor: boolean | null;
  classifications: { hazard_class: string; category: string; h_code: string | null; source: string; is_minimum: boolean; locked: boolean }[];
}

/** 物质与法规数据 (方案 §5.5.1): catalogue, per-source classifications, locks. */
export default function SubstancesPage() {
  const { t } = useTranslation();
  const [q, setQ] = useState("");
  const res = useQuery({ queryKey: ["substances", q], enabled: !!q, queryFn: () => call<Substance[]>("GET", `/api/plm/substances?q=${encodeURIComponent(q)}&limit=100`) });
  return (
    <PageContainer subTitle={t("substances.sub", "《危险化学品目录》分类、GBZ 2.1 限值、管控标识；分类来源优先级：人工锁定 > 供应商 SDS > 目录 > PubChem > 推断")}>
      <Input.Search size="large" enterButton placeholder={t("substances.search", "CAS 号或中英文名称")} onSearch={setQ} style={{ marginBottom: 16 }} />
      <ErrorAlert error={res.error} />
      <ProTable<Substance> rowKey="id" search={false} options={false} loading={res.isFetching} dataSource={res.data}
        columns={[
          { title: "CAS", dataIndex: "cas_no", width: 120 },
          { title: t("substances.name", "名称"), render: (_d, r) => <span>{r.name_zh}<br /><small>{r.name_en}</small></span> },
          { title: t("substances.catalog", "目录序号"), dataIndex: "hazardous_catalog_no", width: 100 },
          { title: t("substances.controls", "管控"), width: 160, render: (_d, r) => <Space wrap>
            {r.highly_toxic && <Tag color="red">{t("substances.highly_toxic", "剧毒")}</Tag>}
            {r.precursor_control && <Tag color="orange">{t("substances.precursor", "易制毒")}</Tag>}
            {r.explosive_precursor && <Tag color="volcano">{t("substances.explosive", "易制爆")}</Tag>}</Space> },
          { title: t("substances.classification", "有效分类"), render: (_d, r) => <Space wrap>{r.classifications.map((c) =>
            <span key={c.hazard_class + c.category}><Tag>{c.hazard_class} {c.category}{c.is_minimum ? "*" : ""}</Tag><SourceBadge source={c.source} />{c.locked && <Tag color="purple">{t("substances.locked", "已锁定")}</Tag>}</span>)}</Space> },
        ]} />
    </PageContainer>
  );
}

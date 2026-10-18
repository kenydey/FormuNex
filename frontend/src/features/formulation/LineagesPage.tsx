import { PageContainer, ProTable } from "@ant-design/pro-components";
import { Empty, Input, Space, Tag } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ErrorAlert } from "@/shared/feedback";
import { fx } from "./api";
import type { Lineage } from "./types";

/** All stored formulation lineages (version chains), searchable by name. */
export default function LineagesPage() {
  const { t } = useTranslation();
  const [q, setQ] = useState("");
  const list = useQuery({ queryKey: ["formulation", "lineages", q], queryFn: () => fx.findLineages(q, "", 100) });
  return (
    <PageContainer content={t("formulation.lineages.intro", "每条谱系是一个配方的不可变修订链；打开可看版本图谱与任意两版差异。")}>
      <Input.Search allowClear enterButton style={{ maxWidth: 480, marginBottom: 16 }} placeholder={t("formulation.lineages.search", "按配方名称查找")} onSearch={setQ} />
      <ErrorAlert error={list.error} />
      <ProTable<Lineage> rowKey="lineage_id" search={false} loading={list.isLoading} dataSource={list.data} options={{ reload: () => void list.refetch() }}
        locale={{ emptyText: <Empty /> }} pagination={{ pageSize: 20 }}
        columns={[
          { title: t("formulation.formula_name", "配方名称"), render: (_d, l) => <Link to={`/lab/lineage/${l.lineage_id}`}>{l.versions[l.versions.length - 1]?.name ?? l.lineage_id}</Link> },
          { title: t("formulation.exp.domain", "产品领域"), render: (_d, l) => <Tag>{l.versions[0]?.domain}</Tag> },
          { title: t("formulation.lineages.count", "版本数"), width: 90, render: (_d, l) => l.versions.length },
          { title: t("formulation.lineages.latest", "最新修改"), ellipsis: true, render: (_d, l) => { const v = l.versions[l.versions.length - 1]; return v ? <Space>v{v.version}<span>{v.change_summary}</span></Space> : "—"; } },
          { title: t("common.updated", "更新时间"), width: 170, render: (_d, l) => l.versions[l.versions.length - 1]?.created_at?.slice(0, 16).replace("T", " ") ?? "—" },
        ]} />
    </PageContainer>
  );
}

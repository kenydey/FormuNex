import { PageContainer, ProTable } from "@ant-design/pro-components";
import { Input, Tag } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { ErrorAlert } from "@/shared/feedback";

interface MaterialView { name: string; role: string; origin: string; availability: string; archived: boolean; spec: Record<string, unknown> }

export default function MaterialsPage() {
  const { t } = useTranslation();
  const [q, setQ] = useState("");
  const res = useQuery({ queryKey: ["materials", q], queryFn: () => call<{ total: number; materials: MaterialView[] }>("GET", `/api/materials?limit=200${q ? `&q=${encodeURIComponent(q)}` : ""}`) });
  return (
    <PageContainer>
      <Input.Search enterButton placeholder={t("materials.search", "原料名称 / 牌号")} onSearch={setQ} style={{ marginBottom: 16, maxWidth: 480 }} />
      <ErrorAlert error={res.error} />
      <ProTable<MaterialView> rowKey="name" search={false} loading={res.isLoading} dataSource={res.data?.materials} options={{ reload: () => void res.refetch() }}
        headerTitle={t("materials.total", "共 {{n}} 种", { n: res.data?.total ?? 0 })}
        columns={[
          { title: t("materials.name", "原料"), dataIndex: "name" },
          { title: t("formula.role", "角色"), dataIndex: "role", render: (_d, r) => <Tag>{r.role}</Tag> },
          { title: t("materials.origin", "来源"), dataIndex: "origin" },
          { title: t("materials.availability", "供应"), dataIndex: "availability" },
          { title: "CAS", render: (_d, r) => String(r.spec?.cas_no ?? "—") },
        ]} />
    </PageContainer>
  );
}

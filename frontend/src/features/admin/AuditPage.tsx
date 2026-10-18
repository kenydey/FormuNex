import { PageContainer, ProTable } from "@ant-design/pro-components";
import { Alert, Input, Space, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useSession } from "@/app/session";
import { ErrorAlert, Forbidden } from "@/shared/feedback";

interface AuditRecord { seq: number; ts: string; actor: string; action: string; entity_type: string; entity_id: string; detail: unknown; hash: string }
interface AuditResponse { chain_ok: boolean; chain_rows: number; first_bad_seq: number | null; records: AuditRecord[] }

export default function AuditPage() {
  const { t } = useTranslation();
  const { can } = useSession();
  const [filter, setFilter] = useState({ entity_type: "", entity_id: "" });
  const q = useQuery({
    queryKey: ["audit", filter], enabled: can("audit.read"),
    queryFn: () => call<AuditResponse>("GET", `/api/plm/audit?limit=500${filter.entity_type ? `&entity_type=${encodeURIComponent(filter.entity_type)}` : ""}${filter.entity_id ? `&entity_id=${encodeURIComponent(filter.entity_id)}` : ""}`),
  });
  if (!can("audit.read")) return <Forbidden permission="audit.read" />;
  return (
    <PageContainer>
      <ErrorAlert error={q.error} />
      {q.data && (q.data.chain_ok
        ? <Alert type="success" showIcon style={{ marginBottom: 12 }} message={t("audit.chain_ok", "哈希链完整：{{n}} 条记录全部校验通过", { n: q.data.chain_rows })} />
        : <Alert type="error" showIcon style={{ marginBottom: 12 }} message={t("audit.chain_broken", "哈希链在第 {{seq}} 条处断裂：审计日志可能被篡改", { seq: q.data.first_bad_seq })} />)}
      <Space style={{ marginBottom: 12 }}>
        <Input.Search placeholder={t("audit.entity_type", "对象类型，如 release_package")} allowClear onSearch={(v) => setFilter((f) => ({ ...f, entity_type: v }))} />
        <Input.Search placeholder={t("audit.entity_id", "对象 ID")} allowClear onSearch={(v) => setFilter((f) => ({ ...f, entity_id: v }))} />
      </Space>
      <ProTable<AuditRecord>
        rowKey="seq" search={false} loading={q.isLoading} dataSource={q.data?.records} options={{ reload: () => void q.refetch() }}
        expandable={{ expandedRowRender: (r) => <pre style={{ margin: 0 }}>{JSON.stringify(r.detail, null, 2)}</pre> }}
        columns={[
          { title: "#", dataIndex: "seq", width: 70 },
          { title: t("audit.time", "时间"), dataIndex: "ts", valueType: "dateTime", width: 170 },
          { title: t("audit.actor", "操作人"), dataIndex: "actor", ellipsis: true, width: 160 },
          { title: t("audit.action", "动作"), dataIndex: "action" },
          { title: t("audit.entity", "对象"), render: (_d, r) => `${r.entity_type} · ${r.entity_id}`, ellipsis: true },
          { title: t("audit.hash", "哈希"), dataIndex: "hash", render: (_d, r) => <Typography.Text code>{r.hash.slice(0, 12)}</Typography.Text>, width: 130 },
        ]}
      />
    </PageContainer>
  );
}

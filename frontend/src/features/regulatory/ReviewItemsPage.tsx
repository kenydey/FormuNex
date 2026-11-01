import { PageContainer, ProTable } from "@ant-design/pro-components";
import { App, Input, Select, Space, Tag } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useSession } from "@/app/session";
import { ErrorAlert, Forbidden, errorText } from "@/shared/feedback";

interface ReviewItem {
  id: string;
  kind: string;
  status: string;
  cas_no: string | null;
  name: string;
  detail: Record<string, unknown>;
  release_id: string | null;
  resolved_by: string | null;
  resolved_at: string | null;
}

interface ReviewItemsResponse {
  open_by_kind: Record<string, number>;
  items: ReviewItem[];
}

// W1-9 seed review queue kinds (backend SEED_REVIEW_KINDS): the lab
// ReviewQueuePage only covers measurements — this page is the frontend
// for the regulatory queue, including the stage-1 seed_drift kind.
const KIND_LABEL: Record<string, string> = {
  invalid_cas: "无效 CAS",
  repaired_cas: "CAS 已修复",
  hazard_text: "危害文本待确认",
  duplicate_catalog_entry: "目录重复条目",
  oel_unmatched: "OEL 未匹配（GBZ）",
  eh40_unmatched: "OEL 未匹配（EH40）",
  transport_unlinked: "运输未关联",
  transport_link_missing: "运输关联缺失",
  seed_drift: "种子漂移",
};

/** 法规复核队列 (W1-9 / 阶段 2 SR-071): 种子导入无法自决的项，EHS 在此处理。 */
export default function ReviewItemsPage() {
  const { t } = useTranslation();
  const { message, modal } = App.useApp();
  const { can } = useSession();
  const qc = useQueryClient();
  const [kind, setKind] = useState<string | undefined>(undefined);
  const [status, setStatus] = useState<string | undefined>("open");
  const q = useQuery({
    queryKey: ["plm", "review-items", kind, status],
    enabled: can("regulatory.read"),
    queryFn: () => call<ReviewItemsResponse>("GET",
      `/api/plm/regulatory/review-items${(() => {
        const p = new URLSearchParams();
        if (kind) p.set("kind", kind);
        if (status) p.set("status", status);
        const s = p.toString();
        return s ? `?${s}` : "";
      })()}`),
  });
  const resolve = useMutation({
    mutationFn: (args: { id: string; note: string }) =>
      call("POST", `/api/plm/regulatory/review-items/${args.id}/resolve`, { note: args.note }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["plm", "review-items"] });
      message.success(t("review.resolved", "已标记为已处理"));
    },
    onError: (e) => message.error(errorText(e)),
  });
  if (!can("regulatory.read")) return <Forbidden permission="regulatory.read" />;
  const counts = q.data?.open_by_kind ?? {};
  return (
    <PageContainer subTitle={t("review.regulatory.sub", "种子导入无法自决的项：修源文件后重跑种子可再生，未处理项重跑后保留；无需后续动作的项在此标记已处理")}>
      <ErrorAlert error={q.error} />
      <Space style={{ marginBottom: 16 }} wrap>
        <Select
          placeholder={t("review.kind", "全部类型")}
          allowClear
          value={kind}
          onChange={(v) => setKind(v)}
          style={{ minWidth: 180 }}
          options={Object.keys(KIND_LABEL).map((k) => ({
            value: k,
            label: `${KIND_LABEL[k]}${counts[k] ? ` (${counts[k]})` : ""}`,
          }))}
        />
        <Select
          value={status}
          onChange={(v) => setStatus(v)}
          style={{ minWidth: 120 }}
          options={[
            { value: "open", label: t("review.open", "未处理") },
            { value: "resolved", label: t("review.resolved2", "已处理") },
            { value: undefined, label: t("review.all", "全部") },
          ]}
        />
      </Space>
      <ProTable<ReviewItem>
        rowKey="id" search={false} loading={q.isLoading} dataSource={q.data?.items}
        options={{ reload: () => void q.refetch() }}
        columns={[
          { title: t("review.kindCol", "类型"), dataIndex: "kind",
            render: (_d, r) => <Tag>{KIND_LABEL[r.kind] ?? r.kind}</Tag> },
          { title: t("review.name", "名称"), dataIndex: "name" },
          { title: t("review.cas", "CAS"), dataIndex: "cas_no" },
          { title: t("review.detail", "详情"), dataIndex: "detail",
            render: (_d, r) => JSON.stringify(r.detail ?? {}) },
          { title: t("review.release", "版本"), dataIndex: "release_id" },
          { title: t("review.status", "状态"), dataIndex: "status",
            render: (_d, r) => r.status === "open"
              ? <Tag color="orange">{t("review.open", "未处理")}</Tag>
              : <Tag color="green">{t("review.resolved2", "已处理")}</Tag> },
          { title: t("review.action", "操作"),
            render: (_d, r) => r.status === "open" && can("regulatory.review") ? (
              <a onClick={() => {
                let note = "";
                modal.confirm({
                  title: t("review.resolve", "标记为已处理"),
                  content: <Input.TextArea onChange={(e) => { note = e.target.value; }}
                    placeholder={t("review.note", "处理说明（可选）")} />,
                  onOk: () => resolve.mutateAsync({ id: r.id, note }),
                });
              }}>{t("review.resolve", "标记为已处理")}</a>
            ) : null },
        ]}
      />
    </PageContainer>
  );
}

import { Button, Drawer, Empty, List, Pagination, Space, Tag, Typography } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call, download } from "@/api/client";
import { ErrorAlert } from "@/shared/feedback";

interface RoundItem {
  round: number;
  loop_entry?: { converged?: boolean; engine?: string; rmse_by_metric?: Record<string, number>; loop_message?: string } | null;
  doe_plan?: { plan_id?: string; design?: string; runs?: unknown[] } | null;
  ledger_rows?: { item_id?: string }[];
}
interface RoundsPage { rounds: RoundItem[]; total_rounds: number; page: number; page_size: number; unassociated_ledger: number }

const PAGE = 5;

/** 轮次历史 (legacy ``CampaignRoundsModal``): DOE plan + ledger rows + loop entry per closed-loop round. */
export function RoundsDrawer({ campaignId, open, onClose }: { campaignId: number; open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const q = useQuery({ queryKey: ["workbench", campaignId, "rounds", page], enabled: open,
    queryFn: () => call<RoundsPage>("GET", `/api/experiments/workbench/${campaignId}/rounds?page=${page}&page_size=${PAGE}`) });
  const d = q.data;
  return (
    <Drawer width={640} open={open} onClose={onClose} title={t("workbench.rounds.title", "轮次历史（DOE + 台账）")} destroyOnHidden>
      <ErrorAlert error={q.error} />
      {d && !d.rounds.length && <Empty description={t("workbench.rounds.empty", "暂无轮次记录（尚未触发闭环优化）")} />}
      <List loading={q.isLoading} dataSource={d?.rounds ?? []} rowKey={(r) => String(r.round)}
        renderItem={(r) => (
          <List.Item>
            <Space direction="vertical" style={{ width: "100%" }}>
              <Space>
                <Typography.Text strong>Round {r.round}</Typography.Text>
                {r.loop_entry?.converged && <Tag color="gold">{t("workbench.loop.converged", "已收敛")}</Tag>}
                {r.loop_entry?.engine && <Tag>{r.loop_entry.engine}</Tag>}
              </Space>
              {r.doe_plan ? (
                <Space>
                  <span>{t("workbench.rounds.doe", "DOE：{{d}}（{{n}} 个实验点）", { d: r.doe_plan.design ?? "?", n: r.doe_plan.runs?.length ?? 0 })}</span>
                  {r.doe_plan.plan_id && <Button size="small" icon={<DownloadOutlined />}
                    onClick={() => void download(`/api/doe/${r.doe_plan!.plan_id}/export?format=csv`, `doe_${r.doe_plan!.plan_id}.csv`)}>{t("workbench.export", "导出")}</Button>}
                </Space>
              ) : <Typography.Text type="secondary">{t("workbench.rounds.no_doe", "无 DOE 记录")}</Typography.Text>}
              <span>
                {t("workbench.rounds.rows", "台账 {{n}} 行", { n: r.ledger_rows?.length ?? 0 })}
                {r.loop_entry?.rmse_by_metric && Object.keys(r.loop_entry.rmse_by_metric).length > 0 && (
                  <> · RMSE {Object.entries(r.loop_entry.rmse_by_metric).map(([m, v]) => `${m}=${Number(v).toFixed(2)}`).join(", ")}</>
                )}
              </span>
              {r.loop_entry?.loop_message && <Typography.Text type="secondary">{r.loop_entry.loop_message}</Typography.Text>}
            </Space>
          </List.Item>
        )} />
      {d && d.unassociated_ledger > 0 && <Typography.Text type="secondary">{t("workbench.rounds.unassoc", "另有 {{n}} 条台账未关联到轮次", { n: d.unassociated_ledger })}</Typography.Text>}
      {d && d.total_rounds > PAGE && <Pagination style={{ marginTop: 12 }} current={page} pageSize={PAGE} total={d.total_rounds} onChange={setPage} />}
    </Drawer>
  );
}

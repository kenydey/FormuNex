import { PageContainer, ProTable } from "@ant-design/pro-components";
import { Button, Empty, Input, Segmented, Select, Space, Tag, Tooltip, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useUi } from "@/app/store";
import { ErrorAlert } from "@/shared/feedback";
import { StatusTag } from "@/shared/tags";
import { DOMAINS } from "@/features/spec/types";
import { fx } from "./api";
import type { ExperimentSearchHit, ExperimentSummary } from "./types";

/** 训练实验记录 + 跨批次台账检索 (legacy ExperimentsBrowser).
 * ``tab``/``tabs`` 仅供实验工作记录 hub 使用：hub 以 prop 强制指定 tab 并隐藏页内 tab 条，
 * 因为页内 tab 本用 ``?tab=``，与 hub 的 ``?tab=`` 冲突；直接访问时行为不变。 */
export default function ExperimentsPage({ tab: forcedTab, tabs = true }: { tab?: "records" | "search"; tabs?: boolean } = {}) {
  const { t, i18n } = useTranslation();
  const [params, setParams] = useSearchParams();
  const tab = forcedTab ?? (params.get("tab") === "search" ? "search" : "records");
  return (
    <PageContainer
      content={t("formulation.exp.intro", "浏览已回灌的训练实验记录（QC / 语料审计），或跨批次检索台账行。")}
      {...(tabs ? {
        tabActiveKey: tab,
        onTabChange: (k) => setParams(k === "search" ? { tab: "search" } : {}),
        tabList: [
          { key: "records", tab: t("formulation.exp.records", "训练实验记录") },
          { key: "search", tab: t("formulation.exp.search", "跨批次检索") },
        ],
      } : {})}
    >
      {tab === "records" ? <Records en={i18n.language === "en-US"} /> : <Search />}
    </PageContainer>
  );
}

function Records({ en }: { en: boolean }) {
  const { t } = useTranslation();
  const projectId = useUi((s) => s.projectId);
  const [scope, setScope] = useState<"project" | "all">(projectId ? "project" : "all");
  const [domain, setDomain] = useState<string | undefined>();
  const effScope = projectId ? scope : "all";
  const list = useQuery({
    queryKey: ["formulation", "experiments", effScope, projectId, domain],
    queryFn: () => fx.experiments({ domain, project_id: effScope === "project" ? projectId ?? undefined : undefined, limit: 100 }),
  });
  return (
    <>
      <ErrorAlert error={list.error} />
      <ProTable<ExperimentSummary>
        rowKey="id" search={false} loading={list.isLoading} dataSource={list.data} options={{ reload: () => void list.refetch() }}
        pagination={{ pageSize: 20 }}
        locale={{ emptyText: <Empty description={t("formulation.exp.empty", "暂无实验记录")} /> }}
        headerTitle={<Space wrap>
          <Segmented value={effScope} onChange={(v) => setScope(v as "project" | "all")} options={[
            { value: "project", label: t("formulation.exp.scope_project", "当前项目"), disabled: !projectId },
            { value: "all", label: t("formulation.exp.scope_all", "全部") },
          ]} />
          <Select allowClear style={{ width: 200 }} placeholder={t("formulation.exp.domain", "产品领域")} value={domain} onChange={setDomain}
            options={DOMAINS.map(([v, zh, enName]) => ({ value: v, label: en ? enName : zh }))} />
        </Space>}
        columns={[
          { title: "ID", dataIndex: "id", width: 80, render: (_d, r) => <Link to={`/lab/experiments/${r.id}`}>#{r.id}</Link> },
          { title: t("formulation.exp.label", "标签"), dataIndex: "label", ellipsis: true,
            render: (_d, r) => r.label || <Typography.Text type="secondary">{t("formulation.exp.no_label", "（无标签）")}</Typography.Text> },
          { title: t("formulation.exp.source", "来源"), dataIndex: "source", width: 140, render: (_d, r) => r.source || "—" },
          { title: t("formulation.exp.domain", "产品领域"), dataIndex: "domain", width: 180, render: (_d, r) => <Tag>{r.domain}</Tag> },
          { title: t("formulation.exp.measurements", "测量数"), dataIndex: "measurement_count", width: 100 },
          { title: t("formulation.exp.measured", "实测摘要"), ellipsis: true,
            render: (_d, r) => Object.entries(r.measured || {}).slice(0, 4).map(([k, v]) => `${k}=${v}`).join("；") || "—" },
          { title: t("common.created", "创建时间"), dataIndex: "created_at", width: 170, render: (_d, r) => r.created_at?.slice(0, 16).replace("T", " ") ?? "—" },
        ]}
      />
    </>
  );
}

function Search() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [q, setQ] = useState<string | null>(null);
  const [picked, setPicked] = useState<ExperimentSearchHit[]>([]);
  const hits = useQuery({ queryKey: ["formulation", "exp-search", q], enabled: q !== null, queryFn: () => fx.searchExperiments(q ?? "") });
  const sameCampaign = picked.length === 2 && picked[0].campaign_id === picked[1].campaign_id;
  return (
    <>
      <Input.Search enterButton allowClear style={{ maxWidth: 520, marginBottom: 16 }}
        placeholder={t("formulation.exp.search_ph", "tags / notes / params（空 = 全部行）")}
        loading={hits.isFetching} onSearch={(v) => { setPicked([]); setQ(v.trim()); }} />
      <ErrorAlert error={hits.error} />
      {q === null ? <Empty description={t("formulation.exp.search_hint", "输入关键词后搜索全部 Campaign 的台账行")} /> : (
        <ProTable<ExperimentSearchHit>
          rowKey={(r) => `${r.campaign_id}:${r.row_id}:${r.item_id}`} search={false} options={false} loading={hits.isLoading}
          dataSource={hits.data} pagination={{ pageSize: 20 }}
          locale={{ emptyText: <Empty description={t("formulation.exp.no_hits", "无匹配行")} /> }}
          rowSelection={{
            selectedRowKeys: picked.map((r) => `${r.campaign_id}:${r.row_id}:${r.item_id}`),
            onChange: (_k, rows) => setPicked(rows.slice(-2)),
          }}
          headerTitle={t("formulation.exp.hits", "共 {{n}} 行", { n: hits.data?.length ?? 0 })}
          toolBarRender={() => [
            <Tooltip key="cmp" title={picked.length === 2 && !sameCampaign ? t("formulation.exp.cmp_same", "仅能对比同一 Campaign 的两行") : undefined}>
              <Button disabled={!sameCampaign}
                onClick={() => navigate(`/lab/experiments/workbench/${picked[0].campaign_id}/diff?a=${picked[0].row_id}&b=${picked[1].row_id}`)}>
                {t("formulation.exp.compare", "对比所选两行")}
              </Button>
            </Tooltip>,
          ]}
          columns={[
            { title: t("formulation.exp.row", "台账行"), dataIndex: "row_id", width: 100,
              render: (_d, r) => <Link to={`/lab/experiments/workbench/${r.campaign_id}/rows/${r.row_id}`}>row #{r.row_id}</Link> },
            { title: "Campaign", dataIndex: "campaign_name", render: (_d, r) => r.campaign_name || `campaign ${r.campaign_id}` },
            { title: t("formulation.exp.item", "样品 ID"), dataIndex: "item_id", ellipsis: true, render: (_d, r) => <Typography.Text code>{r.item_id || "—"}</Typography.Text> },
            { title: t("formulation.exp.status", "状态"), dataIndex: "status", width: 110, render: (_d, r) => <StatusTag status={r.status} /> },
            { title: t("formulation.exp.measured", "实测摘要"), ellipsis: true,
              render: (_d, r) => Object.entries(r.measurements || {}).slice(0, 4).map(([k, v]) => `${k}=${String(v)}`).join("；") || "—" },
          ]}
        />
      )}
    </>
  );
}

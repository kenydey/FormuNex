import { PageContainer, ProCard, StatisticCard } from "@ant-design/pro-components";
import { List, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useSession } from "@/app/session";
import { StatusTag } from "@/shared/tags";

interface Task { id: string; task_no: string; status: string; sample_no: string | null; requested_metrics: { metric: string }[] }
interface PendingPackage { id: string; product_code: string; rev: string; current_step: { seq: number; role: string } | null }

export default function DashboardPage() {
  const { t } = useTranslation();
  const { me, can } = useSession();
  const myTasks = useQuery({
    queryKey: ["lab", "tasks", "mine", me?.id],
    queryFn: () => call<Task[]>("GET", `/api/lab/tasks?assignee_id=${me!.id}`),
    enabled: !!me,
  });
  const openTasks = useQuery({ queryKey: ["lab", "tasks", "open"], queryFn: () => call<Task[]>("GET", "/api/lab/tasks?status=open") });
  const review = useQuery({
    queryKey: ["lab", "review-queue"], enabled: can("lab.review"),
    queryFn: () => call<unknown[]>("GET", "/api/lab/review-queue"),
  });
  const signoff = useQuery({
    queryKey: ["plm", "packages", "pending"],
    queryFn: () => call<PendingPackage[]>("GET", "/api/plm/packages?status=under_review"),
  });
  const mine = (signoff.data ?? []).filter((p) => p.current_step && me?.roles.includes(p.current_step.role));
  const active = (myTasks.data ?? []).filter((x) => x.status === "in_progress" || x.status === "open");

  return (
    <PageContainer title={t("dashboard.hello", "你好，{{name}}", { name: me?.display_name || me?.username })}
      subTitle={me?.roles.map((r) => t(`role.${r}`, r)).join(" · ")}>
      <ProCard ghost gutter={16} wrap>
        <StatisticCard colSpan={{ xs: 24, md: 6 }} statistic={{ title: t("dashboard.my_tasks", "我的实验任务"), value: active.length }} />
        <StatisticCard colSpan={{ xs: 24, md: 6 }} statistic={{ title: t("dashboard.open_tasks", "待领取任务"), value: openTasks.data?.length ?? 0 }} />
        <StatisticCard colSpan={{ xs: 24, md: 6 }} statistic={{ title: t("dashboard.review", "待复核测量值"), value: can("lab.review") ? review.data?.length ?? 0 : "—" }} />
        <StatisticCard colSpan={{ xs: 24, md: 6 }} statistic={{ title: t("dashboard.signoff", "待我签批"), value: mine.length }} />
      </ProCard>
      <ProCard gutter={16} ghost style={{ marginTop: 16 }} wrap>
        <ProCard colSpan={{ xs: 24, md: 12 }} title={t("dashboard.my_tasks", "我的实验任务")} bordered>
          <List
            dataSource={active}
            locale={{ emptyText: t("dashboard.no_tasks", "暂无任务") }}
            renderItem={(x) => (
              <List.Item extra={<StatusTag status={x.status} />}>
                <List.Item.Meta
                  title={<Link to={`/lab/tasks/${x.id}`}>{x.task_no}</Link>}
                  description={x.requested_metrics.map((m) => <Tag key={m.metric}>{m.metric}</Tag>)}
                />
              </List.Item>
            )}
          />
        </ProCard>
        <ProCard colSpan={{ xs: 24, md: 12 }} title={t("dashboard.signoff", "待我签批")} bordered>
          <List
            dataSource={mine}
            locale={{ emptyText: t("dashboard.no_signoff", "暂无待签批的发布包") }}
            renderItem={(p) => (
              <List.Item>
                <List.Item.Meta
                  title={<Link to={`/plm/packages/${p.id}`}>{p.product_code} · {p.rev}</Link>}
                  description={<Typography.Text type="secondary">
                    {t("dashboard.step", "第 {{n}} 步：{{role}}", { n: p.current_step?.seq, role: t(`role.${p.current_step?.role}`, p.current_step?.role ?? "") })}
                  </Typography.Text>}
                />
              </List.Item>
            )}
          />
        </ProCard>
      </ProCard>
    </PageContainer>
  );
}

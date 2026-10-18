import { ProLayout } from "@ant-design/pro-components";
import { BulbOutlined, GlobalOutlined, LockOutlined, LogoutOutlined, UserOutlined } from "@ant-design/icons";
import { Badge, Button, Dropdown, Select, Tooltip } from "antd";
import { useQuery } from "@tanstack/react-query";
import { Suspense, useState } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useSession } from "./session";
import { useUi } from "./store";
import { NAV, type NavItem } from "./nav";
import { ChangePasswordModal } from "@/features/auth/ChangePasswordModal";
import { GlobalSearch } from "@/features/knowledge/GlobalSearch";
import { PageSpin } from "@/shared/PageSpin";
import { ErrorBoundary } from "@/features/chat/ErrorBoundary";
import { DegradedBanner, InfraHealthBanner } from "@/features/chat/HealthBanners";
import { TaskCenter } from "@/features/chat/TaskCenter";
import { ApprovalCenters } from "@/features/chat/Approvals";

interface ProjectSummary { id: string; title: string; domain: string }

function toRoutes(items: NavItem[], t: (k: string, d: string) => string, can: (p: string) => boolean): unknown[] {
  return items
    .filter((i) => (!i.permission || can(i.permission)) && (!i.when || i.when()))
    .map((i) => ({ path: i.path, name: t(i.key, i.zh), icon: i.icon, routes: i.children && toRoutes(i.children, t, can) }));
}

export function Shell() {
  const { t, i18n } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const { me, logout, can } = useSession();
  const ui = useUi();
  const [pwOpen, setPwOpen] = useState(false);
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => call<ProjectSummary[]>("GET", "/api/projects") });
  const reviewQueue = useQuery({
    queryKey: ["lab", "review-queue", "count"], enabled: can("lab.review"), refetchInterval: 60_000,
    queryFn: () => call<unknown[]>("GET", "/api/lab/review-queue?limit=1000"),
  });

  return (
    <ProLayout
      title={t("app.title", "方枢 FormuNex")}
      logo="/favicon.png"
      layout="mix"
      fixSiderbar
      location={{ pathname: location.pathname }}
      route={{ path: "/", routes: toRoutes(NAV, t, (p) => can(p)) }}
      menuItemRender={(item, dom) => (item.path ? <Link to={item.path}>{dom}</Link> : dom)}
      onMenuHeaderClick={() => navigate("/dashboard")}
      actionsRender={() => [
        <GlobalSearch key="search" />,
        <Select<string>
          key="project"
          allowClear
          showSearch
          optionFilterProp="label"
          style={{ width: 220 }}
          placeholder={t("shell.project", "选择项目")}
          value={ui.projectId ?? undefined}
          onChange={(v) => ui.setProject(v ?? null)}
          options={(projects.data ?? []).map((p) => ({ value: p.id, label: p.title }))}
        />,
        <TaskCenter key="tasks" />,
        <Tooltip key="todo" title={t("shell.review_queue", "待复核测量值")}>
          <Badge count={reviewQueue.data?.length ?? 0} size="small">
            <Button type="text" onClick={() => navigate("/lab/review")}>{t("shell.todo", "待办")}</Button>
          </Badge>
        </Tooltip>,
        <Tooltip key="theme" title={t("shell.theme", "明暗主题")}>
          <Button type="text" icon={<BulbOutlined />} onClick={() => ui.setTheme(ui.theme === "dark" ? "light" : "dark")} />
        </Tooltip>,
        <Tooltip key="lang" title="中文 / English">
          <Button type="text" icon={<GlobalOutlined />} onClick={() => {
            const next = ui.locale === "zh-CN" ? "en-US" : "zh-CN";
            ui.setLocale(next);
            void i18n.changeLanguage(next);
          }}>{ui.locale === "zh-CN" ? "EN" : "中"}</Button>
        </Tooltip>,
      ]}
      avatarProps={{
        icon: <UserOutlined />,
        title: me?.display_name || me?.username,
        size: "small",
        render: (_p, dom) => (
          <Dropdown menu={{ items: [
            { key: "pw", icon: <LockOutlined />, label: t("shell.change_password", "修改密码"), onClick: () => setPwOpen(true) },
            { key: "logout", icon: <LogoutOutlined />, label: t("shell.logout", "退出登录"),
              onClick: () => void logout().then(() => navigate("/login")) },
          ] }}>{dom}</Dropdown>
        ),
      }}
    >
      <InfraHealthBanner />
      <DegradedBanner />
      <ErrorBoundary resetKey={location.pathname}>
        <Suspense fallback={<PageSpin />}>
          <Outlet />
        </Suspense>
      </ErrorBoundary>
      <ApprovalCenters />
      <ChangePasswordModal open={pwOpen} onClose={() => setPwOpen(false)} />
    </ProLayout>
  );
}

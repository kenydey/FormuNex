import { BulbOutlined, GlobalOutlined, LockOutlined, LogoutOutlined, PlusOutlined, UserOutlined } from "@ant-design/icons";
import { Badge, Button, Dropdown, Select, Space, Tooltip, theme } from "antd";
import { useQuery } from "@tanstack/react-query";
import { Suspense, useState, type CSSProperties } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useSession } from "./session";
import { useUi } from "./store";
import { deriveNav, visibleNav, type NavItem } from "./nav";
import { LeftRail } from "./LeftRail";
import { GroupHome } from "./GroupHome";
import { FunctionSwitcher } from "./FunctionSwitcher";
import { ChangePasswordModal } from "@/features/auth/ChangePasswordModal";
import { GlobalSearch } from "@/features/knowledge/GlobalSearch";
import { ProjectCreateModal } from "@/features/spec/ProjectCreateModal";
import { PageSpin } from "@/shared/PageSpin";
import { ErrorBoundary } from "@/features/chat/ErrorBoundary";
import { DegradedBanner, InfraHealthBanner } from "@/features/chat/HealthBanners";
import { TaskCenter } from "@/features/chat/TaskCenter";
import { ApprovalCenters } from "@/features/chat/Approvals";
import "./shell.css";

interface ProjectSummary { id: string; title: string; domain: string }

/**
 * 自研壳（前端界面重构 2026-10-28 方案 §4）：顶栏 + 左窄栏（一级）+ 右侧工作区（组首页卡片墙 / 功能页切换条）。
 * 顶栏内容（全局检索、项目选择、任务中心、待办、主题、语言、头像）与壳层全局件（审批中心、改密、新建项目、
 * 健康横幅）全部从旧 ProLayout 壳原样迁移；左栏选中、切换条选中全部由当前路由派生（deriveNav）。
 * 组首页不占路由：左栏组按钮跳到该组首个可见功能并携带 location.state.groupHome，卡片点击后清除该状态；
 * 刷新或深链直接进功能页（组首页只是导航辅助，不是可书签页）。
 */
export function Shell() {
  const { t, i18n } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const { me, logout, can } = useSession();
  const ui = useUi();
  const { token } = theme.useToken();
  const [pwOpen, setPwOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => call<ProjectSummary[]>("GET", "/api/projects") });
  const reviewQueue = useQuery({
    queryKey: ["lab", "review-queue", "count"], enabled: can("lab.review"), refetchInterval: 60_000,
    queryFn: () => call<unknown[]>("GET", "/api/lab/review-queue?limit=1000"),
  });

  const visible = visibleNav(can);
  const derived = deriveNav(location.pathname);
  const visibleGroup = visible.find((g) => g.path === derived?.group.path);
  const showHome =
    (location.state as { groupHome?: boolean } | null)?.groupHome === true &&
    !!visibleGroup?.children?.length;

  const goGroup = (g: NavItem) => {
    if (!g.children?.length) {
      navigate(g.path);
      return;
    }
    navigate(g.children[0].path, { state: { groupHome: true } });
  };
  const goLeaf = (leaf: NavItem) => navigate(leaf.path);
  const goHome = () => navigate(location.pathname, { state: { groupHome: true } });
  const goNextGroup = (groupPath: string) => {
    const g = visible.find((x) => x.path === groupPath);
    if (g) goGroup(g);
  };

  const shellStyle = {
    background: token.colorBgContainer,
    "--ia-border": token.colorBorder,
    "--ia-active-bg": token.colorPrimaryBg,
    "--ia-active": token.colorPrimary,
  } as CSSProperties;

  return (
    <div className="ia-shell" style={shellStyle}>
      <header className="ia-header">
        <span className="ia-header-title" onClick={() => navigate("/dashboard")}>
          {t("app.title", "方枢 FormuNex")}
        </span>
        <GlobalSearch />
        <span className="ia-header-spacer" />
        <Space.Compact>
          <Select<string>
            allowClear
            showSearch
            optionFilterProp="label"
            style={{ width: 220 }}
            placeholder={t("shell.project", "选择项目")}
            value={ui.projectId ?? undefined}
            onChange={(v) => ui.setProject(v ?? null)}
            options={(projects.data ?? []).map((p) => ({ value: p.id, label: p.title }))}
          />
          <Tooltip title={t("shell.new_project", "新建项目")}>
            <Button icon={<PlusOutlined />} disabled={!can("project.write")} onClick={() => setCreateOpen(true)} />
          </Tooltip>
        </Space.Compact>
        <TaskCenter />
        <Tooltip title={t("shell.review_queue", "待复核测量值")}>
          {can("lab.review") && <Badge count={reviewQueue.data?.length ?? 0} size="small">
            <Button type="text" onClick={() => navigate("/lab/measurements?tab=review")}>{t("shell.todo", "待办")}</Button>
          </Badge>}
        </Tooltip>
        <Tooltip title={t("shell.theme", "明暗主题")}>
          <Button type="text" icon={<BulbOutlined />} onClick={() => ui.setTheme(ui.theme === "dark" ? "light" : "dark")} />
        </Tooltip>
        <Tooltip title="中文 / English">
          <Button type="text" icon={<GlobalOutlined />} onClick={() => {
            const next = ui.locale === "zh-CN" ? "en-US" : "zh-CN";
            ui.setLocale(next);
            void i18n.changeLanguage(next);
          }}>{ui.locale === "zh-CN" ? "EN" : "中"}</Button>
        </Tooltip>
        <Dropdown menu={{ items: [
          { key: "pw", icon: <LockOutlined />, label: t("shell.change_password", "修改密码"), onClick: () => setPwOpen(true) },
          { key: "logout", icon: <LogoutOutlined />, label: t("shell.logout", "退出登录"),
            onClick: () => void logout().then(() => navigate("/login")) },
        ] }}>
          <Button type="text" icon={<UserOutlined />}>{me?.display_name || me?.username}</Button>
        </Dropdown>
      </header>

      <div className="ia-body">
        <LeftRail
          groups={visible}
          activeGroupPath={visibleGroup?.path ?? null}
          onGroupClick={goGroup}
          onLogoClick={() => navigate("/dashboard")}
        />
        <main className="ia-main">
          <InfraHealthBanner />
          <DegradedBanner />
          <ErrorBoundary resetKey={location.pathname}>
            <Suspense fallback={<PageSpin />}>
              {showHome && visibleGroup?.children ? (
                <GroupHome group={visibleGroup} onSelect={goLeaf} onNextGroup={goNextGroup} />
              ) : (
                <>
                  {visibleGroup?.children && (
                    <FunctionSwitcher
                      group={visibleGroup}
                      leaves={visibleGroup.children}
                      activeLeafPath={derived?.leaf?.path ?? null}
                      onSelect={goLeaf}
                      onHome={goHome}
                    />
                  )}
                  <Outlet />
                </>
              )}
            </Suspense>
          </ErrorBoundary>
        </main>
      </div>

      <ApprovalCenters />
      <ChangePasswordModal open={pwOpen} onClose={() => setPwOpen(false)} />
      <ProjectCreateModal open={createOpen} onClose={() => setCreateOpen(false)} />
    </div>
  );
}

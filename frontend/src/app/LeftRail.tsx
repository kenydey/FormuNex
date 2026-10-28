import { Button, Tooltip } from "antd";
import { useTranslation } from "react-i18next";
import type { NavItem } from "./nav";

/**
 * 左窄栏（方案 §4.1）：只放一级按钮。主功能组在上，系统管理固定在底部；
 * 工作台是独立项，同样以一级按钮呈现。选中态由当前路由派生（见 Shell）。
 */
export function LeftRail({
  groups,
  activeGroupPath,
  onGroupClick,
  onLogoClick,
}: {
  groups: NavItem[];
  activeGroupPath: string | null;
  onGroupClick: (group: NavItem) => void;
  onLogoClick: () => void;
}) {
  const { t } = useTranslation();
  const main = groups.filter((g) => g.path !== "/admin");
  const admin = groups.find((g) => g.path === "/admin");

  const renderButton = (g: NavItem) => {
    const active = g.path === activeGroupPath;
    return (
      <Tooltip key={g.path} title={t(`${g.key}.desc`, g.desc ?? g.zh)} placement="right">
        <Button
          type="text"
          data-testid={`rail-${g.key}`}
          className={`ia-rail-btn${active ? " active" : ""}`}
          icon={g.icon}
          onClick={() => onGroupClick(g)}
        >
          <span className="ia-rail-label">{t(g.key, g.zh)}</span>
        </Button>
      </Tooltip>
    );
  };

  return (
    <nav className="ia-rail" aria-label={t("ia.rail", "功能导航")}>
      <button type="button" className="ia-rail-logo" onClick={onLogoClick} title={t("app.title", "方枢 FormuNex")}>
        <img src="/favicon.png" alt="" />
      </button>
      <div className="ia-rail-main">{main.map(renderButton)}</div>
      {admin && (
        <div className="ia-rail-bottom">
          <div className="ia-rail-divider" />
          {renderButton(admin)}
        </div>
      )}
    </nav>
  );
}

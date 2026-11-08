import { Card, Col, Row, Typography } from "antd";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router-dom";
import { useSession } from "@/app/session";
import { visibleNav } from "@/app/nav";

/**
 * 工作台新手路径（方案 §4.2，已确认要做）：按 §2 顺序列出 8 步，每步跳转到对应组。
 * 工作台一步链向 /dashboard；其余组链向该组首个可见功能并携带组首页标记（点卡片墙进入）。
 * 只做附加段，不动 DashboardPage 现有查询与列表逻辑。
 * P2-E8: 组首页标记用 ?home=1 查询参数（location.state 刷新即丢）。
 * P3-E9: 步数随权限变化——visibleNav 只列出当前用户可见的组，步数不是固定的 8。
 */
export function NovicePath() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const { can } = useSession();
  const groups = visibleNav(can);

  const go = (g: (typeof groups)[number]) => {
    if (!g.children?.length) {
      // P3-E8: the dashboard step has no children; when already on
      // /dashboard there is nowhere to go — scroll to top instead of
      // a no-op navigation (and render muted, see below).
      if (g.path === location.pathname) window.scrollTo({ top: 0, behavior: "smooth" });
      else navigate(g.path);
      return;
    }
    navigate(`${g.children[0].path}?home=1`);
  };

  return (
    <div data-testid="novice-path">
      <Typography.Title level={5} style={{ marginBottom: 12 }}>
        {t("ia.novice.title", "新手路径：跟着顺序走一遍")}
      </Typography.Title>
      <Row gutter={[12, 12]}>
        {groups.map((g, i) => {
          const here = !g.children?.length && g.path === location.pathname;
          return (
          <Col key={g.path} xs={24} sm={12} md={8} lg={6}>
            <Card
              hoverable={!here}
              data-testid={`novice-step-${g.key}`}
              onClick={() => go(g)}
              className="ia-group-card"
              style={here ? { opacity: 0.55, cursor: "default" } : undefined}
            >
              <Typography.Text type="secondary">{t("ia.novice.step", "第 {{n}} 步", { n: i + 1 })}</Typography.Text>
              <div className="ia-group-card-icon">{g.icon}</div>
              <Typography.Text strong>{t(g.key, g.zh)}</Typography.Text>
              <Typography.Paragraph type="secondary" className="ia-group-card-desc">
                {t(`${g.key}.desc`, g.desc ?? g.zh)}
              </Typography.Paragraph>
            </Card>
          </Col>
          );
        })}
      </Row>
    </div>
  );
}

import { HomeOutlined } from "@ant-design/icons";
import { Breadcrumb, Button, Space } from "antd";
import { useTranslation } from "react-i18next";
import type { NavItem } from "./nav";

/**
 * 功能页顶部切换条（方案 §4.1）：同组二级功能横向滚动切换 + 返回组首页 + 面包屑。
 * 详情页（无精确叶项匹配）仍显示同组切换条，只是不高亮任何一项。
 */
export function FunctionSwitcher({
  group,
  leaves,
  activeLeafPath,
  onSelect,
  onHome,
}: {
  group: NavItem;
  leaves: NavItem[];
  activeLeafPath: string | null;
  onSelect: (leaf: NavItem) => void;
  onHome: () => void;
}) {
  const { t } = useTranslation();
  const active = leaves.find((l) => l.path === activeLeafPath);

  return (
    <div className="ia-switcher" data-testid="function-switcher">
      <Breadcrumb
        className="ia-breadcrumb"
        items={[
          { title: t(group.key, group.zh) },
          ...(active ? [{ title: t(active.key, active.zh) }] : []),
        ]}
      />
      <Space className="ia-switcher-row" wrap={false}>
        <Button
          type="text"
          icon={<HomeOutlined />}
          data-testid="switcher-home"
          onClick={onHome}
          title={t("ia.group_home", "组首页")}
        >
          {t("ia.group_home", "组首页")}
        </Button>
        <span className="ia-switcher-divider" />
        {leaves.map((l) => (
          <Button
            key={l.path}
            type={l.path === activeLeafPath ? "primary" : "text"}
            data-testid={`switcher-${l.key}`}
            onClick={() => onSelect(l)}
            title={t(`${l.key}.desc`, l.desc ?? l.zh)}
          >
            {t(l.key, l.zh)}
          </Button>
        ))}
      </Space>
    </div>
  );
}

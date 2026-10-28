import { ArrowRightOutlined } from "@ant-design/icons";
import { Button, Card, Typography } from "antd";
import { useTranslation } from "react-i18next";
import type { NavItem } from "./nav";

/** 资料检索卡片墙的分组顺序与标题（方案 §4.1）。 */
const DATA_SUBGROUPS = [
  { id: "search", zh: "检索类" },
  { id: "qa", zh: "问答类" },
  { id: "research", zh: "研究类" },
  { id: "knowledge", zh: "知识类" },
] as const;

/** 组首页“下一步”提示：有 toGroup 的跳到下一组的组首页，否则只作文字提示。 */
const NEXT_HINTS: Record<string, { toGroup?: string; key: string; zh: string }> = {
  "/data": { toGroup: "/spec", key: "ia.next.data", zh: "查到资料后，去研发需求立项" },
  "/spec": { toGroup: "/lab", key: "ia.next.spec", zh: "立项后，去配方与实验开始设计" },
  "/lab": { toGroup: "/cockpit", key: "ia.next.lab", zh: "有了实验数据，去寻优与分析找最优配方" },
  "/cockpit": { toGroup: "/product", key: "ia.next.cockpit", zh: "寻优定型后，去产品管理建产品并生成文档" },
  "/product": { key: "ia.next.product", zh: "定型后在这里生成 SDS / 标签并发布" },
  "/materials": { key: "ia.next.materials", zh: "原料与物质数据贯穿配方与产品的全过程" },
};

/**
 * 组首页卡片墙（方案 §4.1）：二级功能以卡片呈现（图标 + 名称 + 一句话说明），点卡片进入功能页。
 * 资料检索 11 项按分组呈现；其余组平铺。下一步提示只提示、不强制。
 */
export function GroupHome({
  group,
  onSelect,
  onNextGroup,
}: {
  group: NavItem;
  onSelect: (leaf: NavItem) => void;
  onNextGroup: (groupPath: string) => void;
}) {
  const { t } = useTranslation();
  const leaves = group.children ?? [];

  const renderCard = (leaf: NavItem) => (
    <Card
      key={leaf.path}
      hoverable
      data-testid={`nav-card-${leaf.key}`}
      onClick={() => onSelect(leaf)}
      className="ia-group-card"
    >
      <div className="ia-group-card-icon">{leaf.icon}</div>
      <Typography.Text strong>{t(leaf.key, leaf.zh)}</Typography.Text>
      <Typography.Paragraph type="secondary" className="ia-group-card-desc">
        {t(`${leaf.key}.desc`, leaf.desc ?? leaf.zh)}
      </Typography.Paragraph>
    </Card>
  );

  const sections =
    group.path === "/data"
      ? DATA_SUBGROUPS.map((s) => ({ ...s, leaves: leaves.filter((l) => l.sub === s.id) })).filter(
          (s) => s.leaves.length > 0,
        )
      : [{ id: "", zh: "", leaves }];

  const next = NEXT_HINTS[group.path];

  return (
    <div className="ia-group-home" data-testid="group-home">
      <div className="ia-group-home-header">
        <span className="ia-group-home-icon">{group.icon}</span>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>
            {t(group.key, group.zh)}
          </Typography.Title>
          <Typography.Text type="secondary">{t(`${group.key}.desc`, group.desc ?? group.zh)}</Typography.Text>
        </div>
      </div>
      {sections.map((s) => (
        <section key={s.id || "all"} className="ia-group-section">
          {s.zh && (
            <Typography.Title level={5} className="ia-group-section-title">
              {t(`ia.sub.${s.id}`, s.zh)}
            </Typography.Title>
          )}
          <div className="ia-group-grid">{s.leaves.map(renderCard)}</div>
        </section>
      ))}
      {next && (
        <div className="ia-group-next">
          <Typography.Text type="secondary">{t("ia.next_label", "下一步：")}</Typography.Text>{" "}
          <Typography.Text>{t(next.key, next.zh)}</Typography.Text>{" "}
          {next.toGroup && (
            <Button type="link" size="small" onClick={() => onNextGroup(next.toGroup!)}>
              {t("ia.next_go", "去看看")} <ArrowRightOutlined />
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

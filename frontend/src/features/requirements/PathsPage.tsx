import { BookOutlined, ExperimentOutlined, SwapOutlined } from "@ant-design/icons";
import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Alert, Button, Col, Row, Space, Steps, Tag, Typography, theme } from "antd";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { NAV } from "@/app/nav";
import { useUi } from "@/app/store";
import { PATHS, flattenPaths, resolveStep, type PathDef, type PathId } from "./paths";

const ICONS: Record<PathId, React.ReactNode> = { formula: <ExperimentOutlined />, substitute: <SwapOutlined />, knowledge: <BookOutlined /> };

function pathText(t: TFunction, id: PathId): { title: string; desc: string } {
  switch (id) {
    case "formula": return { title: t("requirements.paths.formula", "新配方开发"), desc: t("requirements.paths.formula_desc", "需求 → 研究 / 推荐 → DOE → 台账闭环") };
    case "substitute": return { title: t("requirements.paths.substitute", "材料替代"), desc: t("requirements.paths.substitute_desc", "缺货 / 涨价 → 候选 → 验证实验") };
    default: return { title: t("requirements.paths.knowledge", "项目知识沉淀"), desc: t("requirements.paths.knowledge_desc", "资料入库 → Wiki / 图谱 → 可追溯问答") };
  }
}

function stepText(t: TFunction, id: string): string {
  switch (id) {
    case "requirement": return t("requirements.paths.s.requirement", "技术需求与规格契约");
    case "recommend": return t("requirements.paths.s.recommend", "推荐配方");
    case "doe": return t("requirements.paths.s.doe", "DOE 设计");
    case "ledger": return t("requirements.paths.s.ledger", "实验台账");
    case "loop": return t("requirements.paths.s.loop", "自驱动闭环 / 寻优");
    case "materials": return t("requirements.paths.s.materials", "材料库");
    case "substitute": return t("requirements.paths.s.substitute", "结构替代候选");
    case "sources": return t("requirements.paths.s.sources", "项目资料");
    case "wiki": return t("requirements.paths.s.wiki", "Wiki");
    case "quality": return t("requirements.paths.s.quality", "质量运营");
    case "reports": return t("requirements.paths.s.reports", "卷宗报告");
    default: return id;
  }
}

/** 主路径向导 (legacy PathWizard): pick a journey, then open each step's page. */
export default function PathsPage() {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const navigate = useNavigate();
  const projectId = useUi((s) => s.projectId);
  const [active, setActive] = useState<PathId | null>("formula");
  const known = useMemo(() => flattenPaths(NAV), []);
  const current = PATHS.find((p) => p.id === active) ?? null;
  const blocked = (p: PathDef) => !!p.needsProject && !projectId;

  return (
    <PageContainer subTitle={t("requirements.paths.sub", "按目标选择主路径，逐步打开对应页面；不引入新的计算引擎。")}>
      <Row gutter={[16, 16]}>
        {PATHS.map((p) => {
          const txt = pathText(t, p.id);
          const on = p.id === active;
          return (
            <Col key={p.id} xs={24} md={8}>
              <ProCard hoverable bordered data-testid={`path-card-${p.id}`} onClick={() => setActive(on ? null : p.id)}
                style={on ? { borderColor: token.colorPrimary, background: token.colorPrimaryBg } : undefined}
                title={<Space>{ICONS[p.id]}{txt.title}</Space>}
                extra={<Tag color={p.maturity === "stable" ? "success" : "warning"}>{p.maturity}</Tag>}>
                <Typography.Text type="secondary">{txt.desc}</Typography.Text>
                {blocked(p) && <Typography.Paragraph type="warning" style={{ marginTop: 8, marginBottom: 0 }}>
                  {t("requirements.paths.need_project", "需先打开 / 创建项目后才能走知识路径")}
                </Typography.Paragraph>}
              </ProCard>
            </Col>
          );
        })}
      </Row>
      {current && (
        <ProCard bordered style={{ marginTop: 16 }} title={pathText(t, current.id).title} data-testid={`path-steps-${current.id}`}>
          {blocked(current) && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={t("requirements.paths.need_project", "需先打开 / 创建项目后才能走知识路径")} />}
          <Steps direction="vertical" current={-1} items={current.steps.map((s) => {
            const to = blocked(current) ? null : resolveStep(s, known, projectId);
            return {
              title: stepText(t, s.id),
              status: "wait" as const,
              description: to
                ? <Button type="link" size="small" style={{ padding: 0 }} onClick={() => navigate(to)}>{t("requirements.paths.open", "打开")}</Button>
                : <Typography.Text type="secondary">{s.needsProject && !projectId ? t("requirements.paths.needs_project", "需项目") : t("requirements.paths.unavailable", "暂未提供")}</Typography.Text>,
            };
          })} />
        </ProCard>
      )}
    </PageContainer>
  );
}

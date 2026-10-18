import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Badge, Card, Col, Empty, Row, Space, Typography } from "antd";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useUi } from "@/app/store";
import { ExportShelf } from "./ExportShelf";
import { PREFLIGHT_KINDS, PreflightPanel, type PreflightKind } from "./PreflightPanel";
import { useResearch } from "./store";
import { TechReportExport } from "./TechReportExport";
import { VersionsPanel } from "./VersionsPanel";

const TABS = ["live", "shelf", "versions", "preflight", "export"] as const;
type Tab = (typeof TABS)[number];

/** 研究产物工作区 (legacy ArtifactDrawer + ArtifactVersionsPanel + PublicationPreflightPanel + TechReportExportButton). */
export default function ArtifactsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const projectId = useUi((s) => s.projectId);
  const { lineageId } = useParams();
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab");
  const tab: Tab = (TABS as readonly string[]).includes(raw ?? "") ? (raw as Tab) : lineageId ? "versions" : "live";
  const rawKind = params.get("kind");
  const kind: PreflightKind = (PREFLIGHT_KINDS as readonly string[]).includes(rawKind ?? "") ? (rawKind as PreflightKind) : "storm";
  const report = useResearch((s) => s.report);
  const setParam = (k: string, v: string) => setParams((p) => { const n = new URLSearchParams(p); n.set(k, v); return n; }, { replace: true });

  const live = [
    { key: "deep", title: t("research.art.deep", "深度研究报告"), ready: !!report,
      sub: report ? t("research.art.deep_cites", "{{n}} 条引用", { n: report.citations?.length ?? 0 }) : t("research.art.not_yet", "尚未生成"),
      to: "/knowledge/research?tab=deep" },
    { key: "lb", title: t("research.art.formulations", "配方与推荐"), sub: t("research.art.formulations_sub", "配方编辑与候选"), to: "/lab/formulations" },
    { key: "doe", title: t("research.art.doe", "DOE 实验方案"), sub: t("research.art.doe_sub", "设计与任务"), to: "/lab/doe" },
    { key: "opt", title: t("research.art.opt", "寻优收敛"), sub: t("research.art.opt_sub", "帕累托与定型"), to: "/cockpit/pareto" },
  ];

  return (
    <PageContainer
      subTitle={t("research.art.sub", "研究产物：导出货架、版本谱系、发布预检与技术报告导出")}
      tabActiveKey={tab}
      onTabChange={(k) => (lineageId && k !== "versions" ? navigate(`/knowledge/research/artifacts?tab=${k}`) : setParam("tab", k))}
      tabList={[
        { key: "live", tab: t("research.art.tab_live", "当前产物") },
        { key: "shelf", tab: t("research.art.tab_shelf", "导出货架") },
        { key: "versions", tab: t("research.art.tab_versions", "版本管理") },
        { key: "preflight", tab: t("research.art.tab_preflight", "发布预检") },
        { key: "export", tab: t("research.art.tab_export", "技术报告导出") },
      ]}
    >
      {!projectId && tab !== "live" && <Empty description={t("research.need_project", "请先在顶部选择项目，才能保存到货架或版本库。")} />}
      {tab === "live" && (
        <Row gutter={[16, 16]}>
          {live.map((a) => (
            <Col key={a.key} xs={24} sm={12} lg={6}>
              <Card hoverable onClick={() => navigate(a.to)} title={a.title}
                extra={a.ready !== undefined && <Badge status={a.ready ? "success" : "default"} text={a.ready ? t("research.art.ready", "就绪") : t("research.art.empty", "空")} />}>
                <Typography.Text type="secondary">{a.sub}</Typography.Text>
              </Card>
            </Col>
          ))}
        </Row>
      )}
      {projectId && tab === "shelf" && <ExportShelf projectId={projectId} />}
      {projectId && tab === "versions" && <ProCard><VersionsPanel projectId={projectId} lineageId={lineageId} /></ProCard>}
      {projectId && tab === "preflight" && <ProCard><PreflightPanel projectId={projectId} kind={kind} onKindChange={(k) => setParam("kind", k)} /></ProCard>}
      {projectId && tab === "export" && (
        <ProCard title={t("research.art.export_title", "技术报告导出（经发布预检门禁）")}>
          <Space wrap size="large">
            <TechReportExport kind="formulation" projectId={projectId} />
            <TechReportExport kind="doe" projectId={projectId} />
            <TechReportExport kind="optimization" projectId={projectId} />
          </Space>
          <Typography.Paragraph type="secondary" style={{ marginTop: 16 }}>
            {t("research.art.export_help", "阻断项未处理时导出会被拒绝，请先在「发布预检」中处理或放行。")}{" "}
            <Link to="/knowledge/research/artifacts?tab=preflight&kind=tech_report_formulation">{t("research.tr.open_preflight", "打开发布预检")}</Link>
          </Typography.Paragraph>
        </ProCard>
      )}
    </PageContainer>
  );
}

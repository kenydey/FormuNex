import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Alert, Button, Card, Col, Descriptions, Empty, Input, InputNumber, List, Row, Select, Space, Table, Tag, Typography, theme } from "antd";
import { SafetyCertificateOutlined } from "@ant-design/icons";
import { useMutation } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { Chart } from "@/shared/Chart";
import { EditableRows } from "@/shared/EditableRows";
import { ErrorAlert } from "@/shared/feedback";
import { noveltyBand } from "./lib";
import type { Formulation, FormulationIngredient, IPReport, PatentRisk } from "./types";
import { useDomainOptions, useProjectRequirement } from "./useProjectRequirement";

const RISK_COLOR: Record<string, string> = { high: "error", medium: "warning", low: "success", unknown: "default" };

/** IP analysis result: novelty gauge, risky patents, molecule-level prefilter and white-space hints. */
export function IpReportView({ report }: { report: IPReport }) {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const band = noveltyBand(report.novelty_score);
  const color = band === "high" ? token.colorSuccess : band === "partial" ? token.colorWarning : token.colorError;
  const verdict = band === "high" ? t("research.ip.high", "新颖性较高") : band === "partial" ? t("research.ip.partial", "存在部分重叠") : t("research.ip.risk", "存在较高侵权风险");
  return (
    <>
      <Row gutter={16}>
        <Col xs={24} md={8}>
          <Card size="small">
            <Chart height={200} option={{
              series: [{
                type: "gauge", startAngle: 180, endAngle: 0, min: 0, max: 100, radius: "100%", center: ["50%", "70%"],
                progress: { show: true, width: 14, itemStyle: { color } }, axisLine: { lineStyle: { width: 14 } },
                axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false }, pointer: { show: false },
                title: { offsetCenter: [0, "25%"], color: token.colorTextSecondary, fontSize: 12 },
                detail: { valueAnimation: true, offsetCenter: [0, "-10%"], formatter: "{value}%", color, fontSize: 24 },
                data: [{ value: Math.round(Math.max(0, Math.min(1, report.novelty_score)) * 100), name: t("research.ip.novelty", "新颖性评分") }],
              }],
            }} />
          </Card>
        </Col>
        <Col xs={24} md={16}>
          <Alert type={band === "high" ? "success" : band === "partial" ? "warning" : "error"} showIcon message={verdict} style={{ marginBottom: 12 }} />
          <Descriptions size="small" column={1} bordered items={[
            { key: "f", label: t("research.ip.formulation", "配方"), children: report.formulation_name },
            { key: "e", label: t("research.ip.engine", "引擎"), children: <Typography.Text code>{report.engine}</Typography.Text> },
            { key: "n", label: t("research.ip.searched", "检索专利"), children: t("research.ip.searched_n", "{{n}} 件", { n: report.raw_patents_searched }) },
          ]} />
        </Col>
      </Row>
      <ProCard title={t("research.ip.risks", "风险专利")} ghost style={{ marginTop: 16 }}>
        <Table<PatentRisk> size="small" rowKey="patent_id" pagination={false} dataSource={report.risks}
          locale={{ emptyText: t("research.ip.no_risks", "未发现风险专利") }}
          columns={[
            { title: t("research.ip.patent", "专利"), dataIndex: "patent_id", width: 170, render: (v: string) => <Typography.Text code>{v}</Typography.Text> },
            { title: t("research.ip.risk_level", "风险"), dataIndex: "risk", width: 90, render: (r: string) => <Tag color={RISK_COLOR[r] ?? "default"}>{r}</Tag> },
            { title: t("research.ip.title", "标题"), dataIndex: "title" },
            { title: t("research.ip.overlap", "权利要求重叠"), dataIndex: "claim_overlap" },
            { title: t("research.ip.advice", "建议"), dataIndex: "recommendation" },
          ]} />
      </ProCard>
      {(report.molecule_checks?.length ?? 0) > 0 && (
        <ProCard title={t("research.ip.molecules", "分子级专利预筛（molbloom · SureChEMBL）")} ghost style={{ marginTop: 16 }}>
          <List size="small" bordered dataSource={report.molecule_checks} renderItem={(c) => (
            <List.Item extra={c.patented === true ? <Tag color="warning">{t("research.ip.patented", "已见于专利")}</Tag>
              : c.patented === false ? <Tag color="success">{t("research.ip.not_patented", "未见收录")}</Tag> : <Tag>{t("research.ip.unknown", "未知")}</Tag>}>
              <Typography.Text title={c.smiles}>{c.name}</Typography.Text>
            </List.Item>
          )} />
          <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>
            {t("research.ip.bloom_note", "布隆过滤器预筛仅判断分子是否出现在专利语料，不代表权利要求覆盖；正式结论需 FTO 检索。")}
          </Typography.Paragraph>
        </ProCard>
      )}
      {report.whitespace_hints.length > 0 && (
        <ProCard title={t("research.ip.whitespace", "技术空白区提示")} ghost style={{ marginTop: 16 }}>
          <List size="small" dataSource={report.whitespace_hints} renderItem={(h) => <List.Item>→ {h}</List.Item>} />
        </ProCard>
      )}
    </>
  );
}

const blank = (): FormulationIngredient => ({ name: "", role: "additive", weight_pct: 0, cas_no: "", smiles: "" });

/** 专利 / IP 分析: novelty & infringement-risk screening of a formulation (POST /api/ip/analyze). */
export default function IpAnalysisPage() {
  const { t } = useTranslation();
  const { domain: projectDomain } = useProjectRequirement();
  const domains = useDomainOptions();
  const location = useLocation();
  const preset = (location.state as { formulation?: Formulation } | null)?.formulation;
  const [name, setName] = useState(preset?.name ?? "");
  const [domain, setDomain] = useState(preset?.domain ?? projectDomain);
  useEffect(() => { if (!preset) setDomain(projectDomain); }, [projectDomain, preset]);
  const [rows, setRows] = useState<FormulationIngredient[]>(preset?.ingredients ?? [blank()]);
  const [limit, setLimit] = useState(8);
  const analyze = useMutation({
    mutationFn: () => call<IPReport>("POST", "/api/ip/analyze", {
      limit_patents: limit,
      formulation: { name: name.trim() || t("research.ip.unnamed", "未命名配方"), domain,
        ingredients: rows.filter((r) => r.name.trim()).map((r) => ({ ...r, weight_pct: Number(r.weight_pct) || 0, cas_no: r.cas_no || undefined, smiles: r.smiles || undefined })),
        rationale: "", predicted: {}, warnings: [] },
    }),
  });
  const filled = rows.filter((r) => r.name.trim()).length;
  return (
    <PageContainer subTitle={t("research.ip.sub", "检索相关专利，评估配方新颖性与侵权风险，并给出技术空白区提示")}>
      <ProCard title={t("research.ip.input", "待分析配方")} bordered
        extra={<Button type="primary" icon={<SafetyCertificateOutlined />} loading={analyze.isPending} disabled={!filled} onClick={() => analyze.mutate()}>
          {t("research.ip.run", "运行 IP 分析")}</Button>}>
        <Space wrap style={{ marginBottom: 12 }}>
          <Input style={{ width: 260 }} value={name} onChange={(e) => setName(e.target.value)} placeholder={t("research.ip.name", "配方名称")} />
          <Select style={{ width: 160 }} value={domain} onChange={setDomain} options={domains} />
          <span>{t("research.ip.limit", "检索专利数")}</span>
          <InputNumber min={1} max={30} value={limit} onChange={(v) => setLimit(v ?? 8)} />
        </Space>
        <EditableRows<FormulationIngredient> value={rows} onChange={setRows} newRow={blank}
          columns={[
            { title: t("research.emb.material", "原料"), dataIndex: "name" },
            { title: t("research.emb.role", "角色"), dataIndex: "role", width: 140 },
            { title: "wt%", dataIndex: "weight_pct", valueType: "digit", width: 110 },
            { title: "CAS", dataIndex: "cas_no", width: 140 },
            { title: "SMILES", dataIndex: "smiles" },
          ]} />
      </ProCard>
      <div style={{ marginTop: 16 }}>
        <ErrorAlert error={analyze.error} />
        {analyze.data ? <IpReportView report={analyze.data} />
          : !analyze.isPending && <Empty description={t("research.ip.empty", "填写配方后点击「运行 IP 分析」检索相关专利并评估新颖性。")} />}
      </div>
    </PageContainer>
  );
}

import { PageContainer, ProCard } from "@ant-design/pro-components";
import {
  Alert, App, AutoComplete, Button, Checkbox, Descriptions, Empty, Form, Radio, Segmented, Select, Space, Table, Tag, Tooltip, Typography, theme,
} from "antd";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ApiError, call } from "@/api/client";
import { errorText } from "@/shared/feedback";
import { FormulationPicker } from "./FormulationPicker";
import { deltaDisplay, notFoundCandidates, supplyBadges, surechemblRef } from "./logic";
import type {
  ExternalCandidate, Formulation, LayerMeta, LiteratureCandidate, LlmCandidate, SubstituteCandidate, SubstitutionReport, SupplyRiskReport,
  SurechemblCandidate,
} from "./types";
import { useRequirement } from "./useRequirement";

const SHOWN_METRICS = ["cost_cny_per_kg", "voc_gpl", "salt_spray_hours", "cleaning_efficiency"];

interface Promote { key: string; name: string; cas_no?: string | null; smiles?: string | null; role?: string | null; source?: string; source_ref?: string }

function Delta({ pct }: { pct: number | null | undefined }) {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const d = deltaDisplay(pct);
  if (d.tone === "none") return <Typography.Text type="secondary">—</Typography.Text>;
  if (d.tone === "flat") return <Typography.Text type="secondary">{t("design.sub.flat", "持平")}</Typography.Text>;
  return <span style={{ color: d.tone === "up" ? token.colorSuccess : token.colorError }}>{d.text}</span>;
}

/** Smart raw-material substitution (legacy MaterialSubstitutionModal). Funnel: L1 catalog Δ → L2 literature / KG → L3 PubChem →
 *  SureChEMBL patents → L4 rules / LLM. Only catalog rows carry formula Δ; the others can be promoted into the catalog. */
export default function SubstitutionPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [params] = useSearchParams();
  const { projectId, requirement } = useRequirement();
  const [source, setSource] = useState<"baseline" | "version">("baseline");
  const [versionId, setVersionId] = useState<string | undefined>();
  const [formulation, setFormulation] = useState<Formulation | null>(null);
  const [material, setMaterial] = useState(params.get("material") ?? "");
  const [opts, setOpts] = useState({ unavailable: false, literature: true, external: true, surechembl: true });
  const [llm, setLlm] = useState<"auto" | "on" | "off">("auto");
  const [candidates, setCandidates] = useState<string[]>([]);
  const [promoting, setPromoting] = useState<string | null>(null);
  const [promoteMsg, setPromoteMsg] = useState<{ type: "success" | "info" | "warning"; text: string } | null>(null);

  const risk = useQuery({ queryKey: ["materials", "supply-risk"], queryFn: () => call<SupplyRiskReport>("GET", "/api/materials/supply-risk") });
  // useMemo：条件表达式每渲染会新建数组，裸放在 useEffect 依赖里会让 effect 每轮重跑。
  const ingredients = useMemo(
    () => (source === "version" ? formulation?.ingredients ?? [] : []),
    [source, formulation],
  );
  useEffect(() => {
    if (!material && ingredients.length) setMaterial(ingredients[0].name);
  }, [ingredients, material]);

  const find = useMutation({
    mutationFn: () => call<SubstitutionReport>("POST", "/api/materials/substitutes", {
      requirement: requirement ?? undefined, formulation: source === "version" ? formulation ?? undefined : undefined, material, limit: 10,
      include_unavailable: opts.unavailable, include_external: opts.external, external_limit: 8,
      include_literature: opts.literature, literature_limit: 8, include_surechembl: opts.surechembl, surechembl_limit: 8,
      include_llm: llm === "auto" ? null : llm === "on", llm_limit: 5,
    }),
    onMutate: () => { setCandidates([]); setPromoteMsg(null); },
    onError: (e) => setCandidates(e instanceof ApiError ? notFoundCandidates(e.detail) : []),
  });

  const promote = async (p: Promote) => {
    setPromoting(p.key);
    setPromoteMsg(null);
    try {
      const r = await call<{ action: string; name: string; reason?: string }>("POST", "/api/materials/propose", {
        name: p.name, cas_no: p.cas_no || undefined, smiles: p.smiles || undefined, role: p.role || undefined,
        source: p.source || "user", source_ref: p.source_ref,
      });
      const name = r.name || p.name;
      setPromoteMsg(r.action === "upsert" || r.action === "exists"
        ? { type: "success", text: t("design.sub.promoted", "已入库：{{name}}（{{action}}）。可再点「查找替代」查看库内 Δ。", { name, action: r.action }) }
        : r.action === "pending" ? { type: "info", text: t("design.sub.pending", "已进入待入库：{{name}}", { name }) }
          : { type: "warning", text: t("design.sub.not_promoted", "未入库：{{name}}（{{action}}{{reason}}）", { name, action: r.action, reason: r.reason ? ` · ${r.reason}` : "" }) });
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setPromoting(null);
    }
  };

  const report = find.data;
  const atRisk = Object.entries(risk.data?.at_risk ?? {});
  const inCatalogCell = (row: { in_catalog: boolean; catalog_name?: string | null }) =>
    row.in_catalog ? <Tag color="blue">{t("design.sub.in_catalog", "已在库：{{n}}", { n: row.catalog_name ?? "" })}</Tag> : null;
  const promoteCol = <R extends { in_catalog: boolean }>(toPromote: (r: R) => Promote) => ({
    title: t("common.actions", "操作"), key: "act", width: 120, render: (_v: unknown, r: R) => {
      if (r.in_catalog) return <Typography.Text type="secondary">{t("design.sub.see_above", "见库内候选")}</Typography.Text>;
      const p = toPromote(r);
      return <Button size="small" loading={promoting === p.key} onClick={() => void promote(p)}>{t("design.sub.promote", "入库并选用")}</Button>;
    },
  });
  const section = (title: string, meta: LayerMeta | undefined, rows: unknown[], table: React.ReactNode, note: string) => (
    <ProCard bordered style={{ marginTop: 16 }} title={title} extra={<Tag>{meta?.count ?? rows.length}</Tag>}>
      {meta?.skipped_reason && rows.length === 0 ? <Typography.Text type="secondary">{meta.skipped_reason}</Typography.Text>
        : rows.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} /> : table}
      <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>{note}</Typography.Paragraph>
    </ProCard>
  );
  const confidenceNote = (c: SubstituteCandidate["delta_confidence"]) => ({
    high: t("design.sub.conf_high", "分子描述符可用，性能预测有分辨力"),
    low: t("design.sub.conf_low", "跨角色替换，预测仅供参考"),
    cost_only: t("design.sub.conf_cost", "未装 RDKit：同角色换料仅成本/VOC 有分辨力，性能指标不可信"),
  })[c];
  const badgeLabel = (b: string) => ({ stale_price: t("design.sub.stale_price", "价格偏旧"), missing_price: t("design.sub.missing_price", "缺价"),
    long_lead_time: t("design.sub.long_lead", "交期长") } as Record<string, string>)[b] ?? b;
  const metricLabel = (m: string) => ({ cost_cny_per_kg: t("design.m.cost", "成本"), voc_gpl: "VOC",
    salt_spray_hours: t("design.m.salt", "耐盐雾"), cleaning_efficiency: t("design.m.cleaning", "清洗率") } as Record<string, string>)[m] ?? m;

  return (
    <PageContainer subTitle={t("design.sub.sub", "漏斗：库内预测偏离 → 文献/知识库证据 → 联网结构相似（PubChem）→ SureChEMBL 专利化学相似 → AI/规则扩召回。仅库内项含配方 Δ。")}>
      {atRisk.length > 0 && <Alert type="warning" showIcon style={{ marginBottom: 16 }} message={t("design.sub.supply_risk", "供应风险")}
        description={`${atRisk.map(([n, s]) => `${n}（${s}）`).join("、")}${risk.data?.affected.length
          ? ` · ${t("design.sub.affected", "影响 {{n}} 个配方", { n: risk.data.affected.length })}` : ""}`} />}
      {!projectId && <Alert type="info" showIcon style={{ marginBottom: 16 }} message={t("design.need_project", "请先在顶部选择项目。")} />}
      <ProCard bordered>
        <Form layout="vertical">
          <Form.Item label={t("design.sub.source", "配方来源")}>
            <Space wrap>
              <Radio.Group value={source} onChange={(e) => setSource(e.target.value)} optionType="button" options={[
                { value: "baseline", label: t("design.sub.baseline", "项目基线（由需求重建）") },
                { value: "version", label: t("design.sub.version", "已保存的配方版本") }]} />
              {source === "version" && <FormulationPicker domain={requirement?.domain} value={versionId}
                onPick={(id, f) => { setVersionId(id); setFormulation(f); setMaterial(""); }} />}
            </Space>
          </Form.Item>
          <Form.Item label={t("design.sub.material", "要替换的组分")}>
            <Space.Compact style={{ width: "100%", maxWidth: 640 }}>
              {ingredients.length ? (
                <Select style={{ flex: 1 }} value={material || undefined} onChange={setMaterial} options={ingredients.map((i) => ({
                  value: i.name, label: `${i.name}（${i.role} · ${i.weight_pct}%）` }))} />
              ) : (
                <AutoComplete style={{ flex: 1 }} value={material} onChange={setMaterial} options={candidates.map((c) => ({ value: c }))}
                  placeholder={t("design.sub.material_ph", "组分名称（不确定时先查找，会列出配方中的材料）")} />
              )}
              <Button type="primary" loading={find.isPending} disabled={!material || (source === "baseline" && !requirement) || (source === "version" && !formulation)}
                onClick={() => find.mutate()}>{t("design.sub.find", "查找替代")}</Button>
            </Space.Compact>
          </Form.Item>
          <Space wrap size="large">
            <Tooltip title={t("design.sub.unavailable_tip", "默认关闭：替代候选与逆向设计池已排除 discontinued")}>
              <Checkbox checked={opts.unavailable} onChange={(e) => setOpts({ ...opts, unavailable: e.target.checked })}>{t("design.sub.unavailable", "包含停产材料")}</Checkbox>
            </Tooltip>
            <Tooltip title={t("design.sub.literature_tip", "KG substitutes 边与 KB 产品登记；不算配方 Δ，可一键入库后再算")}>
              <Checkbox checked={opts.literature} onChange={(e) => setOpts({ ...opts, literature: e.target.checked })}>{t("design.sub.literature", "文献 / 知识库候选")}</Checkbox>
            </Tooltip>
            <Tooltip title={t("design.sub.external_tip", "按 CAS/SMILES 拉结构相似清单；不算配方 Δ，可一键入库后再算")}>
              <Checkbox checked={opts.external} onChange={(e) => setOpts({ ...opts, external: e.target.checked })}>{t("design.sub.external", "联网检索替代（PubChem）")}</Checkbox>
            </Tooltip>
            <Tooltip title={t("design.sub.surechembl_tip", "结构相似 + 专利证据链接；不算配方 Δ")}>
              <Checkbox checked={opts.surechembl} onChange={(e) => setOpts({ ...opts, surechembl: e.target.checked })}>{t("design.sub.surechembl", "SureChEMBL 专利化学相似")}</Checkbox>
            </Tooltip>
            <Space>
              <Tooltip title={t("design.sub.llm_tip", "自动：库内候选不足 3 个时用规则表 + 可选 LLM 扩名；不算配方 Δ")}>
                <span>{t("design.sub.llm", "AI / 规则扩召回")}</span></Tooltip>
              <Segmented value={llm} onChange={(v) => setLlm(v as typeof llm)} options={[
                { value: "auto", label: t("design.sub.llm_auto", "自动") }, { value: "on", label: t("design.sub.llm_on", "强制开启") },
                { value: "off", label: t("design.sub.llm_off", "关闭") }]} />
            </Space>
          </Space>
        </Form>
        {find.error && <Alert type="error" showIcon style={{ marginTop: 12 }} message={errorText(find.error)}
          description={candidates.length > 0 && <Space wrap>
            <span>{t("design.sub.pick_material", "配方中的材料（点击选用）：")}</span>
            {candidates.map((c) => <Button key={c} size="small" onClick={() => { setMaterial(c); find.reset(); setCandidates([]); }}>{c}</Button>)}
          </Space>} />}
        {promoteMsg && <Alert type={promoteMsg.type} showIcon closable style={{ marginTop: 12 }} message={promoteMsg.text} onClose={() => setPromoteMsg(null)} />}
      </ProCard>

      {report && (
        <>
          <ProCard bordered style={{ marginTop: 16 }} title={t("design.sub.catalog", "库内候选（优先）")}>
            <Descriptions size="small" column={{ xs: 1, md: 3 }} style={{ marginBottom: 12 }}>
              <Descriptions.Item label={t("design.sub.original", "被替换组分")}>
                {report.original}{report.original_in_catalog === false && <Tag color="warning" style={{ marginLeft: 6 }}>{t("design.sub.not_in_catalog", "原组分不在库")}</Tag>}
              </Descriptions.Item>
              <Descriptions.Item label={t("design.sub.group", "可互换组")}>{report.substitute_group ?? "—"}</Descriptions.Item>
              <Descriptions.Item label={t("design.sub.considered", "库内考察")}>{report.total_considered}</Descriptions.Item>
              {report.identity && <Descriptions.Item label={t("design.sub.identity", "鉴定")} span={2}>
                {report.identity.resolved ? [report.identity.cas_no && `CAS ${report.identity.cas_no}`,
                  report.identity.smiles && <Typography.Text key="s" code ellipsis style={{ maxWidth: 280 }}>{report.identity.smiles}</Typography.Text>,
                  `source=${report.identity.source}`].filter(Boolean).map((x, i) => <span key={i} style={{ marginRight: 8 }}>{x}</span>)
                  : t("design.sub.unresolved", "未解析结构（{{s}}）", { s: report.identity.source })}
              </Descriptions.Item>}
              {(report.layers_used?.length ?? 0) > 0 && <Descriptions.Item label={t("design.sub.layers", "使用层级")}>
                {report.layers_used!.map((l) => <Tag key={l}>{l}</Tag>)}</Descriptions.Item>}
            </Descriptions>
            {report.candidates.length === 0 ? (
              <Empty description={t("design.sub.no_catalog", "目录中没有同组或同角色的替代品。可在「原料」中新增材料，或从下方联网结果入库。")} />
            ) : (
              <Table<SubstituteCandidate> size="small" rowKey="material" dataSource={report.candidates} pagination={false} scroll={{ x: true }}
                columns={[
                  { title: t("design.sub.candidate", "候选材料"), render: (_v, c) => (
                    <Space direction="vertical" size={0}>
                      <Typography.Text strong>{c.material}</Typography.Text>
                      <Typography.Text type="secondary">{c.functional_class}{c.availability !== "in_stock" && <Tag color="warning" style={{ marginLeft: 6 }}>{c.availability}</Tag>}</Typography.Text>
                      {supplyBadges(c).length > 0 && <Space size={4} wrap>{supplyBadges(c).map((b) => <Tag key={b} color="orange">{badgeLabel(b)}</Tag>)}</Space>}
                    </Space>) },
                  { title: t("design.sub.similarity", "相似度"), align: "right", render: (_v, c) => `${(c.structural_score * 100).toFixed(0)}%` },
                  ...SHOWN_METRICS.map((m) => ({ title: metricLabel(m), key: m, align: "right" as const,
                    render: (_v: unknown, c: SubstituteCandidate) => <Delta pct={c.deltas[m]?.pct} /> })),
                  { title: t("design.sub.feasibility", "可行性"), render: (_v, c) => (c.feasible
                    ? <Tooltip title={confidenceNote(c.delta_confidence)}><Tag color={c.delta_confidence === "high" ? "green" : "gold"}>{t("design.sub.usable", "可用")}</Tag></Tooltip>
                    : <Tooltip title={c.blocking_reasons.join("\n")}><Tag color="red">{t("design.sub.blocked", "拦截")}</Tag></Tooltip>) },
                ]} />
            )}
            {report.candidates[0] && <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
              {confidenceNote(report.candidates[0].delta_confidence)}</Typography.Paragraph>}
          </ProCard>

          {opts.literature && section(t("design.sub.lit_title", "文献 / 知识库候选（证据 · 选用后可入库）"), report.literature_meta, report.literature ?? [],
            <Table<LiteratureCandidate> size="small" rowKey={(r) => `${r.source}:${r.entity_id || r.name}`} dataSource={report.literature} pagination={false} scroll={{ x: true }}
              columns={[
                { title: t("design.sub.name", "名称"), render: (_v, r) => <Space direction="vertical" size={0}><Typography.Text strong>{r.name}</Typography.Text>
                  {r.cas_no && <Typography.Text type="secondary">CAS {r.cas_no}</Typography.Text>}{inCatalogCell(r)}
                  {r.note && <Typography.Text type="secondary" ellipsis={{ tooltip: r.note }} style={{ maxWidth: 240 }}>{r.note}</Typography.Text>}</Space> },
                { title: t("design.sub.source_col", "来源"), dataIndex: "source" },
                { title: t("design.sub.confidence", "置信"), align: "right", render: (_v, r) => (r.confidence != null ? `${Math.round(Number(r.confidence) * 100)}%` : "—") },
                { title: t("design.sub.evidence", "证据"), render: (_v, r) => {
                  const bits = (r.evidence ?? []).map((e) => e.sentence || e.source_id || "").filter(Boolean);
                  return bits.length ? <Typography.Text ellipsis={{ tooltip: bits.join("\n") }} style={{ maxWidth: 280 }}>
                    {bits[0]}{bits.length > 1 ? ` (+${bits.length - 1})` : ""}</Typography.Text> : "—";
                } },
                promoteCol<LiteratureCandidate>((r) => ({ key: `${r.source}:${r.entity_id || r.name}`, name: r.name, cas_no: r.cas_no, smiles: r.smiles, role: r.role_hint })),
              ]} />, t("design.sub.lit_note", "文献项未做配方 Δ；入库后可再点「查找替代」查看偏离。"))}

          {opts.external && section(t("design.sub.ext_title", "联网候选（结构相似 · 选用后可入库）"), report.external_meta, report.external ?? [],
            <Table<ExternalCandidate> size="small" rowKey={(r) => (r.cid != null ? `cid-${r.cid}` : r.name)} dataSource={report.external} pagination={false} scroll={{ x: true }}
              columns={[
                { title: t("design.sub.name", "名称"), render: (_v, r) => <Space direction="vertical" size={0}><Typography.Text strong>{r.name}</Typography.Text>
                  {r.iupac_name && r.iupac_name !== r.name && <Typography.Text type="secondary" ellipsis={{ tooltip: r.iupac_name }} style={{ maxWidth: 260 }}>{r.iupac_name}</Typography.Text>}
                  {inCatalogCell(r)}</Space> },
                { title: "CAS", dataIndex: "cas_no", render: (_v, r) => r.cas_no || "—" },
                { title: t("design.sub.similarity", "相似度"), align: "right", render: (_v, r) => `${(r.similarity * 100).toFixed(0)}%` },
                { title: t("design.sub.source_col", "来源"), render: () => "PubChem" },
                promoteCol<ExternalCandidate>((r) => ({ key: r.cid != null ? String(r.cid) : r.name, name: r.name, cas_no: r.cas_no, smiles: r.smiles, role: r.role_hint })),
              ]} />, t("design.sub.ext_note", "联网项未做配方 Δ；入库后可再点「查找替代」查看偏离。聚合物/仅商品名若无法解析结构会显示原因。"))}

          {opts.surechembl && section(t("design.sub.sure_title", "SureChEMBL 候选（专利化学相似 · 选用后可入库）"), report.surechembl_meta, report.surechembl ?? [],
            <Table<SurechemblCandidate> size="small" rowKey={(r) => `surechembl:${r.chemical_id || r.smiles || r.name}`} dataSource={report.surechembl} pagination={false} scroll={{ x: true }}
              columns={[
                { title: t("design.sub.name", "名称"), render: (_v, r) => <Space direction="vertical" size={0}><Typography.Text strong>{r.name}</Typography.Text>
                  {r.chemical_id && <Typography.Text type="secondary">SCHEMBL {r.chemical_id}</Typography.Text>}
                  {r.smiles && <Typography.Text code ellipsis={{ tooltip: r.smiles }} style={{ maxWidth: 220 }}>{r.smiles}</Typography.Text>}
                  {inCatalogCell(r)}</Space> },
                { title: t("design.sub.similarity", "相似度"), align: "right", render: (_v, r) => `${(Number(r.similarity) * 100).toFixed(0)}%` },
                { title: t("design.sub.patents", "专利证据"), render: (_v, r) => (r.patents ?? []).length === 0 ? "—" : (
                  <Space direction="vertical" size={0}>{(r.patents ?? []).slice(0, 2).map((p) => (p.url
                    ? <Typography.Link key={p.doc_id} href={p.url} target="_blank" rel="noreferrer" ellipsis style={{ maxWidth: 260 }}>{p.doc_id}{p.title ? ` · ${p.title}` : ""}</Typography.Link>
                    : <span key={p.doc_id}>{p.doc_id}</span>))}</Space>) },
                promoteCol<SurechemblCandidate>((r) => ({ key: `surechembl:${r.chemical_id || r.smiles || r.name}`, name: r.name, smiles: r.smiles, role: r.role_hint,
                  source: "surechembl", source_ref: surechemblRef(r) })),
              ]} />, t("design.sub.sure_note", "SureChEMBL 项未做配方 Δ；专利链接指向 Google Patents（由 SCPN 推导）。"))}

          {llm !== "off" && section(`${t("design.sub.llm_title", "AI / 规则功能建议")}${report.llm_meta?.mode ? ` · mode=${report.llm_meta.mode}` : ""}`,
            report.llm_meta, report.llm ?? [],
            <Table<LlmCandidate> size="small" rowKey={(r) => `${r.source}:${r.name}`} dataSource={report.llm} pagination={false} scroll={{ x: true }}
              columns={[
                { title: t("design.sub.name", "名称"), render: (_v, r) => <Space direction="vertical" size={0}><Typography.Text strong>{r.name}</Typography.Text>
                  {r.kind && <Typography.Text type="secondary">{r.kind}</Typography.Text>}{inCatalogCell(r)}</Space> },
                { title: t("design.sub.source_col", "来源"), dataIndex: "source" },
                { title: t("design.sub.rationale", "理由"), dataIndex: "rationale", render: (_v, r) => r.rationale || "—" },
                promoteCol<LlmCandidate>((r) => ({ key: `${r.source}:${r.name}`, name: r.name, cas_no: r.cas_no, smiles: r.smiles, role: r.role_hint })),
              ]} />, t("design.sub.llm_note", "AI/规则项未做配方 Δ，且不会编造 CAS；入库后可再查偏离。"))}
        </>
      )}
    </PageContainer>
  );
}

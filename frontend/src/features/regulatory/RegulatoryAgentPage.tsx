import { PageContainer } from "@ant-design/pro-components";
import { Alert, Button, Card, Input, InputNumber, Select, Space, Table, Tag, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { ErrorAlert, errorText } from "@/shared/feedback";

interface AgentReply {
  intent: string;
  question: string;
  answer: string;
  tools_used: string[];
  evidence: unknown[];
  classification?: { classified?: boolean; signal_word?: string | null; h_codes?: string[]; hazards?: { hazard_class: string; category: string; h_codes: string[] }[] };
  suggestions?: { target: { name: string; cas: string | null; pct: number; hazards: { hazard_class: string; category: string; h_code: string | null }[] }; alternatives: { name: string; cas_no: string | null; verdict: string; note: string; classifications: { hazard_class: string; category: string; h_code: string | null; source: string }[]; rationale: string }[] }[];
  data_gaps?: { code: string; message: string }[];
}

interface Msg { role: "user" | "agent"; text: string; reply?: AgentReply; error?: unknown }
interface CompRow { key: number; name: string; cas: string; pct: number; role: string }

// 快捷提问的问题本身是发给智能体的示例提问内容，保持中文原文；按钮文案是 UI 文案，在组件内走 t()（静态调用，供 i18n 提取器校验）。

const VERDICT_COLOR: Record<string, string> = { lower: "green", comparable: "orange", unknown: "default" };

/** 法规助手 (方案 §5.6): 只读 — 查物质分类、解释分类依据、降危害建议、国标条款问答。不写任何数据。 */
export default function RegulatoryAgentPage() {
  const { t } = useTranslation();
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  // 默认组分是示例数据（内容，非 UI 文案），保持原文。
  const [comps, setComps] = useState<CompRow[]>([
    { key: 1, name: "甲醛", cas: "50-00-0", pct: 5, role: "preservative" },
    { key: 2, name: "水", cas: "7732-18-5", pct: 95, role: "solvent" },
  ]);
  const verdictLabel = (v: string) => {
    switch (v) {
      case "lower": return t("regulatory.verdict.lower", "危害更低");
      case "comparable": return t("regulatory.verdict.comparable", "相当");
      default: return t("regulatory.verdict.unknown", "未验证");
    }
  };

  const ask = async (question: string, intent: string) => {
    const q = question.trim();
    if (!q || busy) return;
    setMsgs((m) => [...m, { role: "user", text: q }]);
    setInput("");
    setBusy(true);
    try {
      const needsComps = intent === "explain" || intent === "substitute";
      const reply = await call<AgentReply>("POST", "/api/plm/regulatory/ask", {
        question: q,
        intent,
        ...(needsComps ? {
          components: comps.filter((c) => c.name.trim() && c.pct > 0)
            .map((c) => ({ name: c.name, cas: c.cas || null, pct: c.pct, role: c.role || undefined })),
          properties: { phys_state: "liquid" },
        } : {}),
      });
      setMsgs((m) => [...m, { role: "agent", text: reply.answer, reply }]);
    } catch (e) {
      setMsgs((m) => [...m, { role: "agent", text: errorText(e), error: e }]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <PageContainer title={t("regulatory.title", "法规助手")} subTitle={t("regulatory.subtitle", "只读：查物质分类 · 解释分类依据 · 降危害建议 · 国标条款问答")}>
      <Alert type="info" showIcon style={{ marginBottom: 12 }}
        message={t("regulatory.readonly_note", "只读智能体：不修改任何锁定数据、不保存分类结果。替代建议均标注验证状态，未入库候选物需法规专员评估后方可采用。")} />
      <Card title={t("regulatory.comps_title", "配方组分（解释分类 / 降危害建议时使用）")} size="small" style={{ marginBottom: 12 }}>
        <Table<CompRow> size="small" pagination={false} dataSource={comps} rowKey="key"
          columns={[
            { title: t("regulatory.col.name", "组分名"), dataIndex: "name", render: (_, r) => <Input value={r.name} onChange={(e) => setComps(comps.map((c) => c.key === r.key ? { ...c, name: e.target.value } : c))} /> },
            { title: t("regulatory.col.cas", "CAS"), dataIndex: "cas", render: (_, r) => <Input value={r.cas} onChange={(e) => setComps(comps.map((c) => c.key === r.key ? { ...c, cas: e.target.value } : c))} /> },
            { title: t("regulatory.col.pct", "占比 %"), dataIndex: "pct", render: (_, r) => <InputNumber value={r.pct} min={0} max={100} onChange={(v) => setComps(comps.map((c) => c.key === r.key ? { ...c, pct: v ?? 0 } : c))} /> },
            { title: t("regulatory.col.role", "角色"), dataIndex: "role", render: (_, r) => <Select value={r.role} style={{ width: 140 }} options={["preservative", "solvent", "resin", "hardener", "additive", "pigment", "surfactant"].map((x) => ({ value: x, label: x }))} onChange={(v) => setComps(comps.map((c) => c.key === r.key ? { ...c, role: v } : c))} /> },
            { title: "", render: (_, r) => <Button danger size="small" onClick={() => setComps(comps.filter((c) => c.key !== r.key))}>{t("common.delete", "删除")}</Button> },
          ]} />
        <Button size="small" style={{ marginTop: 8 }} onClick={() => setComps([...comps, { key: Date.now(), name: "", cas: "", pct: 0, role: "additive" }])}>{t("regulatory.add_comp", "添加组分")}</Button>
      </Card>
      <Card size="small" style={{ marginBottom: 12 }}>
        <Space direction="vertical" style={{ width: "100%" }}>
          {msgs.length === 0 && <Typography.Text type="secondary">{t("regulatory.empty_hint", "输入问题，或点下方快捷提问。解释分类 / 降危害建议使用上方组分表。")}</Typography.Text>}
          {msgs.map((m, i) => (
            <div key={i} style={{ textAlign: m.role === "user" ? "right" : "left" }}>
              <div style={{ display: "inline-block", maxWidth: "85%", textAlign: "left", background: m.role === "user" ? "#e6f4ff" : "#f5f5f5", borderRadius: 8, padding: "8px 12px", whiteSpace: "pre-wrap" }}>
                {m.error ? <ErrorAlert error={m.error} /> : <Typography.Paragraph style={{ margin: 0 }}>{m.text}</Typography.Paragraph>}
                {m.reply?.classification && (
                  <div style={{ marginTop: 8 }}>
                    <Tag color={m.reply.classification.classified ? "red" : "green"}>
                      {m.reply.classification.classified
                        ? t("regulatory.classified", "已分类：{{signal}} {{h}}", { signal: m.reply.classification.signal_word ?? "", h: (m.reply.classification.h_codes ?? []).join(" ") })
                        : t("regulatory.unclassified", "未分类")}
                    </Tag>
                  </div>
                )}
                {m.reply?.suggestions?.map((s, j) => (
                  <Table key={j} size="small" pagination={false} style={{ marginTop: 8 }}
                    title={() => t("regulatory.target", "目标组分：{{name}}（{{pct}}%）— {{hazards}}", {
                      name: s.target.name, pct: s.target.pct,
                      hazards: s.target.hazards.map((h) => `${h.hazard_class} ${h.category} ${h.h_code ?? ""}`).join("；"),
                    })}
                    dataSource={s.alternatives} rowKey={(a) => a.name}
                    columns={[
                      { title: t("regulatory.col.alternative", "候选替代物"), dataIndex: "name" },
                      { title: t("regulatory.col.verdict", "验证"), dataIndex: "verdict", render: (v: string) => <Tag color={VERDICT_COLOR[v] ?? "default"}>{verdictLabel(v)}</Tag> },
                      { title: t("regulatory.col.note", "说明"), dataIndex: "note" },
                      { title: t("regulatory.col.classification", "候选物分类"), render: (_, a) => a.classifications.map((c) => `${c.hazard_class} ${c.category}`).join("；") || t("regulatory.unclassified", "未分类") },
                    ]} />
                ))}
                {m.reply?.data_gaps?.length ? (
                  <Typography.Text type="secondary" style={{ display: "block", marginTop: 8 }}>
                    {t("regulatory.data_gaps", "数据缺口：{{gaps}}", { gaps: m.reply.data_gaps.map((g) => g.message).join("；") })}
                  </Typography.Text>
                ) : null}
              </div>
            </div>
          ))}
        </Space>
      </Card>
      <Space wrap style={{ marginBottom: 12 }}>
        {[
          { q: "查一下 50-00-0 的分类", intent: "substance", label: t("regulatory.quick.substance", "查物质分类") },
          { q: "为什么这个配方分类为危险化学品？请解释分类依据", intent: "explain", label: t("regulatory.quick.explain", "解释分类依据") },
          { q: "怎么降低这个配方的危害？有没有替代组分？", intent: "substitute", label: t("regulatory.quick.substitute", "降危害建议") },
          { q: "GB 30000 对易燃液体怎么分类？", intent: "standard", label: t("regulatory.quick.standard", "国标条款问答") },
        ].map((q) => <Button key={q.intent} onClick={() => void ask(q.q, q.intent)}>{q.label}</Button>)}
      </Space>
      <Input.Search placeholder={t("regulatory.ask_placeholder", "问法规助手：如“查一下 1330-20-7 的分类”“GB 30000 对急性毒性怎么分类？”")} enterButton={t("regulatory.send", "发送")}
        value={input} onChange={(e) => setInput(e.target.value)} loading={busy}
        onSearch={(v) => void ask(v, "auto")} />
    </PageContainer>
  );
}

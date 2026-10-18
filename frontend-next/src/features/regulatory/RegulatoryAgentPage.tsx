import { PageContainer } from "@ant-design/pro-components";
import { Alert, Button, Card, Input, InputNumber, Select, Space, Table, Tag, Typography } from "antd";
import { useState } from "react";
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

const QUICK: { label: string; q: string; intent: string }[] = [
  { label: "查物质分类", q: "查一下 50-00-0 的分类", intent: "substance" },
  { label: "解释分类依据", q: "为什么这个配方分类为危险化学品？请解释分类依据", intent: "explain" },
  { label: "降危害建议", q: "怎么降低这个配方的危害？有没有替代组分？", intent: "substitute" },
  { label: "国标条款问答", q: "GB 30000 对易燃液体怎么分类？", intent: "standard" },
];

const VERDICT_COLOR: Record<string, string> = { lower: "green", comparable: "orange", unknown: "default" };
const VERDICT_ZH: Record<string, string> = { lower: "危害更低", comparable: "相当", unknown: "未验证" };

/** 法规助手 (方案 §5.6): 只读 — 查物质分类、解释分类依据、降危害建议、国标条款问答。不写任何数据。 */
export default function RegulatoryAgentPage() {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [comps, setComps] = useState<CompRow[]>([
    { key: 1, name: "甲醛", cas: "50-00-0", pct: 5, role: "preservative" },
    { key: 2, name: "水", cas: "7732-18-5", pct: 95, role: "solvent" },
  ]);

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
    <PageContainer title="法规助手" subTitle="只读：查物质分类 · 解释分类依据 · 降危害建议 · 国标条款问答">
      <Alert type="info" showIcon style={{ marginBottom: 12 }}
        message="只读智能体：不修改任何锁定数据、不保存分类结果。替代建议均标注验证状态，未入库候选物需法规专员评估后方可采用。" />
      <Card title="配方组分（解释分类 / 降危害建议时使用）" size="small" style={{ marginBottom: 12 }}>
        <Table<CompRow> size="small" pagination={false} dataSource={comps} rowKey="key"
          columns={[
            { title: "组分名", dataIndex: "name", render: (_, r) => <Input value={r.name} onChange={(e) => setComps(comps.map((c) => c.key === r.key ? { ...c, name: e.target.value } : c))} /> },
            { title: "CAS", dataIndex: "cas", render: (_, r) => <Input value={r.cas} onChange={(e) => setComps(comps.map((c) => c.key === r.key ? { ...c, cas: e.target.value } : c))} /> },
            { title: "占比 %", dataIndex: "pct", render: (_, r) => <InputNumber value={r.pct} min={0} max={100} onChange={(v) => setComps(comps.map((c) => c.key === r.key ? { ...c, pct: v ?? 0 } : c))} /> },
            { title: "角色", dataIndex: "role", render: (_, r) => <Select value={r.role} style={{ width: 140 }} options={["preservative", "solvent", "resin", "hardener", "additive", "pigment", "surfactant"].map((x) => ({ value: x, label: x }))} onChange={(v) => setComps(comps.map((c) => c.key === r.key ? { ...c, role: v } : c))} /> },
            { title: "", render: (_, r) => <Button danger size="small" onClick={() => setComps(comps.filter((c) => c.key !== r.key))}>删除</Button> },
          ]} />
        <Button size="small" style={{ marginTop: 8 }} onClick={() => setComps([...comps, { key: Date.now(), name: "", cas: "", pct: 0, role: "additive" }])}>添加组分</Button>
      </Card>
      <Card size="small" style={{ marginBottom: 12 }}>
        <Space direction="vertical" style={{ width: "100%" }}>
          {msgs.length === 0 && <Typography.Text type="secondary">输入问题，或点下方快捷提问。解释分类 / 降危害建议使用上方组分表。</Typography.Text>}
          {msgs.map((m, i) => (
            <div key={i} style={{ textAlign: m.role === "user" ? "right" : "left" }}>
              <div style={{ display: "inline-block", maxWidth: "85%", textAlign: "left", background: m.role === "user" ? "#e6f4ff" : "#f5f5f5", borderRadius: 8, padding: "8px 12px", whiteSpace: "pre-wrap" }}>
                {m.error ? <ErrorAlert error={m.error} /> : <Typography.Paragraph style={{ margin: 0 }}>{m.text}</Typography.Paragraph>}
                {m.reply?.classification && (
                  <div style={{ marginTop: 8 }}>
                    <Tag color={m.reply.classification.classified ? "red" : "green"}>
                      {m.reply.classification.classified ? `已分类：${m.reply.classification.signal_word ?? ""} ${(m.reply.classification.h_codes ?? []).join(" ")}` : "未分类"}
                    </Tag>
                  </div>
                )}
                {m.reply?.suggestions?.map((s, j) => (
                  <Table key={j} size="small" pagination={false} style={{ marginTop: 8 }}
                    title={() => `目标组分：${s.target.name}（${s.target.pct}%）— ${s.target.hazards.map((h) => `${h.hazard_class} ${h.category} ${h.h_code ?? ""}`).join("；")}`}
                    dataSource={s.alternatives} rowKey={(a) => a.name}
                    columns={[
                      { title: "候选替代物", dataIndex: "name" },
                      { title: "验证", dataIndex: "verdict", render: (v: string) => <Tag color={VERDICT_COLOR[v] ?? "default"}>{VERDICT_ZH[v] ?? v}</Tag> },
                      { title: "说明", dataIndex: "note" },
                      { title: "候选物分类", render: (_, a) => a.classifications.map((c) => `${c.hazard_class} ${c.category}`).join("；") || "未分类" },
                    ]} />
                ))}
                {m.reply?.data_gaps?.length ? (
                  <Typography.Text type="secondary" style={{ display: "block", marginTop: 8 }}>
                    数据缺口：{m.reply.data_gaps.map((g) => g.message).join("；")}
                  </Typography.Text>
                ) : null}
              </div>
            </div>
          ))}
        </Space>
      </Card>
      <Space wrap style={{ marginBottom: 12 }}>
        {QUICK.map((q) => <Button key={q.label} onClick={() => void ask(q.q, q.intent)}>{q.label}</Button>)}
      </Space>
      <Input.Search placeholder="问法规助手：如“查一下 1330-20-7 的分类”“GB 30000 对急性毒性怎么分类？”" enterButton="发送"
        value={input} onChange={(e) => setInput(e.target.value)} loading={busy}
        onSearch={(v) => void ask(v, "auto")} />
    </PageContainer>
  );
}

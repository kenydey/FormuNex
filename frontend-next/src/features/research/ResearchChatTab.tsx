import { Alert, Button, Card, Empty, Flex, Input, Popconfirm, Space, Tag, Tooltip, Typography, theme } from "antd";
import { LoadingOutlined, SendOutlined, StopOutlined } from "@ant-design/icons";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { postSse } from "@/api/stream";
import { Markdown } from "@/shared/Markdown";
import { errorText } from "@/shared/feedback";
import { RelatedWikiList, SaveWikiDraftButton } from "@/features/wiki";
import { chatHistory, citationTarget, evidenceKey, pageLabel, sanitizeEvidence } from "./lib";
import { useResearch } from "./store";
import type { ChatStreamEvent, ChatTurn, Evidence } from "./types";
import { useProjectRequirement } from "./useProjectRequirement";

const CHAT_TIMEOUT_MS = 5 * 60 * 1000;

export function CitationTag({ ev }: { ev: Evidence }) {
  const { t } = useTranslation();
  const target = citationTarget(ev);
  const page = pageLabel(ev);
  const label = (
    <Tooltip title={ev.snippet}>
      <Tag color="blue" style={{ marginBottom: 4, maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis" }}>
        {ev.is_seed_corpus && `${t("research.sources.seed", "示例")} · `}
        {ev.title || ev.identifier || ev.source} · {page ?? t("research.cite.no_page", "页码未知")}
      </Tag>
    </Tooltip>
  );
  if (!target) return label;
  return "internal" in target ? <Link to={target.internal}>{label}</Link>
    : <a href={target.external} target="_blank" rel="noreferrer noopener">{label}</a>;
}

/** Q&A grounded on the selected sources (RAG, POST /api/chat/stream). */
export default function ResearchChatTab() {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const { projectId, domain } = useProjectRequirement();
  const { sources, selected, turns, setTurns } = useResearch();
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const ctrl = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => endRef.current?.scrollIntoView?.({ block: "end" }), [turns]);
  useEffect(() => () => ctrl.current?.abort(), []);

  const patchLast = (p: (m: ChatTurn) => ChatTurn) =>
    setTurns((ts) => ts.map((m, i) => (i === ts.length - 1 && m.role === "assistant" ? p(m) : m)));

  const send = async (question: string, clarified?: { term: string; resolved: string; entity_id?: string }[]) => {
    const q = question.trim();
    if (!q || busy || !selected.length) return;
    const active = sources.filter((e) => selected.includes(evidenceKey(e))).map(sanitizeEvidence);
    const history = chatHistory(useResearch.getState().turns);
    setDraft("");
    setBusy(true);
    setTurns((ts) => [...ts, { role: "user", content: q }, { role: "assistant", content: "", streaming: true, phase: "retrieval" }]);
    const c = new AbortController();
    ctrl.current = c;
    const timer = setTimeout(() => c.abort(), CHAT_TIMEOUT_MS);
    try {
      await postSse<ChatStreamEvent>("/api/chat/stream", {
        question: q, sources: active, domain, project_id: projectId ?? undefined, history, mode: "chat", clarified_entities: clarified,
      }, (ev) => {
        if (ev.type === "phase") patchLast((m) => ({ ...m, phase: ev.phase, toolStatus: ev.phase === "answering" ? null : m.toolStatus }));
        else if (ev.type === "tool_start") patchLast((m) => ({ ...m, phase: "tools", toolStatus: ev.label || ev.name }));
        else if (ev.type === "tool_result" && !ev.ok) patchLast((m) => ({ ...m, toolStatus: t("research.chat.tool_failed", "工具失败：{{n}}", { n: ev.name }) }));
        else if (ev.type === "token") patchLast((m) => ({ ...m, content: m.content + ev.delta }));
        else if (ev.type === "done") patchLast(() => ({
          role: "assistant", content: ev.answer, citations: ev.citations, kbChunksUsed: ev.kb_chunks_used ?? 0,
          notices: ev.notices ?? null, clarification: ev.clarification ?? null, provenance: ev.evidence_provenance ?? null,
        }));
        else if (ev.type === "error") patchLast((m) => ({ ...m, streaming: false, phase: undefined, content: `${m.content}\n\n> ⚠️ ${ev.message}` }));
      }, c.signal);
    } catch (e) {
      const stopped = c.signal.aborted;
      setTurns((ts) => {
        const last = ts[ts.length - 1];
        if (last?.role === "assistant" && last.streaming && !last.content && !stopped) return ts.slice(0, -1).concat({ role: "assistant", content: `> ⚠️ ${errorText(e)}` });
        return ts.map((m, i) => (i === ts.length - 1 && m.streaming ? { ...m, streaming: false, phase: undefined,
          content: m.content + (stopped ? `\n\n> ${t("research.chat.stopped", "已停止")}` : `\n\n> ⚠️ ${errorText(e)}`) } : m));
      });
    } finally {
      clearTimeout(timer);
      setBusy(false);
      ctrl.current = null;
    }
  };

  const phaseText: Record<string, string> = {
    retrieval: t("research.chat.retrieval", "检索资料中…"), tools: t("research.chat.tools", "调用化学工具…"),
    answering: t("research.chat.answering", "生成回答…"), claims: t("research.chat.claims", "核对引用…"),
  };
  const provColor: Record<string, string> = { supported: "success", partial: "warning", unavailable: "error" };
  const provText: Record<string, string> = {
    supported: t("research.prov.supported", "证据充分"), partial: t("research.prov.partial", "证据部分"),
    unavailable: t("research.prov.unavailable", "无据 / 不足"),
  };
  const emptyText = sources.length === 0 ? t("research.chat.no_sources", "先在「外部检索」页检索或上传资料，然后在此向资料提问。")
    : selected.length === 0 ? t("research.chat.no_selected", "请在资料表中勾选至少一条资料用于问答。")
      : t("research.chat.ready", "资料已就绪。例如：「这些专利的主要防腐机理是什么？」");

  return (
    <Card title={t("research.chat.title", "基于已选 {{s}} / {{n}} 条资料问答（RAG 接地）", { s: selected.length, n: sources.length })}
      extra={<Popconfirm title={t("research.chat.clear_confirm", "清空对话？")} onConfirm={() => setTurns(() => [])} disabled={!turns.length || busy}>
        <Button size="small" disabled={!turns.length || busy}>{t("research.chat.clear", "清空对话")}</Button></Popconfirm>}>
      <div style={{ maxHeight: "60vh", overflowY: "auto", paddingRight: 4 }}>
        {turns.length === 0 && <Empty description={emptyText} />}
        {turns.map((m, i) => (
          <Flex key={i} justify={m.role === "user" ? "flex-end" : "flex-start"} style={{ marginBottom: 12 }}>
            <div style={{ maxWidth: "85%", padding: "8px 12px", borderRadius: token.borderRadiusLG,
              background: m.role === "user" ? token.colorPrimaryBg : token.colorFillQuaternary, border: `1px solid ${token.colorBorderSecondary}` }}>
              {m.role === "user" ? <Typography.Text style={{ whiteSpace: "pre-wrap" }}>{m.content}</Typography.Text> : (
                <>
                  {m.streaming ? (
                    <Typography.Paragraph style={{ whiteSpace: "pre-wrap", margin: 0 }}>
                      {m.content}
                      <Typography.Text type="secondary"> <LoadingOutlined /> {m.phase === "tools" ? m.toolStatus || phaseText.tools : phaseText[m.phase ?? ""] ?? ""}</Typography.Text>
                    </Typography.Paragraph>
                  ) : <Markdown content={m.content} />}
                  {(m.notices ?? []).map((n, j) => <Alert key={`${n.code}-${j}`} type="warning" showIcon message={n.message} style={{ marginTop: 6 }} />)}
                  <Space wrap size={4} style={{ marginTop: 6 }}>
                    {(m.kbChunksUsed ?? 0) > 0 && <Tag color="green">{t("research.chat.kb_used", "知识库 ×{{n}}", { n: m.kbChunksUsed })}</Tag>}
                    {m.provenance?.evidence_availability && (
                      <Tag color={provColor[m.provenance.evidence_availability] ?? "default"}>
                        {provText[m.provenance.evidence_availability] ?? m.provenance.evidence_availability}</Tag>
                    )}
                    {(m.provenance?.notes ?? []).slice(0, 4).map((n) => <Tag key={n}>{n}</Tag>)}
                  </Space>
                  {m.clarification && (
                    <div style={{ marginTop: 8 }}>
                      <Typography.Text type="warning">{m.clarification.question}</Typography.Text>
                      <div style={{ marginTop: 4 }}>
                        <Space wrap>
                          {(m.clarification.possible_meanings ?? []).map((meaning) => (
                            <Button key={meaning} size="small" disabled={busy} onClick={() => {
                              const term = m.clarification?.ambiguous_term || meaning;
                              void send(t("research.chat.clarified", "按「{{m}}」理解继续：{{t}}", { m: meaning, t: term }),
                                [{ term, resolved: meaning, entity_id: m.clarification?.candidate_entity_ids?.[0] }]);
                            }}>{meaning}</Button>
                          ))}
                        </Space>
                      </div>
                    </div>
                  )}
                  {(m.citations?.length ?? 0) > 0 && (
                    <div style={{ marginTop: 8, paddingTop: 6, borderTop: `1px solid ${token.colorBorderSecondary}` }}>
                      {m.citations!.map((c, j) => <CitationTag key={`${evidenceKey(c)}-${j}`} ev={c} />)}
                    </div>
                  )}
                  <RelatedWikiList citations={m.citations} />
                  {!m.streaming && m.content && (
                    <div style={{ marginTop: 6 }}>
                      <SaveWikiDraftButton answer={m.content} question={turns[i - 1]?.role === "user" ? turns[i - 1].content : ""}
                        citations={m.citations} origin="chat" />
                    </div>
                  )}
                </>
              )}
            </div>
          </Flex>
        ))}
        <div ref={endRef} />
      </div>
      <Flex gap={8} style={{ marginTop: 12 }} align="flex-end">
        <Input.TextArea value={draft} onChange={(e) => setDraft(e.target.value)} autoSize={{ minRows: 2, maxRows: 6 }}
          disabled={!selected.length} placeholder={selected.length ? t("research.chat.placeholder", "向资料提问…（Enter 发送，Shift+Enter 换行）") : emptyText}
          onPressEnter={(e) => { if (!e.shiftKey) { e.preventDefault(); void send(draft); } }} />
        {busy ? <Button danger icon={<StopOutlined />} onClick={() => ctrl.current?.abort()}>{t("research.stop", "停止")}</Button>
          : <Button type="primary" icon={<SendOutlined />} disabled={!draft.trim() || !selected.length} onClick={() => void send(draft)}>
            {t("research.chat.send", "发送")}</Button>}
      </Flex>
    </Card>
  );
}

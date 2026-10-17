import {
  CameraOutlined, DeleteOutlined, MessageOutlined, PlusOutlined, SendOutlined, StopOutlined,
} from "@ant-design/icons";
import { PageContainer, ProCard } from "@ant-design/pro-components";
import {
  Alert, App, Button, Collapse, Empty, Input, List, Popconfirm, Space, Switch, Tag, Tooltip, Typography, Upload, theme,
} from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import dayjs from "dayjs";
import { call } from "@/api/client";
import { postSse } from "@/api/stream";
import { useUi } from "@/app/store";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { CitationAnswer, sourceHref } from "./CitationAnswer";
import { ChatComposerPlus, TokenPicker, filterSkills, useSkills, type ComposerSelection, type PickItem } from "./ChatComposerPlus";
import { RagPrewarmBar, WikiChatModeSelector } from "./ChatToolbar";
import { ReviewerCard } from "./Reviewer";
import { RelatedWikiList, SaveWikiDraftButton } from "@/features/wiki";
import { ThinkingTimeline, useStepTitle } from "./ThinkingTimeline";
import {
  applyChatEvent, historyForRequest, kbSourceId, newId, restoreHistory, serializeHistory, sessionTitle, trailingToken,
} from "./logic";
import type { ChatMessage, ChatStreamEvent, Evidence, SessionInfo, StructureResult } from "./types";

const TIMEOUT_MS = 5 * 60 * 1000;
const SESSION_TTL = 7 * 86400;

interface KbSource { id: string; title: string; filename: string }
interface SendOpts { clarifiedEntities?: { term: string; resolved: string; entity_id?: string }[] }

function CitationChips({ citations }: { citations: Evidence[] }) {
  const { t } = useTranslation();
  return (
    <Space size={[4, 4]} wrap style={{ marginTop: 8 }} data-testid="citation-chips">
      {citations.map((c, i) => {
        const sid = kbSourceId(c);
        const page = c.page != null ? (c.page_end && c.page_end > c.page ? `pp.${c.page}–${c.page_end}` : `p.${c.page}`) : null;
        const label = <><b>[{i + 1}]</b> {(c.title || c.identifier).slice(0, 40)}{page ? ` · ${page}` : ""}</>;
        const tip = <div><div>{c.title}</div><div style={{ opacity: 0.75 }}>{c.snippet.slice(0, 240)}</div></div>;
        const tag = <Tag color={c.is_seed_corpus ? "warning" : undefined}>{label}</Tag>;
        return (
          <Tooltip key={`${c.identifier}-${i}`} title={tip}>
            {sid ? <Link to={sourceHref(sid, c.page)}>{tag}</Link>
              : c.url ? <a href={c.url} target="_blank" rel="noopener noreferrer">{tag}</a> : tag}
          </Tooltip>
        );
      })}
      {citations.some((c) => c.is_seed_corpus) && <Tag color="warning">{t("chat.seed", "含离线示例摘要")}</Tag>}
    </Space>
  );
}

function ClaimsList({ m }: { m: ChatMessage }) {
  const { t } = useTranslation();
  if (!m.sourcedClaims?.length) return null;
  const tag = (c: NonNullable<ChatMessage["sourcedClaims"]>[number]) => {
    if (c.raw_verdict === "conflicting") return <Tag color="purple">{t("chat.claim.conflicting", "冲突")}</Tag>;
    if (c.status === "supported") return <Tag color="success">{t("chat.claim.supported", "有据")}</Tag>;
    if (c.status === "weak") return <Tag color="warning">{t("chat.claim.weak", "弱支持")}</Tag>;
    return <Tag color="error">{t("chat.claim.unsupported", "无据")}</Tag>;
  };
  return (
    <Collapse size="small" ghost data-testid="chat-claims" items={[{
      key: "c",
      label: <Typography.Text type="secondary">{t("chat.claims", "断言核验 {{n}} 条", { n: m.sourcedClaims.length })}</Typography.Text>,
      children: <List size="small" dataSource={m.sourcedClaims} renderItem={(c, i) => (
        <List.Item key={i}><Space align="start">{tag(c)}<span>{c.text}</span>
          <Typography.Text type="secondary">{Math.round(c.confidence * 100)}%</Typography.Text></Space></List.Item>
      )} />,
    }]} />
  );
}

function AssistantBody({ m, question, busy, projectId, onClarify }: {
  m: ChatMessage; question: string; busy: boolean; projectId: string | null; onClarify: (meaning: string) => void;
}) {
  const { t } = useTranslation();
  const stepTitle = useStepTitle();
  const running = m.steps?.find((s) => s.status === "running");
  if (m.streaming) {
    return (
      <div>
        {m.steps && m.steps.length > 0 && <ThinkingTimeline steps={m.steps} defaultOpen />}
        <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.7 }}>{m.content}</div>
        <Typography.Text type="secondary" data-testid="chat-phase">
          {m.phase === "tools" && m.toolStatus ? `⏳ ${m.toolStatus}` : running ? `⏳ ${stepTitle(running)}…` : t("chat.thinking_now", "思考中…")}
        </Typography.Text>
      </div>
    );
  }
  const av = m.evidenceProvenance?.evidence_availability;
  return (
    <div>
      {m.steps && m.steps.length > 0 && <ThinkingTimeline steps={m.steps} />}
      {m.content ? <CitationAnswer content={m.content} citations={m.citations} /> : null}
      {m.error && <Alert type="error" showIcon message={m.error} style={{ marginTop: 8 }} />}
      {m.notices?.map((n, j) => <Alert key={`${n.code}-${j}`} type="warning" showIcon message={n.message} style={{ marginTop: 6 }} data-testid="chat-notice" />)}
      <Space size={[4, 4]} wrap style={{ marginTop: 6 }}>
        {(m.kbChunksUsed ?? 0) > 0 && <Tooltip title={t("chat.kb_tip", "本回答额外融合了持久知识库中的全文片段")}>
          <Tag color="green">{t("chat.kb_used", "知识库 ×{{n}}", { n: m.kbChunksUsed })}</Tag></Tooltip>}
        {m.rewrittenQuery && m.rewrittenQuery !== question && <Tooltip title={m.rewrittenQuery}>
          <Tag>{t("chat.rewritten", "检索改写")}</Tag></Tooltip>}
        {av && <Tag data-testid="chat-evidence-provenance"
          color={av === "supported" ? "success" : av === "partial" ? "warning" : av === "unavailable" ? "error" : "default"}>
          {t("chat.provenance", "证据诚实度")}：{av === "supported" ? t("chat.prov.supported", "充分") : av === "partial" ? t("chat.prov.partial", "部分")
            : av === "unavailable" ? t("chat.prov.unavailable", "无据/不足") : t("chat.prov.unknown", "未知")}
        </Tag>}
        {(m.evidenceProvenance?.notes ?? []).slice(0, 4).map((n) => <Tag key={n}>{n}</Tag>)}
      </Space>
      {m.clarification && (
        <Alert type="info" style={{ marginTop: 8 }} data-testid="chat-clarification" message={m.clarification.question}
          description={<Space wrap>{(m.clarification.possible_meanings ?? []).map((meaning) => (
            <Button key={meaning} size="small" disabled={busy} onClick={() => onClarify(meaning)}>{meaning}</Button>
          ))}</Space>} />
      )}
      <ClaimsList m={m} />
      {m.citations && m.citations.length > 0 && <CitationChips citations={m.citations} />}
      <RelatedWikiList citations={m.citations} />
      {m.content && !m.error && !busy && (
        <div style={{ marginTop: 6 }}>
          <SaveWikiDraftButton answer={m.content} question={question} citations={m.citations} origin="chat" projectId={projectId} />
        </div>
      )}
      {m.reviewRunId && <ReviewerCard runId={m.reviewRunId} ctx={{ question, answer: m.content, citations: m.citations, projectId }} />}
    </div>
  );
}

/** /knowledge/chat[/:sessionId] — knowledge Q&A over the project's KB, streamed, with sessions persisted per project. */
export default function ChatPage() {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const { message, modal } = App.useApp();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { sessionId: routeSession } = useParams();
  const projectId = useUi((s) => s.projectId);

  const [messages, setMessagesState] = useState<ChatMessage[]>([]);
  const messagesRef = useRef<ChatMessage[]>([]);
  const setMessages = useCallback((fn: (prev: ChatMessage[]) => ChatMessage[]) => {
    messagesRef.current = fn(messagesRef.current);
    setMessagesState(messagesRef.current);
  }, []);
  const [sessionId, setSessionId] = useState<string | null>(routeSession ?? null);
  const loadedRef = useRef<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const abortRef = useRef<AbortController | null>(null);
  const [draft, setDraft] = useState("");
  const [sel, setSel] = useState<ComposerSelection>({ mode: "chat", skills: [], connectors: [], mcpServers: [] });
  const [deep, setDeep] = useState(false);
  const [refs, setRefs] = useState<PickItem[]>([]);
  const [structure, setStructure] = useState<StructureResult | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const sessions = useQuery({
    queryKey: ["chat", "sessions", projectId ?? null],
    queryFn: async () => {
      const qs = new URLSearchParams({ limit: "50" });
      if (projectId) qs.set("project_id", projectId);
      return (await call<{ sessions: SessionInfo[] }>("GET", `/api/session/list?${qs}`)).sessions ?? [];
    },
  });

  // Open the session named in the URL (unless it is the one this page just created / is showing).
  const load = useQuery({
    queryKey: ["chat", "session", routeSession], enabled: !!routeSession && routeSession !== loadedRef.current, gcTime: 0,
    queryFn: () => call<{ history: unknown[] }>("GET", `/api/session/load/${encodeURIComponent(routeSession!)}`),
  });
  useEffect(() => {
    if (!routeSession) {
      if (loadedRef.current !== null && !busy) {
        loadedRef.current = null;
        setSessionId(null);
        setMessages(() => []);
      }
      return;
    }
    if (load.data && routeSession !== loadedRef.current) {
      loadedRef.current = routeSession;
      setSessionId(routeSession);
      setMessages(() => restoreHistory(load.data.history));
    }
  }, [routeSession, load.data, busy, setMessages]);

  useEffect(() => {
    scrollRef.current?.scrollTo?.({ top: scrollRef.current.scrollHeight });
  }, [messages]);
  useEffect(() => () => abortRef.current?.abort(), []);

  const skills = useSkills(true);
  const kbSources = useQuery({ queryKey: ["kb", "sources"], staleTime: 60_000,
    queryFn: () => call<{ sources: KbSource[] }>("GET", "/api/kb/sources") });
  const slashItems = filterSkills(skills.data ?? [], draft);
  const atTok = trailingToken(draft, "@");
  const atItems: PickItem[] | null = atTok.active
    ? (kbSources.data?.sources ?? []).map((s) => ({ id: s.id, label: s.title || s.filename, hint: s.filename }))
      .filter((s) => !atTok.query || s.label.toLowerCase().includes(atTok.query)).slice(0, 12)
    : null;

  const pickSlash = (it: PickItem) => {
    const tok = trailingToken(draft, "/");
    setSel((s) => (s.skills.includes(it.id) ? s : { ...s, skills: [...s.skills, it.id].slice(0, 12) }));
    setDraft(tok.before.trimEnd() ? `${tok.before.trimEnd()} ` : "");
  };
  const pickAt = (it: PickItem) => {
    setRefs((r) => (r.some((x) => x.id === it.id) ? r : [...r, it].slice(0, 20)));
    setDraft(`${atTok.before}@${it.label} `);
  };

  const persist = useCallback(async (id: string) => {
    const msgs = messagesRef.current;
    if (!msgs.some((m) => !m.streaming && m.content)) return;
    try {
      await call("POST", "/api/session/save", { session_id: id, history: serializeHistory(msgs), ttl_seconds: SESSION_TTL,
        project_id: projectId ?? undefined, title: sessionTitle(msgs) });
      loadedRef.current = id;
      if (routeSession !== id) navigate(`/knowledge/chat/${encodeURIComponent(id)}`, { replace: true });
      void qc.invalidateQueries({ queryKey: ["chat", "sessions"] });
    } catch (e) {
      message.warning(t("chat.save_failed", "会话保存失败：{{e}}", { e: errorText(e) }));
    }
  }, [projectId, routeSession, navigate, qc, message, t]);

  const approveMcp = useCallback((perm: NonNullable<ChatMessage["mcpPermission"]>, sid: string, msgId: string) => {
    modal.confirm({
      title: t("chat.mcp_session.title", "批准本会话调用 MCP 写类工具？"),
      content: <Typography.Text code>{perm.server_id} / {perm.tool_name}</Typography.Text>,
      onOk: async () => {
        await call("POST", `/api/connectors/mcp/${encodeURIComponent(perm.server_id)}/approve-session`,
          { session_id: sid, tool_name: perm.tool_name, until_session_end: true });
        setMessages((prev) => prev.map((x) => (x.id === msgId ? { ...x, notices: [...(x.notices ?? []),
          { code: "mcp_approved", message: t("chat.mcp_session.approved", "已批准本会话；请重新发送指令以重试。") }] } : x)));
      },
    });
  }, [modal, t, setMessages]);

  const send = useCallback(async (question: string, opts: SendOpts = {}) => {
    const q = question.trim();
    if (!q || busy) return;
    const sid = sessionId ?? newId("chat");
    if (!sessionId) setSessionId(sid);
    const history = historyForRequest(messagesRef.current);
    const assistantId = newId();
    setMessages((prev) => [...prev, { id: newId(), role: "user", content: q },
      { id: assistantId, role: "assistant", content: "", streaming: true, phase: "retrieval", steps: [] }]);
    const apply = (ev: ChatStreamEvent) => setMessages((prev) => prev.map((m) => (m.id === assistantId ? applyChatEvent(m, ev) : m)));
    setBusy(true);
    setError(null);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const timer = window.setTimeout(() => ctrl.abort(), TIMEOUT_MS);
    let finished = false;
    try {
      await postSse<ChatStreamEvent>("/api/chat/stream", {
        // 深度模式: the backend runs PageIndex navigation first and reports a notice if it falls back.
        question: q, sources: [], deep, project_id: projectId ?? undefined, history,
        mode: sel.mode, selected_skills: sel.skills, selected_connectors: sel.connectors, selected_mcp_servers: sel.mcpServers,
        ref_doc_ids: refs.map((r) => r.id), attachment_source_ids: refs.map((r) => r.id),
        chat_session_id: sid, structure: structure ?? undefined, clarified_entities: opts.clarifiedEntities ?? [],
      }, (ev) => {
        apply(ev);
        if (ev.type === "done" || ev.type === "error") finished = true;
      }, ctrl.signal);
    } catch (e) {
      if (!finished) {
        const aborted = ctrl.signal.aborted;
        apply({ type: "error", message: aborted ? t("chat.stopped", "已停止生成") : errorText(e) });
        if (!aborted) setError(e);
      }
    } finally {
      window.clearTimeout(timer);
      abortRef.current = null;
      setBusy(false);
    }
    const done = messagesRef.current.find((m) => m.id === assistantId);
    if (done && !done.error) {
      if (done.mcpPermission) approveMcp(done.mcpPermission, sid, assistantId);
      await persist(sid);
    }
  }, [busy, sessionId, deep, projectId, sel, refs, structure, t, setMessages, persist, approveMcp]);

  const submit = () => {
    if (slashItems?.length) return pickSlash(slashItems[0]);
    if (atItems?.length) return pickAt(atItems[0]);
    const q = draft;
    setDraft("");
    void send(q);
  };

  const newChat = () => {
    abortRef.current?.abort();
    loadedRef.current = null;
    setSessionId(null);
    setMessages(() => []);
    setRefs([]);
    navigate("/knowledge/chat");
  };

  const del = useMutation({
    mutationFn: (id: string) => call("DELETE", `/api/session/delete/${encodeURIComponent(id)}`),
    onSuccess: (_d, id) => {
      void qc.invalidateQueries({ queryKey: ["chat", "sessions"] });
      if (id === sessionId) newChat();
    },
    onError: (e) => message.error(errorText(e)),
  });

  const uploadStructure = useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.append("image", file);
      return call<StructureResult>("POST", "/api/chemical/structure", fd);
    },
    onSuccess: setStructure,
    onError: (e) => message.error(errorText(e)),
  });

  const questionBefore = useMemo(() => {
    const out: Record<string, string> = {};
    let last = "";
    for (const m of messages) {
      if (m.role === "user") last = m.content;
      else out[m.id] = last;
    }
    return out;
  }, [messages]);

  const bubble = (m: ChatMessage) => (
    <div key={m.id} style={{ display: "flex", justifyContent: m.role === "user" ? "flex-end" : "flex-start", marginBottom: 12 }}
      data-testid={`chat-msg-${m.role}`}>
      <div style={{
        maxWidth: m.role === "user" ? "75%" : "92%", minWidth: 0, padding: "8px 12px", borderRadius: token.borderRadiusLG,
        background: m.role === "user" ? token.colorPrimaryBg : token.colorFillQuaternary,
        border: `1px solid ${m.role === "user" ? token.colorPrimaryBorder : token.colorBorderSecondary}`,
      }}>
        {m.role === "user" ? <div style={{ whiteSpace: "pre-wrap" }}>{m.content}</div> : (
          <AssistantBody m={m} question={questionBefore[m.id] ?? ""} busy={busy} projectId={projectId}
            onClarify={(meaning) => {
              const term = m.clarification?.ambiguous_term || meaning;
              void send(t("chat.clarify_followup", "按「{{meaning}}」理解继续：{{term}}", { meaning, term }), {
                clarifiedEntities: [{ term, resolved: meaning, entity_id: m.clarification?.candidate_entity_ids?.[0] }],
              });
            }} />
        )}
      </div>
    </div>
  );

  return (
    <PageContainer subTitle={t("chat.sub", "向项目知识库提问：流式回答、逐句引用脚注、断言核验与证据审计")}
      extra={<Space wrap><RagPrewarmBar /><WikiChatModeSelector /></Space>}>
      <ProCard split="vertical" bordered style={{ minHeight: "calc(100vh - 220px)" }}>
        <ProCard colSpan={{ xs: 24, md: 6 }} title={t("chat.sessions", "会话")}
          extra={<Button size="small" icon={<PlusOutlined />} onClick={newChat} data-testid="chat-new">{t("chat.new", "新对话")}</Button>}>
          <ErrorAlert error={sessions.error} />
          <List size="small" loading={sessions.isLoading} dataSource={sessions.data ?? []}
            locale={{ emptyText: t("chat.no_sessions", "暂无保存的会话") }}
            renderItem={(s) => (
              <List.Item key={s.session_id} style={{ cursor: "pointer", fontWeight: s.session_id === sessionId ? 600 : undefined }}
                onClick={() => !busy && navigate(`/knowledge/chat/${encodeURIComponent(s.session_id)}`)}
                actions={[
                  <Popconfirm key="d" title={t("chat.delete_session", "删除该会话？")} onConfirm={(e) => { e?.stopPropagation(); del.mutate(s.session_id); }}
                    onCancel={(e) => e?.stopPropagation()}>
                    <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={(e) => e.stopPropagation()} />
                  </Popconfirm>,
                ]}>
                <List.Item.Meta avatar={<MessageOutlined />}
                  title={<Typography.Text ellipsis style={{ maxWidth: 160 }}>{s.title || s.session_id}</Typography.Text>}
                  description={<Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {t("chat.turns", "{{n}} 条", { n: s.history_count })}{s.updated_at ? ` · ${dayjs(s.updated_at).format("MM-DD HH:mm")}` : ""}
                  </Typography.Text>} />
              </List.Item>
            )} />
        </ProCard>
        <ProCard>
          <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
          <ErrorAlert error={load.error ?? error} />
          <div ref={scrollRef} style={{ flex: 1, overflowY: "auto", minHeight: 320, maxHeight: "calc(100vh - 420px)", paddingRight: 4 }}>
            {messages.length === 0
              ? <Empty style={{ marginTop: 64 }} description={t("chat.empty", "在下方输入问题，例如「这些专利的主要防腐机理是什么？」（/ 选技能，@ 引用知识源）")} />
              : messages.map(bubble)}
          </div>
          {structure && (
            <Alert style={{ marginTop: 8 }} closable onClose={() => setStructure(null)}
              type={structure.recognized ? "success" : "warning"}
              message={structure.recognized
                ? <Space wrap>{t("chat.structure.ok", "已识别结构")}<Typography.Text code>{structure.smiles}</Typography.Text>
                    {structure.confidence != null && structure.confidence < 0.6 &&
                      <Tag color="warning">{t("chat.structure.low", "低置信 {{n}}%", { n: Math.round(structure.confidence * 100) })}</Tag>}</Space>
                : t("chat.structure.fail", "未能识别：{{e}}", { e: structure.error || "—" })}
              description={structure.hits.length > 0
                ? t("chat.structure.hits", "相似材料：{{h}}", { h: structure.hits.map((h) => `${h.name} (${Math.round(h.similarity * 100)}%)`).join("、") })
                : structure.warnings.join("；") || undefined} />
          )}
          <Space size={[4, 4]} wrap style={{ marginTop: 8 }} data-testid="composer-selection-chips">
            {sel.mode === "evidence" && <Tag color="blue" closable onClose={() => setSel({ ...sel, mode: "chat" })}>{t("chat.mode.evidence", "文献综合")}</Tag>}
            {sel.skills.map((id) => <Tag key={`s-${id}`} closable onClose={() => setSel({ ...sel, skills: sel.skills.filter((x) => x !== id) })}>/{id}</Tag>)}
            {sel.connectors.map((id) => <Tag key={`c-${id}`} color="green" closable onClose={() => setSel({ ...sel, connectors: sel.connectors.filter((x) => x !== id) })}>{id}</Tag>)}
            {sel.mcpServers.map((id) => <Tag key={`m-${id}`} color="purple" closable onClose={() => setSel({ ...sel, mcpServers: sel.mcpServers.filter((x) => x !== id) })}>mcp:{id}</Tag>)}
            {refs.map((r) => <Tag key={`r-${r.id}`} color="cyan" closable onClose={() => setRefs(refs.filter((x) => x.id !== r.id))}>@{r.label.slice(0, 30)}</Tag>)}
          </Space>
          <div style={{ position: "relative", marginTop: 8 }}>
            {slashItems && <TokenPicker title={t("chat.picker.skills", "/ 技能")} items={slashItems} loading={skills.isLoading}
              empty={t("chat.picker.no_skills", "无匹配技能 — 在设置中安装或启用")} onPick={pickSlash} />}
            {atItems && <TokenPicker title={t("chat.picker.sources", "@ 引用知识源")} items={atItems} loading={kbSources.isLoading}
              empty={t("chat.picker.no_sources", "无匹配知识源")} onPick={pickAt} />}
            <Space.Compact style={{ width: "100%" }} block>
              <ChatComposerPlus value={sel} onChange={setSel} />
              <Upload accept="image/png,image/jpeg,image/webp" showUploadList={false}
                beforeUpload={(f) => { uploadStructure.mutate(f); return false; }}>
                <Tooltip title={t("chat.structure.upload", "上传化学结构式图片（MolScribe 识别 → 相似材料检索）")}>
                  <Button icon={<CameraOutlined />} loading={uploadStructure.isPending} disabled={busy} />
                </Tooltip>
              </Upload>
              <Input.TextArea value={draft} onChange={(e) => setDraft(e.target.value)} autoSize={{ minRows: 2, maxRows: 8 }}
                data-testid="chat-draft-input" maxLength={4000}
                placeholder={sel.mode === "evidence"
                  ? t("chat.placeholder.evidence", "文献综合模式：先检索再写，结论须可追溯…")
                  : t("chat.placeholder", "向知识库提问…（/ 选技能，@ 引用知识源，Enter 发送，Shift+Enter 换行）")}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    if (!busy) submit();
                  }
                }} />
              {busy
                ? <Button danger icon={<StopOutlined />} onClick={() => abortRef.current?.abort()}>{t("chat.stop", "停止")}</Button>
                : <Button type="primary" icon={<SendOutlined />} disabled={!draft.trim()} onClick={submit} data-testid="chat-send">{t("chat.send", "发送")}</Button>}
            </Space.Compact>
          </div>
          <Space style={{ marginTop: 8 }} wrap>
            <Tooltip title={t("chat.deep.tip", "先用 PageIndex 沿长文档目录树逐节阅读（实施例 / 配方表 ↔ 性能表跨页关联），再把读到的章节作为证据交给问答")}>
              <Space size={4}><Switch size="small" checked={deep} onChange={setDeep} data-testid="chat-deep" />{t("chat.deep", "深度模式")}</Space>
            </Tooltip>
            <Link to="/knowledge/reviews">{t("chat.review.all", "全部证据审计")}</Link>
          </Space>
          </div>
        </ProCard>
      </ProCard>
    </PageContainer>
  );
}

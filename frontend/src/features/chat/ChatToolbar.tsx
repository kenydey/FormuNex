import { FireOutlined } from "@ant-design/icons";
import { App, Button, Segmented, Space, Tag, Tooltip, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { errorText } from "@/shared/feedback";

interface WikiMode { current: string; choices: { value: string; label: string; desc: string }[] }

/** Wiki / Raw fusion weight of the chat's dual track (FORMUNEX_WIKI_CHAT_MODE). Claims still only trust Raw. */
export function WikiChatModeSelector() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["chat", "wiki-mode"], retry: false,
    queryFn: () => call<WikiMode>("GET", "/api/settings/wiki-chat-mode") });
  const set = useMutation({
    mutationFn: (mode: string) => call("POST", "/api/settings/wiki-chat-mode", { mode }),
    onSuccess: (_d, mode) => {
      qc.setQueryData<WikiMode>(["chat", "wiki-mode"], (old) => (old ? { ...old, current: mode } : old));
      message.success(t("chat.wiki_mode.saved", "已更新（下次问答生效）"));
    },
    onError: (e) => message.error(t("chat.wiki_mode.failed", "更新失败：{{e}}", { e: errorText(e) })),
  });
  if (!q.data) return null;
  return (
    <Tooltip title={t("chat.wiki_mode.tip", "Wiki / Raw 融合模式（不影响 Claims 只认 Raw）")}>
      <Space size={4} data-testid="wiki-chat-mode">
        <Typography.Text type="secondary">{t("chat.wiki_mode", "Wiki 模式")}</Typography.Text>
        <Segmented size="small" value={q.data.current} disabled={set.isPending} onChange={(v) => set.mutate(String(v))}
          options={q.data.choices.map((c) => ({ value: c.value, label: <Tooltip title={c.desc}>{c.label}</Tooltip> }))} />
      </Space>
    </Tooltip>
  );
}

interface Prewarm { status: string; backend: string | null; elapsed_ms: number | null; error: string | null }

/** RAG index warm-up status; offers a prewarm when idle or failed (first retrieval is slow otherwise). */
export function RagPrewarmBar() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["chat", "rag-status"], retry: false,
    queryFn: () => call<{ prewarm: Prewarm }>("GET", "/api/research/rag/status"),
    refetchInterval: (query) => (query.state.status === "error" ? false : query.state.data?.prewarm.status === "ready" ? 60_000 : 3000),
  });
  const run = useMutation({
    mutationFn: () => call<Prewarm>("POST", "/api/research/rag/prewarm?background=true", {}),
    onSuccess: (pw) => qc.setQueryData(["chat", "rag-status"], (old: { prewarm: Prewarm } | undefined) => ({ ...(old ?? {}), prewarm: pw })),
  });
  const pw = run.error
    ? { status: "failed", backend: null, elapsed_ms: null, error: errorText(run.error) }
    : q.data?.prewarm;
  if (!pw) return null;
  if (pw.status === "ready") {
    return <Tag color="success" data-testid="rag-prewarm-ready">
      {t("chat.rag.ready", "RAG 已就绪")}{pw.backend ? ` · ${pw.backend}` : ""}{pw.elapsed_ms != null ? ` · ${pw.elapsed_ms}ms` : ""}
    </Tag>;
  }
  if (pw.status === "failed" || pw.status === "idle") {
    return (
      <Space size={4} data-testid="rag-prewarm-bar">
        <Tag color={pw.status === "failed" ? "error" : "default"}>
          {pw.status === "failed"
            ? t("chat.rag.failed", "RAG 预热失败：{{e}}", { e: pw.error || "unknown" })
            : t("chat.rag.idle", "RAG 尚未预热（首次检索会更慢）")}
        </Tag>
        <Button size="small" icon={<FireOutlined />} loading={run.isPending} onClick={() => run.mutate()}>
          {t("chat.rag.prewarm", "预热 RAG")}
        </Button>
      </Space>
    );
  }
  return <Tag color="processing" icon={<FireOutlined />} data-testid="rag-prewarm-bar">
    {t("chat.rag.warming", "RAG 预热中…")} {pw.backend || ""} {pw.elapsed_ms != null ? `${pw.elapsed_ms}ms` : ""}
  </Tag>;
}

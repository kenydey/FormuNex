import { ProCard } from "@ant-design/pro-components";
import { Alert, App, Button, List, Progress, Space, Tag, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { isLargeDownload } from "./logic";
import type { EmbeddingDownloadTask, EmbeddingModel, EmbeddingSwitchStatus } from "./types";

const MODELS_KEY = ["settings", "embedding-models"] as const;
const SWITCH_KEY = ["settings", "embedding-switch"] as const;

/** Embedding model: download into the local cache, then switch (rebuilds the whole index — dimensions differ). */
export function EmbeddingCard() {
  const { t } = useTranslation();
  const { modal } = App.useApp();
  const qc = useQueryClient();
  const [download, setDownload] = useState<{ taskId: string; modelId: string } | null>(null);
  const [switching, setSwitching] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const models = useQuery({ queryKey: MODELS_KEY, queryFn: () => call<EmbeddingModel[]>("GET", "/api/embedding-models") });
  const dl = useQuery({
    queryKey: ["settings", "embedding-download", download?.taskId],
    queryFn: () => call<EmbeddingDownloadTask>("GET", `/api/embedding-models/tasks/${encodeURIComponent(download!.taskId)}`),
    enabled: !!download,
    refetchInterval: (q) => (q.state.data && ["done", "failed"].includes(q.state.data.status) ? false : 1000),
  });
  // Polled while a switch runs — also picks up a rebuild started elsewhere (status "running" on first load).
  const sw = useQuery({
    queryKey: SWITCH_KEY,
    queryFn: () => call<EmbeddingSwitchStatus>("GET", "/api/embedding-models/switch/status"),
    refetchInterval: (q) => (switching || q.state.data?.status === "running" ? 2000 : false),
  });

  useEffect(() => {
    const d = dl.data;
    if (!download || !d) return;
    if (d.status === "done") {
      setMsg({ ok: true, text: t("settings.emb.downloaded", "下载完成：{{m}}", { m: download.modelId }) });
      setDownload(null);
      void qc.invalidateQueries({ queryKey: MODELS_KEY });
    } else if (d.status === "failed") {
      setMsg({ ok: false, text: t("settings.emb.download_failed", "下载失败：{{e}}", { e: d.error || "?" }) });
      setDownload(null);
    }
  }, [dl.data, download, qc, t]);

  useEffect(() => {
    if (dl.error && download) {
      setMsg({ ok: false, text: errorText(dl.error) });
      setDownload(null);
    }
  }, [dl.error, download]);

  useEffect(() => {
    const s = sw.data;
    if (!switching || !s) return;
    if (s.status === "done") {
      setSwitching(false);
      setMsg({ ok: true, text: t("settings.emb.switched", "模型切换完成，索引已重建") });
      void qc.invalidateQueries({ queryKey: MODELS_KEY });
    } else if (s.status === "failed") {
      setSwitching(false);
      setMsg({ ok: false, text: t("settings.emb.switch_failed", "切换失败：{{e}}", { e: s.error || "?" }) });
    }
  }, [sw.data, switching, qc, t]);

  const startDownload = useMutation({
    mutationFn: (m: EmbeddingModel) => call<{ task_id: string; model_id: string }>("POST", "/api/embedding-models/download", { model_id: m.id }),
    onSuccess: (r, m) => setDownload({ taskId: r.task_id, modelId: m.id }),
    onError: (e) => setMsg({ ok: false, text: errorText(e) }),
  });
  const startSwitch = useMutation({
    mutationFn: (m: EmbeddingModel) => call("POST", "/api/embedding-models/switch", { model_id: m.id }),
    onSuccess: () => {
      setSwitching(true);
      void qc.invalidateQueries({ queryKey: SWITCH_KEY });
    },
    onError: (e) => setMsg({ ok: false, text: errorText(e) }),
  });

  const onDownload = (m: EmbeddingModel) => {
    setMsg(null);
    if (!isLargeDownload(m.size_estimate)) return startDownload.mutate(m);
    modal.confirm({
      title: t("settings.emb.confirm_download", "下载 {{m}}（约 {{s}}），确定吗？", { m: m.label, s: m.size_estimate }),
      onOk: () => startDownload.mutate(m),
    });
  };
  const onSwitch = (m: EmbeddingModel) => {
    setMsg(null);
    modal.confirm({
      title: t("settings.emb.confirm_switch", "切换到 {{m}}？", { m: m.label }),
      content: t("settings.emb.confirm_switch_body", "将重建全库索引（约 10-20 分钟），期间检索不可用。"),
      onOk: () => startSwitch.mutate(m),
    });
  };

  const running = switching || sw.data?.status === "running";
  const locked = running || !!download;
  return (
    <ProCard bordered title={t("settings.emb.title", "Embedding 模型")}
      subTitle={t("settings.emb.sub", "切换模型会重建全库索引（维度不同）；多语言模型（bge-m3）可解决跨语言召回问题")}>
      <ErrorAlert error={models.error} />
      {msg && <Alert type={msg.ok ? "success" : "error"} showIcon message={msg.text} style={{ marginBottom: 12 }} closable onClose={() => setMsg(null)} />}
      {running && (
        <Alert type="warning" showIcon style={{ marginBottom: 12 }}
          message={t("settings.emb.rebuilding", "索引重建中，期间检索不可用")}
          description={<Progress percent={Math.round(sw.data?.progress ?? 0)} size="small" />} />
      )}
      <List
        loading={models.isLoading}
        dataSource={models.data ?? []}
        rowKey="id"
        renderItem={(m) => (
          <List.Item actions={[
            m.current ? null : m.cached ? (
              <Button key="s" type="primary" size="small" disabled={locked} loading={startSwitch.isPending && startSwitch.variables?.id === m.id}
                onClick={() => onSwitch(m)}>{t("settings.emb.switch", "切换")}</Button>
            ) : (
              <Button key="d" size="small" disabled={locked} loading={startDownload.isPending && startDownload.variables?.id === m.id}
                onClick={() => onDownload(m)}>{t("settings.emb.download", "下载")}</Button>
            ),
          ].filter(Boolean)}>
            <List.Item.Meta
              title={<Space wrap>{m.label}
                {m.current && <Tag color="processing">{t("settings.emb.current", "当前使用")}</Tag>}
                {m.langs === "multi" && <Tag color="blue">{t("settings.emb.multi", "多语言推荐")}</Tag>}
                {m.cached && !m.current && <Tag>{t("settings.emb.cached", "已下载")}</Tag>}
              </Space>}
              description={
                <>
                  <Typography.Text type="secondary">{m.dim}d · {m.size_estimate} · {m.note}</Typography.Text>
                  {download?.modelId === m.id && <Progress percent={Number((dl.data?.progress ?? 0).toFixed(1))} size="small" />}
                </>
              }
            />
          </List.Item>
        )}
      />
    </ProCard>
  );
}

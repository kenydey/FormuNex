import { CheckCard, PageContainer, ProCard } from "@ant-design/pro-components";
import { Alert, App, Badge, Descriptions, Input, Space, Tag, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { SmilesDrawing } from "@/shared/Markdown";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { PARSE_PROFILES, missingForProfile } from "./logic";
import type { OcsrStatus, ParseAvailability } from "./types";

const PROFILE_KEY = ["settings", "parse-profile"];

/** Parse / retrieval profile (low · mid · cloud · high), applied server-side. */
export function ParseProfileSelector() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: PROFILE_KEY, queryFn: () => call<{ profile: string; availability: ParseAvailability }>("GET", "/api/settings/parse-profile") });
  const meta: Record<string, { title: string; desc: string }> = {
    low: { title: t("library.parse.low", "低配 · 纯 CPU"), desc: t("library.parse.low_desc", "Hybrid 本地解析（云 MinerU 关）+ BM25 + FAISS 检索。零 GPU / 云依赖，任何 VPS 可用。") },
    mid: { title: t("library.parse.mid", "中配 · CPU + 可选云"), desc: t("library.parse.mid_desc", "低配之上启用云 MinerU 页升级（表格 / 公式保真）+ GPU 检索自动探测（无 CUDA 自动回落）。") },
    cloud: { title: t("library.parse.cloud", "云端解析 · 弱机友好"), desc: t("library.parse.cloud_desc", "关本地 OCR，扫描 / 难页走云 MinerU（需 Token，耗配额）；文字 PDF 仍本地 hybrid；检索无 GPU 依赖。") },
    high: { title: t("library.parse.high", "高配 · GPU 主机"), desc: t("library.parse.high_desc", "全部本地、数据不出域：hybrid 版面解析优先（装了 Docling / marker 就用）+ 本地 OCR + GPU ColBERT。") },
  };
  const need = { gpu: t("library.parse.need_gpu", "CUDA GPU ≥ 4GB"), mineru: t("library.parse.need_mineru", "MinerU Token（云解析）") };
  const apply = useMutation({
    mutationFn: (p: string) => call<{ profile: string; persisted: boolean; availability: ParseAvailability }>("POST", "/api/settings/parse-profile", { profile: p }),
    onSuccess: (r) => {
      qc.setQueryData(PROFILE_KEY, { profile: r.profile, availability: r.availability });
      message.success(t("library.parse.applied", "已应用「{{p}}」。切换后需重启后端使检索后端完全生效。", { p: meta[r.profile]?.title ?? r.profile })
        + (r.persisted ? "" : t("library.parse.not_persisted", "（仅本次进程生效，环境文件只读）")));
    },
    onError: (e) => message.error(errorText(e)),
  });
  const avail = q.data?.availability ?? null;
  return (
    <ProCard bordered title={t("library.parse.title", "解析 / 检索档位")} loading={q.isLoading}
      subTitle={q.data && <Typography.Text type="secondary">{t("library.parse.current", "当前：{{p}}", { p: meta[q.data.profile]?.title ?? q.data.profile })}
        {avail?.active_rag_backend ? ` · ${t("library.parse.backend", "检索后端 {{b}}", { b: avail.active_rag_backend })}` : ""}</Typography.Text>}>
      <ErrorAlert error={q.error} />
      <CheckCard.Group value={q.data?.profile} disabled={apply.isPending} onChange={(v) => { if (typeof v === "string" && v !== q.data?.profile) apply.mutate(v); }}
        style={{ width: "100%" }}>
        {PARSE_PROFILES.map((p) => {
          const missing = missingForProfile(p, avail);
          return (
            <CheckCard key={p} value={p} title={meta[p].title} style={{ width: 260 }}
              description={<Space direction="vertical" size={4}>
                <span>{meta[p].desc}</span>
                <span>{missing.length ? missing.map((m) => <Tag key={m} color="warning">{t("library.parse.missing", "缺 {{m}} — 将降级", { m: need[m] })}</Tag>)
                  : <Tag color="success">{t("library.parse.ok", "条件满足")}</Tag>}</span>
              </Space>} />
          );
        })}
      </CheckCard.Group>
      {avail && !avail.vision_available && (
        <Alert type="info" showIcon message={t("library.parse.no_vision", "未配置视觉模型 — 扫描件中的图片型表格将只走 OCR 文本。")} />
      )}
    </ProCard>
  );
}

/** Offline chemical-structure recognition status; SMILES preview (no structure editor in this UI). */
export function OcsrPanel() {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["settings", "ocsr"], queryFn: () => call<{ status: OcsrStatus }>("GET", "/api/settings/ocsr") });
  const [smiles, setSmiles] = useState("c1ccccc1O");
  const st = q.data?.status;
  return (
    <ProCard bordered title={t("library.ocsr.title", "OCSR 离线结构识别")} loading={q.isLoading}>
      <Typography.Paragraph type="secondary">
        {t("library.ocsr.desc", "化学结构图优先离线识别为 SMILES（免 token，省费用），失败回退视觉 LLM。MolScribe（torch-cpu）跑在独立 worker，不占主服务内存。总开关为环境变量")}
        {" "}<Typography.Text code>FORMUNEX_OCSR_ENABLED</Typography.Text>
      </Typography.Paragraph>
      <ErrorAlert error={q.error} />
      {st && (
        <Descriptions size="small" bordered column={{ xs: 1, md: 2 }}>
          <Descriptions.Item label={t("library.ocsr.enabled", "总开关")}>
            <Badge status={st.enabled ? "success" : "default"} text={st.enabled ? t("library.ocsr.on", "已开启") : t("library.ocsr.off", "未开启")} />
          </Descriptions.Item>
          <Descriptions.Item label={t("library.ocsr.molscribe", "MolScribe 已安装")}>
            <Badge status={st.molscribe_installed ? "success" : "warning"}
              text={st.molscribe_installed ? t("library.ocsr.in_worker", "是（molscribe worker 内）") : t("library.ocsr.not_installed", "否（主进程）")} />
          </Descriptions.Item>
          <Descriptions.Item label={t("library.ocsr.queue", "队列")}><Typography.Text code>{st.molscribe_queue}</Typography.Text></Descriptions.Item>
          <Descriptions.Item label={t("library.ocsr.timeout", "超时")}>{st.molscribe_timeout_s}s</Descriptions.Item>
        </Descriptions>
      )}
      <Typography.Title level={5} style={{ marginTop: 16 }}>{t("library.ocsr.preview", "SMILES 预览")}</Typography.Title>
      <Space align="start" wrap>
        <Input.TextArea rows={3} style={{ width: 320, fontFamily: "monospace" }} value={smiles} onChange={(e) => setSmiles(e.target.value)}
          placeholder={t("library.ocsr.smiles_ph", "粘贴或编辑 SMILES")} />
        {smiles.trim() && <SmilesDrawing key={smiles.trim()} smiles={smiles.trim()} width={300} height={200} />}
      </Space>
    </ProCard>
  );
}

export default function ParsingPage() {
  const { t } = useTranslation();
  return (
    <PageContainer subTitle={t("library.parse.sub", "文档解析档位与离线化学结构识别")}>
      <Space direction="vertical" style={{ width: "100%" }} size="middle">
        <ParseProfileSelector />
        <OcsrPanel />
      </Space>
    </PageContainer>
  );
}

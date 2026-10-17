import { Alert, Button, Space } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { degradedReasons, infraState, UNREACHABLE_HEALTH, type InfraIssue } from "./logic";
import type { PlatformHealth } from "./types";

/** Settings tabs of the new UI (registered by the settings area). */
export const SETTINGS_MODEL_PATH = "/admin/settings/model";
export const SETTINGS_DEPS_PATH = "/admin/settings/deps";

/** /health every 30 s: rose banner for real outages, amber advisory for the soft-degraded local ELN ledger. */
export function InfraHealthBanner() {
  const { t } = useTranslation();
  const [dismissed, setDismissed] = useState<string | null>(null);
  const q = useQuery({
    queryKey: ["shell", "health"], refetchInterval: 30_000, retry: false,
    queryFn: () => call<PlatformHealth>("GET", "/health").catch(() => UNREACHABLE_HEALTH),
  });
  const st = infraState(q.data);
  if (st.level === "none" || dismissed === st.level) return null;
  const recheck = <Button size="small" loading={q.isFetching} onClick={() => void q.refetch()}>{t("shell.health.recheck", "重新检测")}</Button>;
  if (st.level === "local") {
    return <Alert banner closable type="warning" data-testid="infra-health-local-ledger" onClose={() => setDismissed("local")}
      message={<Space wrap><b>{t("shell.health.local_ledger", "本地台账")}</b>
        {st.hint || t("shell.health.local_hint", "Datalab 不可达；实验台账使用本地 sqlite（soft-degrade）。需要 ELN 时启动 compose 并打开 DATALAB_REQUIRED。")}</Space>}
      action={recheck} />;
  }
  const label = (i: InfraIssue) => {
    switch (i.code) {
      case "database": return t("shell.health.database", "数据库不可用");
      case "datalab": return i.hint ? t("shell.health.datalab_hint", "Datalab ELN：{{h}}", { h: i.hint }) : t("shell.health.datalab", "Datalab ELN 不可达");
      case "broker": return t("shell.health.broker", "任务队列（Redis/Celery）不可达");
      case "pdf": return t("shell.health.pdf", "PDF 解析器未就绪");
      default: return t("shell.health.generic", "平台健康检查返回 degraded");
    }
  };
  return <Alert banner closable type="error" data-testid="infra-health-banner" onClose={() => setDismissed("degraded")}
    message={<Space wrap><b>{t("shell.health.degraded", "基础设施降级")}</b>{st.issues.map(label).join(" · ")}</Space>} action={recheck} />;
}

/** Offline / degraded mode: no LLM key, or the online-retrieval libraries missing (offline fallbacks still run). */
export function DegradedBanner() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [dismissed, setDismissed] = useState(false);
  const settings = useQuery({ queryKey: ["shell", "llm-settings"], retry: false, staleTime: 300_000,
    queryFn: () => call<{ key_set?: boolean }>("GET", "/api/settings") });
  const status = useQuery({ queryKey: ["shell", "search-status"], retry: false, staleTime: 300_000,
    queryFn: () => call<Record<string, { available?: boolean }>>("GET", "/api/search/status") });
  const reasons = degradedReasons(settings.data, status.data);
  if (dismissed || !reasons.length) return null;
  const text = reasons.map((r) => (r === "llm_key" ? t("shell.degraded.llm_key", "未配置大模型 API Key") : t("shell.degraded.deps", "在线检索依赖未安装")));
  return <Alert banner closable type="warning" data-testid="degraded-banner" onClose={() => setDismissed(true)}
    message={<Space wrap><b>{t("shell.degraded.title", "离线降级模式")}</b>{text.join(" · ")}
      {t("shell.degraded.desc", "—— 深度研究、在线检索、跨源交叉验证等功能以离线兜底运行。")}</Space>}
    action={<Button size="small" onClick={() => navigate(reasons.includes("llm_key") ? SETTINGS_MODEL_PATH : SETTINGS_DEPS_PATH)}>
      {reasons.includes("llm_key") ? t("shell.degraded.go_model", "去配置大模型") : t("shell.degraded.go_deps", "去安装依赖")}
    </Button>} />;
}

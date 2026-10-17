import { ProCard } from "@ant-design/pro-components";
import { Alert, AutoComplete, Button, Flex, Form, Input, Select, Skeleton, Space, Typography } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { CUSTOM_PROVIDER, modelOptions, recommendedModel, showsBaseUrl } from "./logic";
import type { ConnectionTest, LlmModelOption, LlmProviderInfo, LlmSettings, ModelsRefresh, VisionSettings } from "./types";
import { EmbeddingCard } from "./EmbeddingCard";

export const LLM_KEY = ["settings", "llm"] as const;

interface Outcome { ok: boolean; message: string }

function OutcomeAlert({ outcome }: { outcome: Outcome | null }) {
  if (!outcome) return null;
  return <Alert type={outcome.ok ? "success" : "error"} showIcon message={outcome.message} style={{ marginBottom: 12 }} />;
}

function ModelPicker({ provider, value, onChange, options, refreshing, onRefresh, hint, freeText }: {
  provider: string; value: string; onChange: (v: string) => void; options: LlmModelOption[]; refreshing: boolean;
  onRefresh: () => void; hint: string | null; freeText: boolean;
}) {
  const { t } = useTranslation();
  const opts = options.map((m) => ({ value: m.id, label: m.recommended ? `${m.label} ★` : m.label }));
  return (
    <Form.Item
      label={
        <Space>
          {t("settings.llm.model", "模型")}
          <Button size="small" icon={<ReloadOutlined />} loading={refreshing} onClick={onRefresh} disabled={!provider && !freeText}>
            {t("settings.llm.refresh_models", "更新列表")}
          </Button>
        </Space>
      }
      extra={hint}
    >
      {freeText ? (
        <AutoComplete value={value} onChange={onChange} options={opts}
          placeholder={t("settings.vision.custom_model_ph", "tgi（TGI 占位符）或 Qwen/Qwen2.5-VL-7B-Instruct（vLLM）")} />
      ) : (
        <Select value={value || undefined} onChange={onChange} options={opts} showSearch disabled={opts.length === 0}
          placeholder={t("settings.llm.no_models", "（无可用选项）")} />
      )}
    </Form.Item>
  );
}

/** Text model (研究问答、配方推荐…): provider / model / key / base URL, save-and-test. */
function TextModelCard({ data }: { data: LlmSettings }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [provider, setProvider] = useState(data.provider);
  const [model, setModel] = useState(data.model);
  const [baseUrl, setBaseUrl] = useState(data.base_url ?? "");
  const [apiKey, setApiKey] = useState("");
  const [refreshed, setRefreshed] = useState<{ provider: string; models: LlmModelOption[]; message: string } | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);

  useEffect(() => {
    setProvider(data.provider);
    setModel(data.model);
    setBaseUrl(data.base_url ?? "");
  }, [data.provider, data.model, data.base_url]);

  const current = data.providers.find((p) => p.id === provider);
  const options = modelOptions(refreshed?.provider === provider && refreshed.models.length ? refreshed.models : current?.models, model);

  const refresh = useMutation({
    mutationFn: () => call<ModelsRefresh>("POST", "/api/settings/models/refresh", { provider, baseUrl: baseUrl || undefined, model }),
    onSuccess: (r) => {
      setRefreshed({ provider, models: r.models, message: r.message });
      if (r.models.length > 0 && !r.models.some((m) => m.id === model)) setModel(recommendedModel(r.models));
    },
    onError: (e) => setRefreshed({ provider, models: [], message: errorText(e) }),
  });
  const save = useMutation({
    mutationFn: () => call<ConnectionTest>("POST", "/api/settings", {
      provider, model, api_key: apiKey.trim() || undefined, baseUrl: showsBaseUrl(current, provider) ? baseUrl : undefined,
    }),
    onSuccess: (r) => {
      setApiKey("");
      setOutcome({ ok: r.ok, message: r.message });
      void qc.invalidateQueries({ queryKey: LLM_KEY });
    },
    onError: (e) => setOutcome({ ok: false, message: errorText(e) }),
  });
  const test = useMutation({
    mutationFn: () => call<ConnectionTest>("POST", "/api/settings/test", {}),
    onSuccess: (r) => setOutcome({ ok: r.ok, message: r.message || `${r.provider}/${r.model}` }),
    onError: (e) => setOutcome({ ok: false, message: errorText(e) }),
  });

  function onProviderChange(next: string) {
    const p = data.providers.find((x) => x.id === next);
    setProvider(next);
    setModel(recommendedModel(p?.models));
    // A custom endpoint has no catalog default: clear rather than carry the previous provider's URL over.
    setBaseUrl(p?.base_url ?? "");
    setRefreshed(null);
    setOutcome(null);
  }

  const busy = save.isPending || test.isPending;
  return (
    <ProCard bordered title={t("settings.llm.text_title", "文本模型 · Text")}
      subTitle={t("settings.llm.text_sub", "研究问答、配方推荐、深度研究、意图解析等所有纯文本任务")}>
      <Form layout="vertical" style={{ maxWidth: 640 }}>
        <Form.Item label={t("settings.llm.provider", "供应商")}>
          <Select value={provider} onChange={onProviderChange} options={data.providers.map((p) => ({ value: p.id, label: p.label }))} />
        </Form.Item>
        <ModelPicker provider={provider} value={model} onChange={setModel} options={options} refreshing={refresh.isPending}
          onRefresh={() => refresh.mutate()} hint={refreshed?.provider === provider ? refreshed.message : null}
          freeText={provider === CUSTOM_PROVIDER} />
        <Form.Item label="API Key" extra={data.key_set && !apiKey ? t("settings.llm.key_saved", "当前供应商密钥已写入服务器 .env（不回显）") : undefined}>
          <Input.Password value={apiKey} onChange={(e) => setApiKey(e.target.value)} autoComplete="new-password"
            placeholder={data.key_set ? t("settings.llm.key_set_ph", "已配置 — 输入新密钥以覆盖") : "sk-..."} />
        </Form.Item>
        {showsBaseUrl(current, provider) && (
          <Form.Item label={t("settings.llm.base_url", "自定义 Base URL（可选）")}
            extra={t("settings.llm.base_url_hint", "修改 Base URL 后请点击「更新列表」同步远端可用模型。")}>
            <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder={current?.base_url ?? "https://..."} />
          </Form.Item>
        )}
        <OutcomeAlert outcome={outcome} />
        <Space>
          <Button onClick={() => test.mutate()} loading={test.isPending} disabled={busy}>
            {t("settings.llm.test_only", "仅测试连接")}
          </Button>
          <Button type="primary" onClick={() => save.mutate()} loading={save.isPending} disabled={busy || !provider}>
            {t("settings.llm.save_test", "保存并测试连接")}
          </Button>
        </Space>
      </Form>
    </ProCard>
  );
}

const INHERIT = "";

/** Vision model, configured separately: many strong text models (e.g. DeepSeek) do not accept images. */
function VisionModelCard({ providers, vision }: { providers: LlmProviderInfo[]; vision: VisionSettings }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [provider, setProvider] = useState(vision.provider ?? INHERIT);
  const [model, setModel] = useState(vision.inherits ? "" : vision.model ?? "");
  const [baseUrl, setBaseUrl] = useState(vision.base_url ?? "");
  const [apiKey, setApiKey] = useState("");
  const [refreshed, setRefreshed] = useState<{ provider: string; models: LlmModelOption[]; message: string } | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);

  // Server state is the source of truth; mirror it on load. Deliberately keeps ``outcome``: a save refetches and
  // lands here, and clearing it would wipe the confirmation the save just set.
  useEffect(() => {
    setProvider(vision.provider ?? INHERIT);
    setModel(vision.inherits ? "" : vision.model ?? "");
    setBaseUrl(vision.base_url ?? "");
    setApiKey("");
  }, [vision]);

  const inheriting = provider === INHERIT;
  const isCustom = provider === CUSTOM_PROVIDER;
  const current = providers.find((p) => p.id === provider);
  const options = modelOptions(refreshed?.provider === provider && refreshed.models.length ? refreshed.models : current?.models, model);

  const refresh = useMutation({
    mutationFn: () => call<ModelsRefresh>("POST", "/api/settings/models/refresh", { provider, baseUrl: baseUrl || undefined, model }),
    onSuccess: (r) => {
      setRefreshed({ provider, models: r.models, message: r.message });
      if (r.models.length > 0 && !r.models.some((m) => m.id === model)) setModel(recommendedModel(r.models));
    },
    onError: (e) => setRefreshed({ provider, models: [], message: errorText(e) }),
  });
  const save = useMutation({
    mutationFn: () => call("POST", "/api/settings/vision", {
      provider, model: inheriting ? "" : model, api_key: apiKey.trim() || undefined, baseUrl: inheriting ? "" : baseUrl,
    }),
    onSuccess: () => {
      setApiKey("");
      setOutcome({ ok: true, message: inheriting ? t("settings.vision.saved_inherit", "已改为跟随文本模型")
        : t("settings.vision.saved", "已保存（点「测试视觉」验证是否真的能读图）") });
      void qc.invalidateQueries({ queryKey: LLM_KEY });
    },
    onError: (e) => setOutcome({ ok: false, message: errorText(e) }),
  });
  const probe = useMutation({
    mutationFn: () => call<ConnectionTest>("POST", "/api/settings/vision/test", {}),
    onSuccess: (r) => setOutcome({ ok: r.ok, message: r.message }),
    onError: (e) => setOutcome({ ok: false, message: errorText(e) }),
  });

  function onProviderChange(next: string) {
    setProvider(next);
    setOutcome(null);
    setRefreshed(null);
    if (next === INHERIT) {
      setModel("");
      setBaseUrl("");
      return;
    }
    const p = providers.find((x) => x.id === next);
    setModel(recommendedModel(p?.models));
    setBaseUrl(p?.base_url ?? "");
  }

  return (
    <ProCard bordered title={t("settings.vision.title", "视觉模型 · Vision")}
      subTitle={t("settings.vision.sub", "化学结构图、图表、未能渲染的表格与上传图片；可与文本模型选不同供应商")}>
      <Form layout="vertical" style={{ maxWidth: 640 }}>
        <Form.Item label={t("settings.llm.provider", "供应商")}>
          <Select value={provider} onChange={onProviderChange} options={[
            { value: INHERIT, label: t("settings.vision.inherit", "跟随文本模型") },
            ...providers.map((p) => ({ value: p.id, label: p.label })),
          ]} />
        </Form.Item>
        {inheriting ? (
          <Typography.Paragraph type="secondary">
            {t("settings.vision.inherit_hint", "当前与文本模型共用同一供应商与密钥（模型 {{m}}）。若该模型不支持图片，请另选一个供应商。",
              { m: vision.model || "—" })}
          </Typography.Paragraph>
        ) : (
          <>
            <ModelPicker provider={provider} value={model} onChange={setModel} options={options} refreshing={refresh.isPending}
              onRefresh={() => refresh.mutate()} hint={refreshed?.provider === provider ? refreshed.message : null} freeText={isCustom} />
            {(isCustom || current?.base_url != null) && (
              <Form.Item label="Base URL" extra={isCustom
                ? t("settings.vision.hf_hint", "HuggingFace Endpoints 需以 /v1 结尾——只填到域名时会自动补上。")
                : t("settings.vision.base_url_hint", "可覆盖默认端点，如百炼 Token Plan 专属地址")}>
                <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder={current?.base_url ?? "https://..."} />
              </Form.Item>
            )}
            <Form.Item label="API Key" extra={isCustom ? t("settings.vision.key_hf", "HuggingFace 令牌 hf_…")
              : t("settings.vision.key_reuse", "留空则复用该供应商已保存的密钥")}>
              <Input.Password value={apiKey} onChange={(e) => setApiKey(e.target.value)} autoComplete="new-password"
                placeholder={vision.key_set ? t("settings.llm.key_set_ph", "已配置 — 输入新密钥以覆盖") : "hf_... / sk-..."} />
            </Form.Item>
          </>
        )}
        <OutcomeAlert outcome={outcome} />
        {!outcome && !vision.configured && vision.hint && <Alert type="warning" showIcon message={vision.hint} style={{ marginBottom: 12 }} />}
        <Space>
          <Button onClick={() => probe.mutate()} loading={probe.isPending} disabled={!vision.configured}
            title={vision.configured ? t("settings.vision.probe_tip", "发送一张测试图，验证模型是否真的能读图") : vision.hint}>
            {t("settings.vision.probe", "测试视觉")}
          </Button>
          <Button type="primary" onClick={() => save.mutate()} loading={save.isPending}>
            {t("settings.vision.save", "保存视觉设置")}
          </Button>
        </Space>
      </Form>
    </ProCard>
  );
}

export function ModelTab() {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: LLM_KEY, queryFn: () => call<LlmSettings>("GET", "/api/settings") });
  return (
    <Flex vertical gap={16}>
      <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
        {t("settings.llm.intro", "文本任务与视觉任务分开配置，系统按任务类型自动调用对应模型。API Key 保存在服务器 .env 中，页面不回显。")}
      </Typography.Paragraph>
      <ErrorAlert error={q.error} />
      {q.isLoading && <Skeleton active />}
      {q.data && (
        <>
          <TextModelCard data={q.data} />
          <VisionModelCard providers={q.data.providers} vision={q.data.vision} />
        </>
      )}
      <EmbeddingCard />
    </Flex>
  );
}

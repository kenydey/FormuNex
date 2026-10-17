import { ProCard } from "@ant-design/pro-components";
import { App, Button, Flex, Input, List, Popconfirm, Skeleton, Space, Tag, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { call } from "@/api/client";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { groupBy } from "./logic";
import type { SecretStatus } from "./types";

const SECRETS_KEY = ["settings", "secrets"] as const;

/** Groups whose entries have a server-side probe (probe_secret). */
const TESTABLE_GROUPS = new Set(["search", "patent", "research", "parse", "observability"]);

function groupLabel(t: TFunction, g: string): string {
  switch (g) {
    case "llm": return t("settings.keys.group_llm", "大模型");
    case "search": return t("settings.keys.group_search", "检索增强");
    case "patent": return t("settings.keys.group_patent", "专利数据源");
    case "research": return t("settings.keys.group_research", "学术 / 研究");
    case "parse": return t("settings.keys.group_parse", "文档解析");
    case "infra": return t("settings.keys.group_infra", "基础设施");
    case "observability": return t("settings.keys.group_observability", "可观测性 / 追踪");
    default: return g;
  }
}

/** One secret: shows only what the server returns (masked for keys); a new value is sent only when typed. */
function SecretRow({ s }: { s: SecretStatus }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const save = useMutation({
    mutationFn: (value: string | null) => call<{ secrets: SecretStatus[] }>("POST", "/api/settings/secrets", { updates: { [s.id]: value } }),
    onSuccess: (r, value) => {
      qc.setQueryData(SECRETS_KEY, { secrets: r.secrets });
      setEditing(false);
      setDraft("");
      message.success(value ? t("settings.keys.saved", "已保存到服务器 .env") : t("settings.keys.cleared", "已清除"));
    },
    onError: (e) => setResult({ ok: false, text: errorText(e) }),
  });
  const test = useMutation({
    mutationFn: () => call<{ ok: boolean; message: string }>("POST", "/api/settings/secrets/test", { id: s.id }),
    onSuccess: (r) => setResult({ ok: r.ok, text: r.message }),
    onError: (e) => setResult({ ok: false, text: errorText(e) }),
  });
  const InputC = s.secret === false ? Input : Input.Password;
  return (
    <List.Item
      actions={[
        editing ? (
          <Space key="e">
            <Button size="small" type="primary" disabled={!draft.trim()} loading={save.isPending} onClick={() => save.mutate(draft.trim())}>
              {t("common.save", "保存")}
            </Button>
            <Button size="small" onClick={() => { setEditing(false); setDraft(""); }}>{t("settings.cancel", "取消")}</Button>
          </Space>
        ) : (
          <Button key="m" size="small" onClick={() => { setEditing(true); setResult(null); }}>{t("settings.keys.modify", "修改")}</Button>
        ),
        TESTABLE_GROUPS.has(s.group) && s.set ? (
          <Button key="t" size="small" loading={test.isPending} onClick={() => test.mutate()}>{t("settings.keys.test", "测试")}</Button>
        ) : null,
        s.set && !editing ? (
          <Popconfirm key="c" title={t("settings.keys.confirm_clear", "清除该配置？")} onConfirm={() => save.mutate(null)}>
            <Button size="small" danger type="text">{t("settings.keys.clear", "清除")}</Button>
          </Popconfirm>
        ) : null,
      ].filter(Boolean)}
    >
      <List.Item.Meta
        title={<Space wrap>{s.label}{s.set ? <Tag color="success">{t("settings.keys.set", "已配置")}</Tag> : <Tag>{t("settings.keys.unset", "未配置")}</Tag>}</Space>}
        description={
          <Flex vertical gap={4}>
            <Typography.Text type="secondary" code>{s.env_key}</Typography.Text>
            {editing ? (
              <InputC autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} autoComplete="new-password"
                placeholder={s.masked ? t("settings.keys.current", "当前 {{v}}", { v: s.masked })
                  : s.secret === false ? t("settings.keys.new_value", "输入新值") : t("settings.keys.new_key", "输入新密钥")}
                onPressEnter={() => draft.trim() && save.mutate(draft.trim())} />
            ) : (
              <Typography.Text type="secondary">{s.masked || "—"}</Typography.Text>
            )}
            {result && <Typography.Text type={result.ok ? "success" : "danger"}>{result.text}</Typography.Text>}
          </Flex>
        }
      />
    </List.Item>
  );
}

/** API keys & service addresses grouped by purpose (LLM / retrieval routing / patents / parsing / …). */
export function ApiKeysTab() {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: SECRETS_KEY, queryFn: () => call<{ secrets: SecretStatus[] }>("GET", "/api/settings/secrets") });
  const groups = groupBy(q.data?.secrets ?? [], (s) => s.group);
  return (
    <Flex vertical gap={16}>
      <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
        {t("settings.keys.intro", "API 密钥保存在服务器 .env 文件中，页面只显示掩码。修改后点击「保存」即时生效；Celery Worker 需重启后读取新密钥。")}
      </Typography.Paragraph>
      <ErrorAlert error={q.error} />
      {q.isLoading && <Skeleton active />}
      {groups.map(([g, items]) => (
        <ProCard key={g} bordered title={groupLabel(t, g)} size="small">
          <List rowKey="id" dataSource={items} renderItem={(s) => <SecretRow s={s} />} />
        </ProCard>
      ))}
    </Flex>
  );
}

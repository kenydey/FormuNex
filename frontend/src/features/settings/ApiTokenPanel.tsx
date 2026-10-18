import { ProCard } from "@ant-design/pro-components";
import { Alert, App, Button, Flex, Input, Popconfirm, Skeleton, Space, Tag, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { ErrorAlert, errorText } from "@/shared/feedback";

const API_TOKEN_KEY = ["settings", "api-token"] as const;

interface ApiTokenStatus { set: boolean; masked: string; auth_enabled: boolean }

/** 平台自身的 bearer token（FORMUNEX_API_TOKEN）：给脚本 / API 客户端用。

旧前端 ApiAccessPanel 是浏览器端存 token（让浏览器带上 Bearer）；新前端 UI 会话走 JWT，
不需要它。这里管理的是服务端 token 本身：保存进服务器 .env，脚本调用方用
``Authorization: Bearer <token>``。页面只显示掩码，完整 token 永不回显。 */
export function ApiTokenPanel() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const q = useQuery({
    queryKey: API_TOKEN_KEY,
    queryFn: () => call<ApiTokenStatus>("GET", "/api/settings/api-token"),
  });
  const save = useMutation({
    mutationFn: (token: string | null) => call<ApiTokenStatus>("POST", "/api/settings/api-token", { token }),
    onSuccess: (r, token) => {
      qc.setQueryData(API_TOKEN_KEY, r);
      setEditing(false);
      setDraft("");
      message.success(token ? t("settings.api_token.saved", "已保存到服务器 .env，立即生效") : t("settings.api_token.cleared", "已清除"));
    },
    onError: (e) => setResult({ ok: false, text: errorText(e) }),
  });
  const status = q.data;
  return (
    <Flex vertical gap={16}>
      <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
        {t("settings.api_token.intro", "平台自身的 API 访问令牌，供脚本与 API 客户端以 Authorization: Bearer 调用。UI 会话走登录 JWT，不用它。令牌保存在服务器 .env，页面只显示掩码。")}
      </Typography.Paragraph>
      {!q.isLoading && status && !status.auth_enabled && (
        <Alert type="info" showIcon
          message={t("settings.api_token.auth_off", "当前 API 鉴权未启用：令牌暂不起作用，启用鉴权（FORMUNEX_API_AUTH_ENABLED=true）后生效。")} />
      )}
      <ErrorAlert error={q.error} />
      {q.isLoading && <Skeleton active />}
      {status && (
        <ProCard bordered title={t("settings.api_token.title", "API 访问令牌")} size="small">
          <Flex vertical gap={8}>
            <Space wrap>
              {status.set
                ? <Tag color="success">{t("settings.keys.set", "已配置")}</Tag>
                : <Tag>{t("settings.keys.unset", "未配置")}</Tag>}
              <Typography.Text type="secondary" code>FORMUNEX_API_TOKEN</Typography.Text>
            </Space>
            {editing ? (
              <Input.Password autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} autoComplete="new-password"
                placeholder={status.masked
                  ? t("settings.api_token.current", "当前 {{v}}，输入新令牌替换", { v: status.masked })
                  : t("settings.api_token.new_token", "输入新令牌（单个无空白字符串）")}
                onPressEnter={() => draft.trim() && save.mutate(draft.trim())} />
            ) : (
              <Typography.Text type="secondary">{status.masked || "—"}</Typography.Text>
            )}
            <Space>
              {editing ? (
                <>
                  <Button type="primary" size="small" disabled={!draft.trim()} loading={save.isPending}
                    onClick={() => save.mutate(draft.trim())}>{t("common.save", "保存")}</Button>
                  <Button size="small" onClick={() => { setEditing(false); setDraft(""); }}>{t("settings.cancel", "取消")}</Button>
                </>
              ) : (
                <Button size="small" onClick={() => { setEditing(true); setResult(null); }}>{t("settings.keys.modify", "修改")}</Button>
              )}
              {status.set && !editing && (
                <Popconfirm title={t("settings.api_token.confirm_clear", "清除平台令牌？脚本调用将立即失效。")}
                  onConfirm={() => save.mutate(null)}>
                  <Button size="small" danger type="text" loading={save.isPending}>{t("settings.keys.clear", "清除")}</Button>
                </Popconfirm>
              )}
            </Space>
            {result && <Typography.Text type={result.ok ? "success" : "danger"}>{result.text}</Typography.Text>}
            <Typography.Text type="secondary">
              {t("settings.api_token.usage", "脚本用法：curl -H \"Authorization: Bearer <令牌>\" <后端地址>/api/...")}
            </Typography.Text>
          </Flex>
        </ProCard>
      )}
    </Flex>
  );
}

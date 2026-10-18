import { ProCard } from "@ant-design/pro-components";
import { Alert, App, Button, Descriptions, Flex, List, Segmented, Skeleton, Space, Tag, Typography, theme } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { envFlagUpdates, groupBy } from "./logic";
import type { EnvFlag, EnvFlagsResponse } from "./types";
import { ENV_FLAGS_KEY } from "./useEnvFlag";

/** FORMUNEX_* feature toggles (written to the process env and .env), plus read-only numeric parameters. */
export function EnvFlagsTab() {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const focus = params.get("focus");
  const q = useQuery({ queryKey: ENV_FLAGS_KEY, queryFn: () => call<EnvFlagsResponse>("GET", "/api/settings/env-flags") });
  const flags = useMemo(() => q.data?.flags ?? [], [q.data]);
  const [drafts, setDrafts] = useState<Record<string, boolean>>({});

  useEffect(() => {
    setDrafts(Object.fromEntries(flags.map((f) => [f.attr, f.value])));
  }, [flags]);

  // ?focus=<attr> (deep link from reports): scroll the toggle into view.
  useEffect(() => {
    if (!focus || flags.length === 0) return;
    document.getElementById(`env-flag-${focus}`)?.scrollIntoView?.({ block: "center", behavior: "smooth" });
  }, [focus, flags]);

  const updates = envFlagUpdates(flags, drafts);
  const dirty = Object.keys(updates).length;
  const save = useMutation({
    mutationFn: () => call<{ updated: string[]; rejected: string[]; flags: EnvFlag[] }>("POST", "/api/settings/env-flags", { updates }),
    onSuccess: (r) => {
      qc.setQueryData<EnvFlagsResponse>(ENV_FLAGS_KEY, (old) => ({ ...(old ?? {}), flags: r.flags }));
      message.success(t("settings.env.saved", "已更新 {{n}} 项 — 写入 .env 并即时生效", { n: r.updated.length }));
      if (r.rejected.length) message.warning(t("settings.env.rejected", "未接受：{{k}}", { k: r.rejected.join(", ") }));
      if (focus) {
        params.delete("focus");
        setParams(params, { replace: true });
      }
    },
    onError: (e) => message.error(errorText(e)),
  });

  const groups = groupBy(flags, (f) => f.category_label || f.category);
  return (
    <Flex vertical gap={16}>
      <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
        {t("settings.env.intro", "以下功能开关对应服务器的 FORMUNEX_* 环境变量。保存后写入进程环境与 .env 文件，对后续请求即时生效并在重启后保持（个别项需重启，见提示）。")}
      </Typography.Paragraph>
      <ErrorAlert error={q.error} />
      {q.isLoading && <Skeleton active />}
      <Space style={{ position: "sticky", top: 0, zIndex: 2, background: token.colorBgLayout, padding: "8px 0" }}>
        <Button type="primary" disabled={dirty === 0} loading={save.isPending} onClick={() => save.mutate()}>
          {t("settings.env.save", "保存并生效")}
        </Button>
        <Button onClick={() => setDrafts(Object.fromEntries(flags.map((f) => [f.attr, f.default])))} disabled={flags.length === 0}>
          {t("settings.env.reset", "恢复默认值")}
        </Button>
        <Button onClick={() => setDrafts(Object.fromEntries(flags.map((f) => [f.attr, f.value])))} disabled={dirty === 0}>
          {t("settings.env.discard", "放弃修改")}
        </Button>
        {dirty > 0 && <Typography.Text type="warning">{t("settings.env.dirty", "{{n}} 项未保存", { n: dirty })}</Typography.Text>}
      </Space>
      {groups.map(([label, items]) => (
        <ProCard key={label} title={label} bordered size="small">
          <List rowKey="attr" dataSource={items} renderItem={(f) => {
            const draft = drafts[f.attr] ?? f.value;
            const focused = focus === f.attr;
            return (
              <List.Item id={`env-flag-${f.attr}`} style={focused ? { background: token.colorPrimaryBg, paddingInline: 8 } : undefined}
                actions={[
                  <Segmented key="v" size="small" value={draft ? "true" : "false"}
                    onChange={(v) => setDrafts((d) => ({ ...d, [f.attr]: v === "true" }))}
                    options={[{ value: "true", label: "True" }, { value: "false", label: "False" }]} />,
                ]}>
                <List.Item.Meta
                  title={<Space wrap size={4}>
                    {f.label}
                    <Typography.Text type="secondary" code>{f.env_key}</Typography.Text>
                    {f.maturity && f.maturity !== "stable" && <Tag color={f.maturity === "experimental" ? "error" : "warning"}>{f.maturity}</Tag>}
                    {draft !== f.value && <Tag color="processing">{t("settings.env.modified", "已修改")}</Tag>}
                    {draft !== f.default && <Tag color="gold">{t("settings.env.non_default", "非默认")}</Tag>}
                  </Space>}
                  description={<>{f.description}{f.hint && <Typography.Text type="warning">（{f.hint}）</Typography.Text>}</>}
                />
              </List.Item>
            );
          }} />
        </ProCard>
      ))}
      {(q.data?.vars?.length ?? 0) > 0 && (
        <ProCard bordered size="small" title={t("settings.env.vars", "数值参数 · 只读")}
          subTitle={t("settings.env.vars_hint", "改 .env 后重启生效")}>
          <Descriptions size="small" column={1} bordered items={(q.data?.vars ?? []).map((v) => ({
            key: v.attr,
            label: <Space direction="vertical" size={0}>{v.label}<Typography.Text type="secondary" code>{v.env_key}</Typography.Text></Space>,
            children: <Space direction="vertical" size={0}>
              <Typography.Text strong>{v.value ?? "—"}</Typography.Text>
              <Typography.Text type="secondary">{t("settings.env.default", "默认 {{v}}", { v: v.default ?? "—" })} · {v.description}</Typography.Text>
            </Space>,
          }))} />
        </ProCard>
      )}
      {flags.length === 0 && !q.isLoading && !q.error && <Alert type="info" message={t("settings.env.none", "没有可配置的开关")} />}
    </Flex>
  );
}

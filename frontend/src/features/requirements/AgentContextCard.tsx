import { ProCard } from "@ant-design/pro-components";
import { App, Button, Input, Space, Typography } from "antd";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { errorText } from "@/shared/feedback";
import { historyKey } from "./ProjectHistory";

/**
 * 项目 Agent 上下文 (legacy ProjectAgentContext): free text injected into this project's agent prompts
 * (evidence synthesis / Q&A). Saved on its own (presence-based merge PUT), so unsaved spec edits are untouched.
 */
export function AgentContextCard({ projectId, value }: { projectId: string; value: string }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const [saved, setSaved] = useState(value);
  const [draft, setDraft] = useState(value);
  useEffect(() => { setSaved(value); setDraft(value); }, [value, projectId]);
  const save = useMutation({
    mutationFn: (text: string) => call("PUT", `/api/projects/${encodeURIComponent(projectId)}`, { workspace: { agent_context: text } }),
    onSuccess: (_d, text) => {
      setSaved(text);
      message.success(t("requirements.agent.saved", "Agent 上下文已保存"));
      void qc.invalidateQueries({ queryKey: historyKey(projectId) });
    },
    onError: (e) => message.error(errorText(e)),
  });
  const dirty = draft !== saved;
  return (
    <ProCard size="small" bordered title={t("requirements.agent.title", "Agent 上下文")}>
      <Typography.Paragraph type="secondary">
        {t("requirements.agent.help", "保存后自动注入本项目 Agent prompt（证据综合 / 问答时生效）；留空则不注入。")}
      </Typography.Paragraph>
      <Input.TextArea data-testid="agent-context-textarea" rows={8} value={draft} onChange={(e) => setDraft(e.target.value)} style={{ maxWidth: 800 }}
        placeholder={t("requirements.agent.placeholder", "例如：\n- 本项目聚焦水性环氧防腐涂料\n- 优先引用近 5 年文献\n- 回答用中文，数值保留 2 位小数")} />
      <Space style={{ marginTop: 12 }}>
        <Button type="primary" disabled={!dirty} loading={save.isPending} onClick={() => save.mutate(draft)}>{t("common.save", "保存")}</Button>
        <Button disabled={!dirty || save.isPending} onClick={() => setDraft(saved)}>{t("requirements.agent.revert", "撤销修改")}</Button>
        {dirty && <Typography.Text type="warning">{t("requirements.agent.dirty", "有未保存的修改")}</Typography.Text>}
      </Space>
    </ProCard>
  );
}

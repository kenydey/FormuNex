import { SaveOutlined } from "@ant-design/icons";
import { App, Button, Space, Tooltip, Typography } from "antd";
import { useMutation } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useUi } from "@/app/store";
import { errorText } from "@/shared/feedback";
import { isFlagGateError } from "./api";
import { wikiHref } from "./paths";
import type { WikiCitation } from "./wikiEvidence";

export interface SaveWikiDraftButtonProps {
  /** Assistant answer (Markdown) to persist. */
  answer: string;
  /** The user question it answers (draft title / body). */
  question?: string;
  citations?: WikiCitation[];
  origin?: "chat" | "deep_research";
  /** Defaults to the project selected in the shell header. */
  projectId?: string | null;
}

/**
 * S4: save an assistant answer as an L2 ``queries/`` wiki draft (unreviewed; never Claims / DOE).
 * Gated server-side by ``wiki_chat_save_draft``. Reusable from chat / deep research.
 */
export function SaveWikiDraftButton({ answer, question = "", citations, origin = "chat", projectId }: SaveWikiDraftButtonProps) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const current = useUi((s) => s.projectId);
  const pid = projectId === undefined ? current : projectId;
  const save = useMutation({
    mutationFn: () => call<{ ok: boolean; path?: string; disclaimer?: string }>("POST", "/api/wiki/drafts/save", {
      project_id: pid, question, answer_markdown: answer.trim(), origin,
      citations: (citations ?? []).map((c) => ({ title: c.title, source: c.source, identifier: c.identifier, snippet: c.snippet })),
    }),
    onSuccess: (o) => message.success(t("wiki.draft_saved", "已存草稿 {{path}}", { path: o.path ?? "" })),
    onError: (e) => message.error(isFlagGateError(e, "wiki_chat_save_draft")
      ? t("wiki.draft_flag", "Wiki 草稿未启用：请在系统设置的环境开关中打开 wiki_chat_save_draft")
      : errorText(e)),
  });
  const disabled = !pid || !answer.trim();
  return (
    <Space size={4} data-testid="save-wiki-draft">
      <Tooltip title={!pid ? t("wiki.draft_need_project", "请先选择项目") : t("wiki.draft_tip", "写入 queries/ L2 草稿（unreviewed；不进 Claims / DOE）")}>
        <Button size="small" icon={<SaveOutlined />} disabled={disabled} loading={save.isPending} onClick={() => save.mutate()}>
          {t("wiki.draft_save", "存为 Wiki 草稿")}
        </Button>
      </Tooltip>
      {save.data?.path && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          <Link to={wikiHref(save.data.path)}>{save.data.path}</Link>{save.data.disclaimer ? ` · ${save.data.disclaimer}` : ""}
        </Typography.Text>
      )}
    </Space>
  );
}

export default SaveWikiDraftButton;

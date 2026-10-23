import { Alert, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useSearchParams } from "react-router-dom";

/**
 * The question a chat playbook (行动包) hands to its target page: ``?q=`` (survives a reload) or the
 * navigation state's ``question`` (see features/chat/playbooks.ts).
 */
export function handoffQuestion(params: URLSearchParams, state: unknown): string {
  const fromState = (state as { question?: unknown } | null)?.question;
  return (params.get("q") || (typeof fromState === "string" ? fromState : "")).trim();
}

export function useHandoffQuestion(): string {
  const [params] = useSearchParams();
  const location = useLocation();
  return handoffQuestion(params, location.state);
}

/**
 * Pages whose form has no free-text field for the question still show it, so the user keeps the
 * context of the chat while filling in factors, constraints or a formulation.
 */
export function HandoffBanner() {
  const { t } = useTranslation();
  const question = useHandoffQuestion();
  const [closed, setClosed] = useState(false);
  if (!question || closed) return null;
  return (
    <Alert type="info" showIcon closable onClose={() => setClosed(true)} style={{ marginBottom: 16 }}
      data-testid="handoff-banner"
      message={t("handoff.title", "来自问答的问题")}
      description={
        <Typography.Paragraph copyable={{ text: question }} ellipsis={{ rows: 3, expandable: true }} style={{ marginBottom: 0 }}>
          {question}
        </Typography.Paragraph>
      } />
  );
}

/**
 * The formulation ingredient a question names, for prefilling a field that expects one: the longest
 * ingredient name that appears verbatim in the question (case-insensitive), or "" when none does —
 * never a guess.
 */
export function ingredientInQuestion(question: string, names: string[]): string {
  const q = question.toLowerCase();
  let best = "";
  for (const n of names) {
    const name = n.trim();
    if (name.length >= 2 && name.length > best.length && q.includes(name.toLowerCase())) best = name;
  }
  return best;
}

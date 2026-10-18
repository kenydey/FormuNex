import { PlusOutlined } from "@ant-design/icons";
import { Button, Divider, Empty, List, Popover, Segmented, Space, Spin, Tag, Tooltip, Typography, theme } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useEnvFlag } from "@/features/settings/useEnvFlag";
import { trailingToken } from "./logic";
import { builtinTarget, playbookTarget, uncoveredBuiltins, type PlaybookContext, type PlaybookTarget } from "./playbooks";
import type { BuiltinConnector, McpServerConfig, UnifiedSkill } from "./types";

export type ChatMode = "chat" | "evidence";
export const MAX_MCP = 4;

export interface ComposerSelection {
  mode: ChatMode;
  skills: string[];
  connectors: string[];
  mcpServers: string[];
}

const toggle = (list: string[], id: string, max = Infinity) =>
  list.includes(id) ? list.filter((x) => x !== id) : list.length >= max ? list : [...list, id];

export function useSkills(enabled: boolean) {
  return useQuery({ queryKey: ["chat", "skills"], enabled, staleTime: 60_000,
    queryFn: async () => (await call<{ skills: UnifiedSkill[] }>("GET", "/api/skills")).skills.filter((s) => s.enabled) });
}

/**
 * "+" menu of the composer: answer mode, chat skills, playbooks (→ their pages, carrying the current question and
 * project; prompt-only ones prefill the draft), built-in connectors and MCP servers (≤ 4).
 */
export function ChatComposerPlus({ value, onChange, context, onPrefill }: {
  value: ComposerSelection; onChange: (v: ComposerSelection) => void;
  /** Current question / project handed to the playbook's target page. */
  context?: PlaybookContext;
  onPrefill?: (text: string) => void;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  // FORMUNEX_CHAT_COMPOSER_PLUS_ENABLED: off hides the "+" menu (plain-text
  // composer keeps working). Fallback true until the first env-flags response.
  const composerPlusEnabled = useEnvFlag("chat_composer_plus_enabled", true);
  const skills = useSkills(open);
  const conns = useQuery({ queryKey: ["chat", "connectors"], enabled: open, staleTime: 60_000,
    queryFn: () => call<{ builtin: BuiltinConnector[]; mcp: McpServerConfig[] }>("GET", "/api/connectors") });
  const chatSkills = (skills.data ?? []).filter((s) => s.kind === "chat_skill");
  const playbooks = (skills.data ?? []).filter((s) => s.kind === "playbook");
  const builtins = uncoveredBuiltins(playbooks);
  const builtinLabel: Record<string, string> = {
    recommend: t("chat.playbook.recommend", "AI 配方推荐"),
    doe_classic: t("chat.playbook.doe_classic", "经典 DOE（CCD 筛选）"),
    loop: t("chat.playbook.loop", "BayBE 自驱动闭环"),
    inverse_design: t("chat.playbook.inverse_design", "逆向设计"),
    substitution: t("chat.playbook.substitution", "原料替代"),
    deep_research: t("chat.playbook.deep_research", "深度研究报告"),
    ip_analysis: t("chat.playbook.ip_analysis", "IP / FTO 分析"),
  };
  const run = (target: PlaybookTarget) => {
    setOpen(false);
    if (target.kind === "navigate") navigate(target.to, { state: target.state });
    else onPrefill?.(target.text);
  };
  const builtin = (conns.data?.builtin ?? []).filter((c) => c.enabled);
  const mcp = (conns.data?.mcp ?? []).filter((m) => m.enabled);
  const content = (
    <div style={{ width: 320, maxHeight: 420, overflow: "auto" }} data-testid="chat-composer-plus">
      <Typography.Text type="secondary">{t("chat.plus.mode", "模式")}</Typography.Text>
      <div style={{ margin: "4px 0 8px" }}>
        <Segmented<ChatMode> value={value.mode} onChange={(mode) => onChange({ ...value, mode })} options={[
          { value: "chat", label: t("chat.mode.chat", "普通问答") },
          { value: "evidence", label: t("chat.mode.evidence", "文献综合") },
        ]} />
      </div>
      <Typography.Text type="secondary">{t("chat.plus.skills", "对话技能")}</Typography.Text>
      <Spin spinning={skills.isLoading}>
        <Space size={[4, 4]} wrap style={{ margin: "4px 0 8px" }}>
          {chatSkills.map((s) => (
            <Tag.CheckableTag key={s.id} checked={value.skills.includes(s.id)}
              onChange={() => onChange({ ...value, skills: toggle(value.skills, s.id, 12) })}>
              {s.icon} {s.id}
            </Tag.CheckableTag>
          ))}
          {!skills.isLoading && !chatSkills.length && <Typography.Text type="secondary">{t("chat.plus.no_skills", "无已启用技能")}</Typography.Text>}
        </Space>
      </Spin>
      <Typography.Text type="secondary">{t("chat.plus.playbooks", "行动包（打开对应页面）")}</Typography.Text>
      <Space size={[4, 4]} wrap style={{ margin: "4px 0 8px" }} data-testid="chat-playbooks">
        {playbooks.map((s) => (
          <Tooltip key={s.id} title={[s.summary, s.when_to_use].filter(Boolean).join(" · ") || undefined}>
            <Button size="small" data-testid={`playbook-${s.id}`} onClick={() => run(playbookTarget(s, context ?? {}))}>
              {s.icon} {s.title}
            </Button>
          </Tooltip>
        ))}
        {builtins.map((b) => (
          <Button key={b.id} size="small" data-testid={`playbook-${b.id}`} onClick={() => run(builtinTarget(b, context ?? {}))}>
            {b.icon} {builtinLabel[b.id] ?? b.id}
          </Button>
        ))}
      </Space>
      {context?.question?.trim() && (
        <div style={{ marginBottom: 8 }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>{t("chat.plus.playbook_carry", "将携带当前问题与项目打开")}</Typography.Text>
        </div>
      )}
      <Typography.Text type="secondary">{t("chat.plus.connectors", "连接器")}</Typography.Text>
      <Spin spinning={conns.isLoading}>
        <Space size={[4, 4]} wrap style={{ margin: "4px 0 8px" }}>
          {builtin.map((c) => (
            <Tag.CheckableTag key={c.id} checked={value.connectors.includes(c.id)}
              onChange={() => onChange({ ...value, connectors: toggle(value.connectors, c.id, 8) })}>
              {c.display_name}
            </Tag.CheckableTag>
          ))}
          {!conns.isLoading && !builtin.length && <Typography.Text type="secondary">{t("chat.plus.no_connectors", "无已启用连接器")}</Typography.Text>}
        </Space>
      </Spin>
      {mcp.length > 0 && (
        <>
          <Divider style={{ margin: "4px 0" }} />
          <Typography.Text type="secondary">{t("chat.plus.mcp", "MCP 服务器")}</Typography.Text>
          <Space size={[4, 4]} wrap style={{ margin: "4px 0" }}>
            {mcp.map((m) => (
              <Tag.CheckableTag key={m.id} data-testid={`mcp-server-${m.id}`} checked={value.mcpServers.includes(m.id)}
                onChange={() => onChange({ ...value, mcpServers: toggle(value.mcpServers, m.id, MAX_MCP) })}>
                {m.id}
              </Tag.CheckableTag>
            ))}
          </Space>
          <div><Typography.Text type="secondary" style={{ fontSize: 12 }}>{t("chat.plus.mcp_hint", "最多 4 个；写类工具需会话审批")}</Typography.Text></div>
        </>
      )}
    </div>
  );
  if (!composerPlusEnabled) return null;
  return (
    <Popover trigger="click" placement="topLeft" open={open} onOpenChange={setOpen} content={content}
      title={t("chat.plus.title", "技能 · 文献综合 · 连接器")}>
      <Button icon={<PlusOutlined />} data-testid="chat-plus-btn" aria-label={t("chat.plus.title", "技能 · 文献综合 · 连接器")} />
    </Popover>
  );
}

export interface PickItem { id: string; label: string; hint?: string; icon?: string }

/** Inline picker shown above the composer while a ``/skill`` or ``@source`` token is being typed. */
export function TokenPicker({ items, loading, title, empty, onPick }: {
  items: PickItem[]; loading?: boolean; title: string; empty: string; onPick: (item: PickItem) => void;
}) {
  const { token } = theme.useToken();
  return (
    <div data-testid="chat-token-picker" style={{
      position: "absolute", bottom: "100%", left: 0, marginBottom: 4, width: 360, maxHeight: 260, overflow: "auto", zIndex: 20,
      background: token.colorBgElevated, boxShadow: token.boxShadowSecondary, borderRadius: token.borderRadiusLG, padding: 4,
    }}>
      <Typography.Text type="secondary" style={{ padding: "2px 8px", fontSize: 12 }}>{title}</Typography.Text>
      {loading ? <Spin size="small" style={{ display: "block", margin: 8 }} /> : !items.length
        ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={empty} />
        : <List size="small" dataSource={items} renderItem={(it, i) => (
            <List.Item key={it.id} style={{ cursor: "pointer", padding: "4px 8px", background: i === 0 ? token.controlItemBgHover : undefined }}
              onMouseDown={(e) => { e.preventDefault(); onPick(it); }}>
              <Typography.Text>{it.icon} {it.label}</Typography.Text>
              {it.hint && <Typography.Text type="secondary" ellipsis style={{ maxWidth: 160 }}>{it.hint}</Typography.Text>}
            </List.Item>
          )} />}
    </div>
  );
}

/** Items for the ``/`` picker: chat skills whose id or title contains the query. */
export function filterSkills(skills: UnifiedSkill[], draft: string): PickItem[] | null {
  const tok = trailingToken(draft, "/");
  if (!tok.active) return null;
  return skills
    .filter((s) => s.kind === "chat_skill")
    .filter((s) => !tok.query || s.id.toLowerCase().includes(tok.query) || s.title.toLowerCase().includes(tok.query))
    .slice(0, 12)
    .map((s) => ({ id: s.id, label: `/${s.id}`, hint: s.title, icon: s.icon }));
}

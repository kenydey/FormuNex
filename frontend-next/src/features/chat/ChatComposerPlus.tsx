import { PlusOutlined } from "@ant-design/icons";
import { Button, Divider, Empty, List, Popover, Segmented, Space, Spin, Tag, Typography, theme } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { trailingToken } from "./logic";
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

/** "+" menu of the composer: answer mode, chat skills, built-in connectors and MCP servers (≤ 4). */
export function ChatComposerPlus({ value, onChange }: { value: ComposerSelection; onChange: (v: ComposerSelection) => void }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const skills = useSkills(open);
  const conns = useQuery({ queryKey: ["chat", "connectors"], enabled: open, staleTime: 60_000,
    queryFn: () => call<{ builtin: BuiltinConnector[]; mcp: McpServerConfig[] }>("GET", "/api/connectors") });
  const chatSkills = (skills.data ?? []).filter((s) => s.kind === "chat_skill");
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

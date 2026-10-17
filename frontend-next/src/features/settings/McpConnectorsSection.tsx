import { ProCard } from "@ant-design/pro-components";
import { Alert, App, Button, Dropdown, Flex, Form, Input, List, Modal, Popconfirm, Segmented, Space, Switch, Tag, Typography, Upload } from "antd";
import { PlusOutlined, UploadOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useSession } from "@/app/session";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { manualMcpConfig } from "./logic";
import type { BuiltinConnector, ConnectorsResponse, McpImportResponse, McpServerConfig } from "./types";

export const CONNECTORS_KEY = ["settings", "connectors"] as const;
/** rbac: managing external connectors. */
export const CONNECTORS_PERMISSION = "admin.connectors";

type Mode = "github" | "upload" | "paste" | "manual";

/** Import an MCP server config: dry-run preview first, then confirm (imported servers start disabled). */
function ImportModal({ mode, onClose }: { mode: Mode | null; onClose: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const [tab, setTab] = useState<Mode>(mode ?? "github");
  const [url, setUrl] = useState("");
  const [json, setJson] = useState("");
  const [manual, setManual] = useState({ id: "local-mcp", command: "", args: "" });
  const [res, setRes] = useState<McpImportResponse | null>(null);
  const current = mode ? tab : null;

  const close = () => {
    setRes(null);
    setUrl("");
    setJson("");
    onClose();
  };
  const preview = useMutation({
    mutationFn: async (file?: File): Promise<McpImportResponse> => {
      if (file) {
        const fd = new FormData();
        fd.append("file", file);
        return call("POST", "/api/connectors/mcp/import/upload?dry_run=true", fd);
      }
      if (tab === "github") return call("POST", "/api/connectors/mcp/import/github", { url: url.trim(), dry_run: true });
      if (tab === "manual") return call("POST", "/api/connectors/mcp/import", { config: manualMcpConfig(manual.id, manual.command, manual.args), dry_run: true });
      return call("POST", "/api/connectors/mcp/import", { json_text: json, dry_run: true });
    },
    onSuccess: setRes,
    onError: (e) => { setRes(null); message.error(errorText(e)); },
  });
  const confirm = useMutation({
    mutationFn: (id: string) => call<McpImportResponse>("POST", "/api/connectors/mcp/import/confirm", { import_id: id }),
    onSuccess: (r) => {
      if (r.imported) {
        void qc.invalidateQueries({ queryKey: CONNECTORS_KEY });
        message.success(t("settings.conn.imported", "已导入（默认禁用，需手动启用）"));
        close();
      } else {
        setRes(r);
        message.warning(r.detail);
      }
    },
    onError: (e) => message.error(errorText(e)),
  });
  const p = res?.preview;
  return (
    <Modal open={!!mode} onCancel={close} width={720} destroyOnHidden title={t("settings.conn.add_mcp", "添加 MCP 服务器")}
      footer={[
        <Button key="c" onClick={close}>{t("settings.cancel", "取消")}</Button>,
        <Button key="ok" type="primary" disabled={!res?.import_id || (p?.errors?.length ?? 0) > 0} loading={confirm.isPending}
          onClick={() => res?.import_id && confirm.mutate(res.import_id)}>{t("settings.conn.confirm_import", "确认导入")}</Button>,
      ]}>
      <Flex vertical gap={12}>
        <Segmented<Mode> value={current ?? "github"} onChange={(v) => { setTab(v); setRes(null); }} options={[
          { value: "github", label: t("settings.skills.from_github", "从 GitHub") },
          { value: "upload", label: t("settings.conn.from_upload", "上传 JSON") },
          { value: "paste", label: t("settings.conn.from_paste", "粘贴 JSON") },
          { value: "manual", label: t("settings.conn.manual", "手动填写") },
        ]} />
        {current === "github" && (
          <Space.Compact style={{ width: "100%" }}>
            <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder={t("settings.conn.github_ph", "owner/repo 或 URL（自动找 mcp.json / .cursor/mcp.json）")} />
            <Button disabled={!url.trim()} loading={preview.isPending} onClick={() => preview.mutate(undefined)}>{t("settings.preview", "预览")}</Button>
          </Space.Compact>
        )}
        {current === "upload" && (
          <Upload accept=".json,application/json" maxCount={1} showUploadList={false} beforeUpload={(f) => { preview.mutate(f); return false; }}>
            <Button icon={<UploadOutlined />} loading={preview.isPending}>{t("settings.conn.pick_json", "选择 JSON 文件")}</Button>
          </Upload>
        )}
        {current === "paste" && (
          <>
            <Input.TextArea rows={8} value={json} onChange={(e) => setJson(e.target.value)} style={{ fontFamily: "monospace" }}
              placeholder={'{\n  "mcpServers": {\n    "name": { "command": "npx", "args": [] }\n  }\n}'} />
            <Button disabled={!json.trim()} loading={preview.isPending} onClick={() => preview.mutate(undefined)}>{t("settings.preview", "预览")}</Button>
          </>
        )}
        {current === "manual" && (
          <Form layout="vertical">
            <Form.Item label="id"><Input value={manual.id} onChange={(e) => setManual({ ...manual, id: e.target.value })} /></Form.Item>
            <Form.Item label="command" required><Input value={manual.command} placeholder="npx" onChange={(e) => setManual({ ...manual, command: e.target.value })} /></Form.Item>
            <Form.Item label={t("settings.conn.args", "参数（空格分隔）")}><Input value={manual.args} onChange={(e) => setManual({ ...manual, args: e.target.value })} /></Form.Item>
            <Button disabled={!manual.command.trim()} loading={preview.isPending} onClick={() => preview.mutate(undefined)}>{t("settings.preview", "预览")}</Button>
          </Form>
        )}
        {res && !p && <Alert type="info" message={res.detail} />}
        {p && (
          <>
            <Typography.Text type="secondary">{t("settings.conn.source", "来源 {{s}}", { s: p.source })}{p.source_url ? ` · ${p.source_url}` : ""}</Typography.Text>
            <List size="small" bordered rowKey="id" dataSource={p.servers} renderItem={(s) => (
              <List.Item>
                <Typography.Text code>{s.id}</Typography.Text>&nbsp;{s.command} {(s.args ?? []).join(" ")}
                {s.env_keys?.length ? <Space size={4} style={{ marginLeft: 8 }}>{s.env_keys.map((k) => <Tag key={k}>env:{k}</Tag>)}</Space> : null}
              </List.Item>
            )} />
            {p.warnings.length > 0 && <Alert type="warning" showIcon message={<ul style={{ margin: 0, paddingLeft: 16 }}>{p.warnings.map((w) => <li key={w}>{w}</li>)}</ul>} />}
            {p.errors.length > 0 && <Alert type="error" showIcon message={<ul style={{ margin: 0, paddingLeft: 16 }}>{p.errors.map((w) => <li key={w}>{w}</li>)}</ul>} />}
          </>
        )}
      </Flex>
    </Modal>
  );
}

/** Built-in read-only connectors (Literature / Chemistry) and custom MCP servers. */
export function McpConnectorsSection() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { can } = useSession();
  const manage = can(CONNECTORS_PERMISSION);
  const qc = useQueryClient();
  const [mode, setMode] = useState<Mode | null>(null);
  const [probe, setProbe] = useState<{ id: string; ok: boolean; text: string } | null>(null);
  const q = useQuery({ queryKey: CONNECTORS_KEY, queryFn: () => call<ConnectorsResponse>("GET", "/api/connectors") });

  const setData = (patch: Partial<ConnectorsResponse>) => qc.setQueryData<ConnectorsResponse>(CONNECTORS_KEY, (old) => (old ? { ...old, ...patch } : old));
  const toggleBuiltin = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      call<{ builtin: BuiltinConnector[] }>("POST", `/api/connectors/builtin/${encodeURIComponent(id)}/toggle`, { enabled }),
    onSuccess: (r) => setData({ builtin: r.builtin }),
    onError: (e) => message.error(errorText(e)),
  });
  const toggleServer = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      call<{ mcp: McpServerConfig[] }>("PATCH", `/api/connectors/mcp/${encodeURIComponent(id)}`, { enabled }),
    onSuccess: (r) => setData({ mcp: r.mcp }),
    onError: (e) => message.error(errorText(e)),
  });
  const remove = useMutation({
    mutationFn: (id: string) => call<{ mcp: McpServerConfig[] }>("DELETE", `/api/connectors/mcp/${encodeURIComponent(id)}`),
    onSuccess: (r) => setData({ mcp: r.mcp }),
    onError: (e) => message.error(errorText(e)),
  });
  const runProbe = useMutation({
    mutationFn: (id: string) => call<{ ok: boolean; tools?: string[]; error?: string | null }>("POST", `/api/connectors/mcp/${encodeURIComponent(id)}/probe`, {}),
    onSuccess: (r, id) => setProbe({ id, ok: r.ok, text: r.ok
      ? t("settings.conn.probe_ok", "已连接 · tools: {{t}}", { t: (r.tools ?? []).join(", ") || "(none)" })
      : r.error || "probe failed" }),
    onError: (e, id) => setProbe({ id, ok: false, text: errorText(e) }),
  });

  const d = q.data;
  return (
    <>
      <ProCard colSpan={{ xs: 24, md: 12 }} bordered loading={q.isLoading} title={t("settings.conn.builtin", "内置连接器（只读）")}>
        <ErrorAlert error={q.error} />
        <List rowKey="id" dataSource={d?.builtin ?? []} locale={{ emptyText: t("settings.none", "暂无") }}
          header={d && !d.connectors_builtin_enabled ? <Alert type="warning" showIcon message={t("settings.conn.builtin_off", "内置连接器总开关已关闭（环境变量）")} /> : undefined}
          renderItem={(c) => (
            <List.Item actions={[<Switch key="e" size="small" checked={c.enabled} disabled={!manage}
              loading={toggleBuiltin.isPending && toggleBuiltin.variables?.id === c.id} onChange={(v) => toggleBuiltin.mutate({ id: c.id, enabled: v })} />]}>
              <List.Item.Meta title={<Space wrap>{c.display_name}<Tag>{c.kind}</Tag></Space>}
                description={<>{c.description}{c.use_when && <><br /><Typography.Text type="secondary" style={{ fontSize: 12 }}>{c.use_when}</Typography.Text></>}</>} />
            </List.Item>
          )} />
      </ProCard>
      <ProCard colSpan={{ xs: 24, md: 12 }} bordered loading={q.isLoading} title={t("settings.conn.mcp", "MCP 服务器（实验）")}
        extra={<Space>
          <Link to="/admin/mcp-approvals">{t("settings.conn.approvals", "审批中心")}</Link>
          {manage && (
            <Dropdown menu={{ items: [
              { key: "github", label: t("settings.skills.from_github", "从 GitHub") },
              { key: "upload", label: t("settings.conn.from_upload", "上传 JSON") },
              { key: "paste", label: t("settings.conn.from_paste", "粘贴 JSON") },
              { key: "manual", label: t("settings.conn.manual", "手动填写") },
            ], onClick: ({ key }) => setMode(key as Mode) }}>
              <Button size="small" type="primary" icon={<PlusOutlined />}>{t("settings.conn.add", "添加")}</Button>
            </Dropdown>
          )}
        </Space>}>
        {d && !d.mcp_client_enabled && (
          <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={
            <>{t("settings.conn.client_off", "MCP Client 运行时默认关闭（可先导入配置）。Probe / 工具调用需在环境变量中开启 mcp_client_enabled。")}{" "}
              <Link to="/admin/settings/env">{t("settings.conn.to_env", "前往环境变量")}</Link></>
          } />
        )}
        <List rowKey="id" dataSource={d?.mcp ?? []} locale={{ emptyText: t("settings.conn.no_mcp", "暂无已保存的 MCP 服务器") }}
          renderItem={(s) => (
            <List.Item actions={[
              <Switch key="e" size="small" checked={!!s.enabled} disabled={!manage}
                loading={toggleServer.isPending && toggleServer.variables?.id === s.id} onChange={(v) => toggleServer.mutate({ id: s.id, enabled: v })} />,
              <Button key="p" size="small" disabled={!d?.mcp_client_enabled} loading={runProbe.isPending && runProbe.variables === s.id}
                title={d?.mcp_client_enabled ? "tools/list" : t("settings.conn.need_client", "需开启 mcp_client_enabled")}
                onClick={() => runProbe.mutate(s.id)}>Probe</Button>,
              manage ? (
                <Popconfirm key="d" title={t("settings.conn.confirm_delete", "删除该 MCP 服务器？")} onConfirm={() => remove.mutate(s.id)}>
                  <Button size="small" danger type="text">{t("common.delete", "删除")}</Button>
                </Popconfirm>
              ) : null,
            ].filter(Boolean)}>
              <List.Item.Meta
                title={<Space wrap><Typography.Text code>{s.id}</Typography.Text>{s.transport && <Tag>{s.transport}</Tag>}</Space>}
                description={<>
                  <Typography.Text type="secondary">{s.command} {(s.args ?? []).join(" ")}</Typography.Text>
                  {/* env values may be secrets: show the names only. */}
                  {s.env && Object.keys(s.env).length > 0 && <div>{Object.keys(s.env).map((k) => <Tag key={k}>env:{k}</Tag>)}</div>}
                  {probe?.id === s.id && <div><Typography.Text type={probe.ok ? "success" : "danger"}>{probe.text}</Typography.Text></div>}
                </>}
              />
            </List.Item>
          )} />
        <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
          {t("settings.conn.mcp_hint", "支持 Claude Desktop / Cursor 形状的 mcpServers JSON：先预览再确认；导入后默认禁用。")}
        </Typography.Paragraph>
      </ProCard>
      <ImportModal key={mode ?? "closed"} mode={mode} onClose={() => setMode(null)} />
    </>
  );
}

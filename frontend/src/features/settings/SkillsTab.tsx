import { ProCard } from "@ant-design/pro-components";
import { Alert, App, Button, Descriptions, Flex, Input, List, Modal, Popconfirm, Segmented, Space, Switch, Tag, Typography, Upload } from "antd";
import { GithubOutlined, PlusOutlined, UploadOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { disabledSkillIds, shortSha } from "./logic";
import type { SkillInstallResponse, SkillPack, SkillsCatalog, SkillUpdateCheck, UnifiedSkill } from "./types";

const SKILLS_KEY = ["settings", "skills"] as const;
const PACKS_KEY = ["settings", "skill-packs"] as const;

type AddMode = "github" | "upload" | "paste";

const ORIGIN_COLORS: Record<string, string> = { github: "blue", local: "green", pack: "green", bundled: "default" };

/** Dry-run preview → confirm. Every install path (GitHub / upload / paste / pack / update) ends here. */
function InstallModal({ open, initial, onClose }: { open: boolean; initial: SkillInstallResponse | null; onClose: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const [mode, setMode] = useState<AddMode>("github");
  const [url, setUrl] = useState("");
  const [md, setMd] = useState("");
  const [res, setRes] = useState<SkillInstallResponse | null>(null);
  const shown = res ?? initial;

  const close = () => {
    setRes(null);
    setUrl("");
    setMd("");
    onClose();
  };
  const preview = useMutation({
    mutationFn: async (file?: File): Promise<SkillInstallResponse> => {
      if (file) {
        const fd = new FormData();
        fd.append("file", file);
        return call("POST", "/api/skills/install/upload?dry_run=true", fd);
      }
      if (mode === "github") return call("POST", "/api/skills/install/github", { url: url.trim(), dry_run: true });
      return call("POST", "/api/skills/install/paste", { markdown: md, dry_run: true });
    },
    onSuccess: setRes,
    onError: (e) => {
      setRes(null);
      message.error(errorText(e));
    },
  });
  const confirm = useMutation({
    mutationFn: (installId: string) => call<SkillInstallResponse>("POST", "/api/skills/install/confirm", { install_id: installId }),
    onSuccess: (r) => {
      if (r.installed) {
        if (r.catalog) qc.setQueryData(SKILLS_KEY, r.catalog);
        void qc.invalidateQueries({ queryKey: SKILLS_KEY });
        void qc.invalidateQueries({ queryKey: PACKS_KEY });
        message.success(t("settings.skills.installed", "已安装 {{id}}", { id: r.skill_id ?? "" }));
        close();
      } else {
        setRes(r);
        message.warning(r.detail);
      }
    },
    onError: (e) => message.error(errorText(e)),
  });

  const p = shown?.preview;
  return (
    <Modal open={open} onCancel={close} width={720} destroyOnHidden title={t("settings.skills.add", "添加技能")}
      footer={[
        <Button key="c" onClick={close}>{t("settings.cancel", "取消")}</Button>,
        <Button key="ok" type="primary" disabled={!shown?.install_id || (p?.errors?.length ?? 0) > 0} loading={confirm.isPending}
          onClick={() => shown?.install_id && confirm.mutate(shown.install_id)}>
          {t("settings.skills.confirm_install", "确认安装")}
        </Button>,
      ]}>
      {!initial && (
        <Flex vertical gap={12} style={{ marginBottom: 16 }}>
          <Segmented<AddMode> value={mode} onChange={(v) => { setMode(v); setRes(null); }} options={[
            { value: "github", label: t("settings.skills.from_github", "从 GitHub") },
            { value: "upload", label: t("settings.skills.from_upload", "上传本地包") },
            { value: "paste", label: t("settings.skills.from_paste", "粘贴 SKILL.md") },
          ]} />
          {mode === "github" && (
            <Space.Compact style={{ width: "100%" }}>
              <Input prefix={<GithubOutlined />} value={url} onChange={(e) => setUrl(e.target.value)}
                placeholder="https://github.com/org/repo/tree/main/my-skill" onPressEnter={() => url.trim() && preview.mutate(undefined)} />
              <Button disabled={!url.trim()} loading={preview.isPending} onClick={() => preview.mutate(undefined)}>{t("settings.preview", "预览")}</Button>
            </Space.Compact>
          )}
          {mode === "upload" && (
            <Upload accept=".zip,.md,.markdown" maxCount={1} showUploadList={false}
              beforeUpload={(f) => { preview.mutate(f); return false; }}>
              <Button icon={<UploadOutlined />} loading={preview.isPending}>{t("settings.skills.pick_file", "选择 .zip / SKILL.md")}</Button>
            </Upload>
          )}
          {mode === "paste" && (
            <>
              <Input.TextArea rows={8} value={md} onChange={(e) => setMd(e.target.value)} style={{ fontFamily: "monospace" }}
                placeholder={"---\nname: my-skill\ndescription: ...\nallowed_tools: kb_hybrid\n---\n\n# ..."} />
              <Button disabled={!md.trim()} loading={preview.isPending} onClick={() => preview.mutate(undefined)}>{t("settings.preview", "预览")}</Button>
            </>
          )}
        </Flex>
      )}
      {shown && !p && <Alert type="info" message={shown.detail} />}
      {p && (
        <>
          <Descriptions size="small" bordered column={1} title={p.name} items={[
            { key: "s", label: t("settings.skills.summary", "简介"), children: p.summary || p.description },
            { key: "o", label: t("settings.skills.origin", "来源"), children: <>{p.origin || "—"}{p.source_url ? ` · ${p.source_url}` : ""}</> },
            { key: "sha", label: "SHA", children: shortSha(p.pinned_sha) || "—" },
            { key: "f", label: t("settings.skills.files", "文件数"), children: p.file_count },
            { key: "tools", label: t("settings.skills.tools", "允许工具"), children: <Space wrap>{p.allowed_tools.map((x) => <Tag key={x}>{x}</Tag>)}</Space> },
            ...(p.rejected_tools.length ? [{ key: "rej", label: t("settings.skills.rejected_tools", "被拒工具"),
              children: <Space wrap>{p.rejected_tools.map((x) => <Tag key={x} color="error">{x}</Tag>)}</Space> }] : []),
          ]} />
          {p.warnings.length > 0 && <Alert style={{ marginTop: 12 }} type="warning" showIcon message={<ul style={{ margin: 0, paddingLeft: 16 }}>{p.warnings.map((w) => <li key={w}>{w}</li>)}</ul>} />}
          {p.errors.length > 0 && <Alert style={{ marginTop: 12 }} type="error" showIcon message={<ul style={{ margin: 0, paddingLeft: 16 }}>{p.errors.map((w) => <li key={w}>{w}</li>)}</ul>} />}
        </>
      )}
    </Modal>
  );
}

function SkillList({ title, rows, busy, onToggle, onUninstall, onCheck, onUpdate }: {
  title: string; rows: UnifiedSkill[]; busy: boolean; onToggle: (s: UnifiedSkill, v: boolean) => void;
  onUninstall?: (s: UnifiedSkill) => void; onCheck?: (s: UnifiedSkill) => void; onUpdate?: (s: UnifiedSkill) => void;
}) {
  const { t } = useTranslation();
  return (
    <ProCard bordered size="small" title={title}>
      <List rowKey="id" dataSource={rows} locale={{ emptyText: t("settings.none", "暂无") }} renderItem={(s) => (
        <List.Item actions={[
          <Switch key="e" size="small" checked={s.enabled} disabled={busy || s.activation_policy === "always-on"} onChange={(v) => onToggle(s, v)} />,
          s.origin === "github" && onCheck ? <a key="c" onClick={() => onCheck(s)}>{t("settings.skills.check_update", "检查更新")}</a> : null,
          s.origin === "github" && onUpdate ? <a key="u" onClick={() => onUpdate(s)}>{t("settings.skills.update", "更新")}</a> : null,
          s.origin && s.origin !== "bundled" && s.kind === "chat_skill" && onUninstall ? (
            <Popconfirm key="d" title={t("settings.skills.confirm_uninstall", "卸载该技能？")} onConfirm={() => onUninstall(s)}>
              <a>{t("settings.skills.uninstall", "卸载")}</a>
            </Popconfirm>
          ) : null,
        ].filter(Boolean)}>
          <List.Item.Meta
            avatar={<span style={{ fontSize: 18 }}>{s.icon || "✦"}</span>}
            title={<Space wrap>{s.title}<Tag color={ORIGIN_COLORS[s.origin || "bundled"] ?? "default"}>{s.origin || "bundled"}</Tag></Space>}
            description={<>
              <Typography.Paragraph type="secondary" ellipsis={{ rows: 2 }} style={{ marginBottom: 0 }}>{s.summary}</Typography.Paragraph>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>{s.kind} · {s.activation_policy} · {s.id}</Typography.Text>
            </>}
          />
        </List.Item>
      )} />
    </ProCard>
  );
}

/** Playbooks + chat skills (SKILL.md) + optional domain packs; connectors are managed on /admin/connectors. */
export function SkillsTab() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const [modal, setModal] = useState<{ open: boolean; initial: SkillInstallResponse | null }>({ open: false, initial: null });
  const skills = useQuery({ queryKey: SKILLS_KEY, queryFn: () => call<SkillsCatalog>("GET", "/api/skills") });
  const packs = useQuery({ queryKey: PACKS_KEY, queryFn: () => call<{ packs: SkillPack[] }>("GET", "/api/skills/packs") });
  const list = skills.data?.skills ?? [];

  const toggle = useMutation({
    mutationFn: ({ s, enabled }: { s: UnifiedSkill; enabled: boolean }) => call<{ skills: UnifiedSkill[] }>("POST", "/api/skills/prefs", {
      disabled_ids: disabledSkillIds(list, s.id, enabled), pinned_ids: list.filter((x) => x.pinned).map((x) => x.id),
    }),
    onSuccess: (r) => qc.setQueryData<SkillsCatalog>(SKILLS_KEY, (old) => ({ prefs: old?.prefs ?? {}, skills: r.skills })),
    onError: (e) => message.error(errorText(e)),
  });
  const uninstall = useMutation({
    mutationFn: (s: UnifiedSkill) => call<{ catalog?: SkillsCatalog }>("DELETE", `/api/skills/installed/${encodeURIComponent(s.id)}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: SKILLS_KEY });
      void qc.invalidateQueries({ queryKey: PACKS_KEY });
    },
    onError: (e) => message.error(errorText(e)),
  });
  const check = useMutation({
    mutationFn: (s: UnifiedSkill) => call<SkillUpdateCheck>("GET", `/api/skills/installed/${encodeURIComponent(s.id)}/check-update`),
    onSuccess: (r) => {
      if (!r.checkable) message.info(`${r.skill_id}: ${r.reason}`);
      else if (r.update_available) message.info(t("settings.skills.update_available", "{{id}}：有更新（{{a}} → {{b}}）",
        { id: r.skill_id, a: shortSha(r.local_sha), b: shortSha(r.remote_sha) }));
      else message.success(t("settings.skills.up_to_date", "{{id}}：已是最新", { id: r.skill_id }));
    },
    onError: (e) => message.error(errorText(e)),
  });
  const dryRun = useMutation({
    mutationFn: (path: string) => call<SkillInstallResponse>("POST", path, { dry_run: true }),
    onSuccess: (r) => setModal({ open: true, initial: r }),
    onError: (e) => message.error(errorText(e)),
  });

  const busy = toggle.isPending || uninstall.isPending;
  return (
    <Flex vertical gap={16}>
      <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
        {t("settings.skills.intro", "统一管理配方行动包（推荐/DOE/寻优等）与对话技能（SKILL.md）。支持从 GitHub / 本地上传 / 粘贴安装：先预览审查，再确认写入。")}{" "}
        <Link to="/admin/connectors">{t("settings.skills.connectors_link", "连接器与 MCP →")}</Link>
      </Typography.Paragraph>
      <Space>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setModal({ open: true, initial: null })}>{t("settings.skills.add", "添加技能")}</Button>
      </Space>
      <ErrorAlert error={skills.error ?? packs.error} />
      <ProCard bordered size="small" title={t("settings.skills.packs", "可选域包 · Domain Packs")}
        subTitle={t("settings.skills.packs_sub", "专项能力以外挂域包存在，安装后出现在对话技能")}>
        <List rowKey="id" loading={packs.isLoading} dataSource={packs.data?.packs ?? []} locale={{ emptyText: t("settings.skills.no_packs", "暂无域包") }}
          renderItem={(p) => (
            <List.Item actions={[
              <Button key="i" size="small" disabled={p.installed} loading={dryRun.isPending && dryRun.variables?.includes(p.id)}
                onClick={() => dryRun.mutate(`/api/skills/packs/${encodeURIComponent(p.id)}/install`)}>
                {p.installed ? t("settings.skills.installed_tag", "已安装") : t("settings.skills.install", "安装")}
              </Button>,
            ]}>
              <List.Item.Meta title={p.title} description={<>{p.summary}<br /><Typography.Text type="secondary" style={{ fontSize: 12 }}>pack · {p.id}</Typography.Text></>} />
            </List.Item>
          )} />
      </ProCard>
      <SkillList title={t("settings.skills.playbooks", "配方行动包 · Playbooks")} rows={list.filter((s) => s.kind === "playbook")} busy={busy}
        onToggle={(s, enabled) => toggle.mutate({ s, enabled })} />
      <SkillList title={t("settings.skills.chat_skills", "对话技能 · Chat Skills")} rows={list.filter((s) => s.kind === "chat_skill")} busy={busy}
        onToggle={(s, enabled) => toggle.mutate({ s, enabled })} onUninstall={(s) => uninstall.mutate(s)} onCheck={(s) => check.mutate(s)}
        onUpdate={(s) => dryRun.mutate(`/api/skills/installed/${encodeURIComponent(s.id)}/update`)} />
      <InstallModal open={modal.open} initial={modal.initial} onClose={() => setModal({ open: false, initial: null })} />
    </Flex>
  );
}

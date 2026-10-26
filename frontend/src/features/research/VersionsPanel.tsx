import { ProCard } from "@ant-design/pro-components";
import { App, Button, Checkbox, Empty, Input, Popconfirm, Select, Space, Table, Tag, Tooltip, Typography, theme } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import dayjs from "dayjs";
import { call } from "@/api/client";
import { useSession } from "@/app/session";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { toggleDiffPick } from "./lib";
import type { ArtifactDiff, ArtifactLineage, ArtifactVersion, ArtifactVersionList, VersionStatus } from "./types";

/** One row of ``GET /api/artifacts/lineages``. */
export interface LineageSummary {
  lineage_id: string;
  project_id: string;
  kind: string;
  title: string;
  version_count: number;
  latest_version: number | null;
  latest_version_id: string | null;
  status: VersionStatus | null;
  created_at: number;
  updated_at: number;
}

const enc = encodeURIComponent;
const short = (id: string) => id.slice(0, 8);
const fmt = (ts?: number | null) => (ts ? dayjs(ts * 1000).format("YYYY-MM-DD HH:mm") : "—");
const STATUS_COLOR: Record<VersionStatus, string> = { staging: "warning", pending: "processing", finalized: "success" };

/**
 * Artifact version lineage (W4-4): load / create a lineage, list versions with status and derivation, submit
 * (staging → pending), finalize (pending → finalized, freezes evidence), restore as a new version (copy-on-write),
 * verify integrity, and diff two versions.
 */
export function VersionsPanel({ projectId, lineageId }: { projectId: string | null; lineageId?: string }) {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const { message } = App.useApp();
  const { can } = useSession();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [diff, setDiff] = useState<ArtifactDiff | null>(null);
  useEffect(() => {
    setPicked([]);
    setDiff(null);
  }, [lineageId]);
  useEffect(() => setDiff(null), [picked]);

  const lineagesKey = ["research", "artifact-lineages", projectId];
  const lineages = useQuery({
    queryKey: lineagesKey, enabled: !!projectId,
    queryFn: async () => {
      const rows = await call<LineageSummary[]>("GET", `/api/artifacts/lineages?project_id=${enc(projectId!)}`);
      return Array.isArray(rows) ? rows : [];
    },
  });
  const key = ["research", "artifact-versions", lineageId];
  const list = useQuery({
    queryKey: key, enabled: !!lineageId,
    queryFn: () => call<ArtifactVersionList>("GET", `/api/artifacts/lineages/${enc(lineageId!)}/versions`),
  });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: key });
    void qc.invalidateQueries({ queryKey: lineagesKey });
  };
  const onErr = (e: unknown) => message.error(errorText(e));
  const create = useMutation({
    mutationFn: () => call<ArtifactLineage>("POST", "/api/artifacts/lineages", { project_id: projectId, name: name.trim(), kind: "report" }),
    onSuccess: (l) => { setName(""); void qc.invalidateQueries({ queryKey: lineagesKey }); navigate(`/knowledge/research/artifacts/${l.lineage_id}`); }, onError: onErr,
  });
  const action = useMutation({
    mutationFn: ({ id, op }: { id: string; op: "submit" | "finalize" | "restore" }) =>
      call<ArtifactVersion>("POST", `/api/artifacts/versions/${enc(id)}/${op}`, op === "restore" ? { actor: null } : undefined),
    onSuccess: (_v, { op }) => {
      message.success(op === "restore" ? t("research.ver.restored", "已基于旧版创建新版本")
        : op === "submit" ? t("research.ver.submitted", "已提交待审") : t("research.ver.finalized", "已定版"));
      refresh();
    },
    onError: onErr,
  });
  const verify = useMutation({
    mutationFn: (id: string) => call<{ ok?: boolean; valid?: boolean; [k: string]: unknown }>("GET", `/api/artifacts/versions/${enc(id)}/verify`),
    onSuccess: (r) => (r.ok ?? r.valid) === false ? message.error(t("research.ver.tampered", "内容校验失败：快照可能被篡改"))
      : message.success(t("research.ver.verified", "sha256 校验通过")),
    onError: onErr,
  });
  const runDiff = useMutation({
    mutationFn: ([a, b]: string[]) => call<ArtifactDiff>("GET", `/api/artifacts/versions/${enc(b)}/diff?against=${enc(a)}`),
    onSuccess: setDiff, onError: onErr,
  });
  const basedOn = useMemo(() => new Map((list.data?.graph ?? []).map((g) => [g.version_id, g.based_on_version_id])), [list.data]);

  if (!projectId) return <Empty description={t("research.need_project", "请先在顶部选择项目，才能保存到货架或版本库。")} />;
  return (
    <>
      <Space wrap style={{ marginBottom: 16 }}>
        <Select<string> style={{ width: 380 }} showSearch allowClear value={lineageId} loading={lineages.isLoading} data-testid="lineage-select"
          placeholder={t("research.ver.pick", "选择逻辑文件（版本谱系）")} optionFilterProp="label"
          notFoundContent={t("research.ver.no_lineages", "该项目暂无逻辑文件")}
          onChange={(v) => navigate(v ? `/knowledge/research/artifacts/${v}` : "/knowledge/research/artifacts?tab=versions")}
          options={(lineages.data ?? []).map((l) => ({
            value: l.lineage_id,
            label: `${l.title} · ${l.kind} · ${l.latest_version ? `v${l.latest_version}` : "—"} ${short(l.lineage_id)}`,
          }))}
          optionRender={(o) => {
            const l = (lineages.data ?? []).find((x) => x.lineage_id === o.value);
            if (!l) return o.label;
            return (
              <Space size={4} wrap>
                <Typography.Text strong>{l.title}</Typography.Text>
                <Tag>{l.kind}</Tag>
                {l.latest_version ? <Tag color={l.status ? STATUS_COLOR[l.status] : undefined}>v{l.latest_version}</Tag> : <Tag>{t("research.ver.no_version_tag", "无版本")}</Tag>}
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>{fmt(l.updated_at)}</Typography.Text>
              </Space>
            );
          }} />
        <Space.Compact>
          <Input style={{ width: 220 }} value={name} onChange={(e) => setName(e.target.value)} placeholder={t("research.ver.new_name", "新建逻辑文件名称")} />
          <Button disabled={!name.trim() || !can("project.write")} loading={create.isPending} onClick={() => create.mutate()}>{t("research.ver.create", "新建")}</Button>
        </Space.Compact>
      </Space>
      <Typography.Paragraph type="secondary">
        {t("research.ver.help", "staging（草稿）→ pending（待审）→ finalized（已定版，不可变）；回滚 = 基于旧版新建版本（copy-on-write）。")}
      </Typography.Paragraph>
      <ErrorAlert error={list.error} />
      {!lineageId && <Empty description={t("research.ver.empty_pick", "选择一个逻辑文件，或从深度研究报告「存为版本」")} />}
      {list.data && (
        <>
          <Typography.Title level={5}>
            {list.data.lineage.name} <Typography.Text type="secondary" copyable={{ text: list.data.lineage.lineage_id }}>({short(list.data.lineage.lineage_id)})</Typography.Text>
            <Tag style={{ marginLeft: 8 }}>{list.data.lineage.kind}</Tag>
          </Typography.Title>
          <Table<ArtifactVersion> size="small" rowKey="version_id" loading={list.isFetching} dataSource={list.data.versions} pagination={false}
            locale={{ emptyText: t("research.ver.no_versions", "该逻辑文件暂无版本。") }}
            columns={[
              { title: t("research.ver.diff", "对比"), width: 60, render: (_v, v) => (
                <Checkbox checked={picked.includes(v.version_id)} onChange={() => setPicked((p) => toggleDiffPick(p, v.version_id))} />
              ) },
              { title: t("research.ver.version", "版本"), dataIndex: "version_id", render: (id: string) => <Typography.Text code copyable={{ text: id }}>{short(id)}</Typography.Text> },
              { title: t("research.ver.status", "状态"), dataIndex: "status", render: (s: VersionStatus) => (
                <Tag color={STATUS_COLOR[s] ?? "warning"}>{s === "staging" ? t("research.ver.staging", "草稿") : s === "pending" ? t("research.ver.pending", "待审")
                  : s === "finalized" ? t("research.ver.finalized_tag", "已定版") : s}</Tag>
              ) },
              { title: t("research.ver.based_on", "派生自"), render: (_v, v) => { const p = basedOn.get(v.version_id) ?? v.based_on_version_id; return p ? <Typography.Text code>{short(p)}</Typography.Text> : "—"; } },
              { title: t("research.ver.created", "创建时间"), dataIndex: "created_at", render: (ts: number) => fmt(ts) },
              { title: t("research.ver.actor", "操作人"), dataIndex: "actor", render: (a?: string | null) => a || "—" },
              { title: t("research.ver.size", "大小"), dataIndex: "content_bytes", render: (n: number) => `${n} B` },
              { title: t("research.ver.evidence", "证据冻结"), dataIndex: "evidence_frozen", render: (f?: boolean) => (f ? <Tag color="success">✓</Tag> : "—") },
              { title: t("common.actions", "操作"), width: 300, render: (_v, v) => (
                <Space size={0} wrap>
                  {v.status === "staging" && <Button type="link" size="small" disabled={!can("project.write")} onClick={() => action.mutate({ id: v.version_id, op: "submit" })}>{t("research.ver.submit", "提交待审")}</Button>}
                  {v.status === "pending" && (
                    <Popconfirm title={t("research.ver.finalize_confirm", "定版后不可修改，确认？")} onConfirm={() => action.mutate({ id: v.version_id, op: "finalize" })}>
                      <Button type="link" size="small" disabled={!can("project.write")}>{t("research.ver.finalize", "定版")}</Button>
                    </Popconfirm>
                  )}
                  <Button type="link" size="small" disabled={!can("project.write")} onClick={() => action.mutate({ id: v.version_id, op: "restore" })}>{t("research.ver.restore", "恢复为新版")}</Button>
                  <Tooltip title={v.sha256}><Button type="link" size="small" loading={verify.isPending && verify.variables === v.version_id}
                    onClick={() => verify.mutate(v.version_id)}>{t("research.ver.verify", "校验")}</Button></Tooltip>
                </Space>
              ) },
            ]} />
          <Space style={{ marginTop: 12 }}>
            <Button disabled={picked.length !== 2} loading={runDiff.isPending} onClick={() => runDiff.mutate(picked)}>
              {t("research.ver.run_diff", "对比选中版本（{{n}}/2）", { n: picked.length })}</Button>
            {picked.length === 2 && <Typography.Text type="secondary">{short(picked[0])} → {short(picked[1])}</Typography.Text>}
          </Space>
        </>
      )}
      {diff && (
        <ProCard bordered style={{ marginTop: 16 }} title={t("research.ver.diff_title", "对比 {{a}} → {{b}}", { a: short(diff.against_id), b: short(diff.version_id) })}
          extra={diff.truncated && <Tag color="warning">{t("research.ver.truncated", "大文本仅行级对比")}</Tag>}>
          {diff.ops.length === 0 && <Empty description={t("research.ver.same", "两版本内容一致。")} />}
          <div style={{ fontFamily: token.fontFamilyCode, fontSize: 12 }}>
            {diff.ops.map((op, i) => (
              <div key={i} data-op={op.type} style={{
                whiteSpace: "pre-wrap", padding: "0 4px",
                background: op.type === "insert" ? token.colorSuccessBg : op.type === "delete" ? token.colorErrorBg : undefined,
                color: op.type === "equal" ? token.colorTextSecondary : token.colorText,
                textDecoration: op.type === "delete" ? "line-through" : undefined,
              }}>
                {op.type === "insert" ? `+ ${op.new_text}` : op.type === "delete" ? `− ${op.old_text}` : op.old_text}
              </div>
            ))}
          </div>
        </ProCard>
      )}
    </>
  );
}

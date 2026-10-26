import { ProTable } from "@ant-design/pro-components";
import { Alert, App, Button, Popconfirm, Space, Tag, Tooltip, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useSession } from "@/app/session";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { materialsKey } from "./MaterialLibraryTab";

export interface MaterialCandidate {
  id: string; name: string; role?: string; cas_no?: string; smiles?: string; formula?: string; zh_name?: string; supplier?: string;
  source?: string; source_ref?: string; confidence?: string; status?: string; updated_at?: string | null;
}

export const candidatesKey = [...materialsKey, "candidates"];

export function useCandidates() {
  return useQuery({ queryKey: candidatesKey, queryFn: () => call<{ total: number; candidates: MaterialCandidate[] }>("GET", "/api/materials/candidates?limit=200") });
}

/** 待入库 (legacy MaterialsPanel "pending" tab): low-confidence candidates — promote / dismiss, KB harvest, noise clean-up. */
export function CandidatesTab() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { can } = useSession();
  const qc = useQueryClient();
  const q = useCandidates();
  const refresh = () => void qc.invalidateQueries({ queryKey: materialsKey });
  const promote = useMutation({
    mutationFn: (id: string) => call<{ ok: boolean; action?: string; name?: string }>("POST", `/api/materials/candidates/${encodeURIComponent(id)}/promote`, {}),
    onSuccess: (r) => { message.success(t("requirements.cand.promoted", "已晋升 · {{n}}", { n: r.name ?? "" })); refresh(); },
    onError: (e) => message.error(errorText(e)),
  });
  const dismiss = useMutation({
    mutationFn: (id: string) => call("POST", `/api/materials/candidates/${encodeURIComponent(id)}/dismiss`, {}),
    onSuccess: refresh, onError: (e) => message.error(errorText(e)),
  });
  const harvest = useMutation({
    mutationFn: () => call<Record<string, number>>("POST", "/api/materials/harvest-kb-products?limit=200&min_mentions=2", {}),
    onSuccess: (r) => { message.success(t("requirements.cand.harvested", "KB 收获：{{r}}", { r: Object.entries(r).map(([k, v]) => `${k} ${v}`).join(" · ") })); refresh(); },
    onError: (e) => message.error(errorText(e)),
  });
  const noise = useMutation({
    mutationFn: () => call<{ dismissed: number; kept: number; scanned: number }>("POST", "/api/materials/candidates/dismiss-noise?source=kb_promoted", {}),
    onSuccess: (r) => { message.success(t("requirements.cand.noise_done", "已清理噪声：忽略 {{d}} · 保留 {{k}}", { d: r.dismissed, k: r.kept })); refresh(); },
    onError: (e) => message.error(errorText(e)),
  });
  return (
    <>
      <Alert type="info" showIcon style={{ marginBottom: 12 }}
        message={t("requirements.cand.help", "低置信候选待确认。KB 自动抽取仅入库有 CAS / SMILES 的项；地名、公司碎片等会被质量门跳过。")} />
      <ErrorAlert error={q.error} />
      <ProTable<MaterialCandidate>
        rowKey="id" search={false} loading={q.isLoading} dataSource={q.data?.candidates} options={{ reload: () => void q.refetch() }} pagination={{ pageSize: 50 }}
        headerTitle={t("requirements.cand.total", "待入库 {{n}} 条", { n: q.data?.total ?? q.data?.candidates.length ?? 0 })}
        toolBarRender={() => [
          <Button key="h" disabled={!can("knowledge.write")} loading={harvest.isPending} onClick={() => harvest.mutate()}>{t("requirements.cand.harvest", "从 KB 商品库收获")}</Button>,
          <Popconfirm key="n" title={t("requirements.cand.noise_confirm", "自动忽略 KB 抽取出的低质量待入库项？")} onConfirm={() => noise.mutateAsync().catch(() => undefined)}>
            <Button danger disabled={!can("knowledge.write")} loading={noise.isPending}>{t("requirements.cand.noise", "清理 KB 噪声")}</Button>
          </Popconfirm>,
        ]}
        locale={{ emptyText: t("requirements.cand.empty", "暂无待入库候选") }}
        columns={[
          { title: t("requirements.mat.name", "名称"), dataIndex: "name",
            render: (_d, c) => <Space size={4}>{c.name}{c.zh_name && <Typography.Text type="secondary">{c.zh_name}</Typography.Text>}</Space> },
          { title: "CAS", dataIndex: "cas_no", width: 120, render: (_d, c) => c.cas_no || "—" },
          { title: t("requirements.mat.role", "角色"), dataIndex: "role", width: 100, render: (_d, c) => (c.role ? <Tag>{c.role}</Tag> : "—") },
          { title: t("requirements.cand.source", "来源"), dataIndex: "source", width: 160,
            render: (_d, c) => <Tooltip title={c.source_ref}><span>{c.source || "—"}</span></Tooltip> },
          { title: t("requirements.cand.confidence", "置信"), dataIndex: "confidence", width: 90, render: (_d, c) => <Tag>{c.confidence || "—"}</Tag> },
          { title: t("common.updated", "更新时间"), dataIndex: "updated_at", valueType: "dateTime", width: 170 },
          { title: t("common.actions", "操作"), valueType: "option", width: 130, render: (_d, c) => [
            <Button key="p" type="link" disabled={!can("knowledge.write")} onClick={() => !promote.isPending && promote.mutate(c.id)}>{t("requirements.cand.promote", "晋升")}</Button>,
            <Button key="d" type="link" disabled={!can("knowledge.write")} onClick={() => !dismiss.isPending && dismiss.mutate(c.id)}>{t("requirements.cand.dismiss", "忽略")}</Button>,
          ] },
        ]} />
    </>
  );
}

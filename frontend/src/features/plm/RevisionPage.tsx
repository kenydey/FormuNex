import { PageContainer, ProCard, ProTable } from "@ant-design/pro-components";
import { Alert, App, Button, Descriptions, Space, Tabs, Tag, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ApiError, call } from "@/api/client";
import { useSession } from "@/app/session";
import { EditableRows } from "@/shared/EditableRows";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { StatusTag } from "@/shared/tags";
import type { Acceptance, RedLineSubstance, ReleaseSpec } from "@/features/spec/types";
import { GateChecklist } from "./GateChecklist";
import { GenerateDocModal } from "./GenerateDocModal";
import { DOC_NAMES, type DocSummary, type Gate, type PackageView, type Revision } from "./types";

interface Spec { release_specs?: ReleaseSpec[]; acceptance?: Acceptance[]; red_lines?: RedLineSubstance[]; [k: string]: unknown }

/** 产品修订版：规格快照、文档、发布门禁、发布包 (方案 §5.5). */
export default function RevisionPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { revisionId = "" } = useParams();
  const { can } = useSession();
  const qc = useQueryClient();
  const rev = useQuery({ queryKey: ["plm", "revision", revisionId], queryFn: () => call<Revision>("GET", `/api/plm/revisions/${revisionId}`) });
  const docs = useQuery({ queryKey: ["plm", "docs", revisionId], queryFn: () => call<DocSummary[]>("GET", `/api/plm/revisions/${revisionId}/documents`) });
  const gate = useQuery({ queryKey: ["plm", "gate", revisionId], queryFn: () => call<Gate>("GET", `/api/plm/revisions/${revisionId}/gate`) });
  const packages = useQuery({ queryKey: ["plm", "packages", revisionId], queryFn: () => call<PackageView[]>("GET", `/api/plm/revisions/${revisionId}/packages`) });
  const [spec, setSpec] = useState<Spec>({});
  const [genOpen, setGenOpen] = useState(false);
  const [submitGate, setSubmitGate] = useState<Gate | null>(null);
  useEffect(() => { if (rev.data) setSpec(rev.data.spec_snapshot as Spec); }, [rev.data]);
  const refresh = () => void qc.invalidateQueries({ queryKey: ["plm"] });
  const pid = rev.data?.product.project_id;
  const act = useMutation({
    mutationFn: ({ path, method = "POST", body }: { path: string; method?: string; body?: unknown }) => call(method, path, body),
    onSuccess: () => { message.success(t("common.done", "完成")); refresh(); },
    onError: (e) => {
      if (e instanceof ApiError && e.status === 409 && (e.detail as { gate?: Gate })?.gate) {
        setSubmitGate((e.detail as { gate: Gate }).gate);
        message.error(t("gate.failed", "发布门禁未通过"));
      } else message.error(errorText(e));
    },
  });
  if (rev.error) return <PageContainer><ErrorAlert error={rev.error} /></PageContainer>;
  const r = rev.data;
  const editable = !!r && ["draft", "candidate"].includes(r.state);
  const latest = Object.values((docs.data ?? []).reduce<Record<string, DocSummary>>((acc, d) => {
    const k = `${d.doc_type}|${d.lang}|${d.regulation ?? "CN"}|${d.batch_id ?? ""}`;
    if (!acc[k] || acc[k].version_no < d.version_no) acc[k] = d;
    return acc;
  }, {}));

  return (
    <PageContainer loading={!r} title={r && `${r.product.code} · ${t("plm.rev", "修订")} ${r.rev}`} subTitle={r && <StatusTag status={r.state} />}
      extra={r && <Space>
        <Link to={`/plm/products/${r.product.id}`}><Button>{t("plm.product", "产品")}</Button></Link>
        {r.state === "draft" && can("formulation.freeze", pid) && <Button onClick={() => act.mutate({ path: `/api/plm/revisions/${revisionId}/candidate` })}>{t("plm.candidate", "钦定为候选")}</Button>}
        {r.state === "candidate" && can("release.submit", pid) && <Button type="primary" disabled={!gate.data?.passed}
          onClick={() => act.mutate({ path: `/api/plm/revisions/${revisionId}/submit` })}>{t("plm.submit", "提交签批")}</Button>}
      </Space>}>
      {r && <Tabs items={[
        { key: "overview", label: t("plm.overview", "概览"), children: (
          <ProCard bordered>
            <Descriptions column={2} size="small">
              <Descriptions.Item label={t("plm.product", "产品")}>{r.product.code} {r.product.name_zh}</Descriptions.Item>
              <Descriptions.Item label={t("plm.state", "状态")}><StatusTag status={r.state} /></Descriptions.Item>
              <Descriptions.Item label={t("winning.version", "配方版本")}>{r.formulation_version_id ?? "—"}</Descriptions.Item>
              <Descriptions.Item label={t("winning.experiment", "定型实验")}>{r.winning_experiment_id ?? "—"}</Descriptions.Item>
              <Descriptions.Item label={t("plm.frozen", "冻结时间")}>{r.frozen_at ?? "—"}</Descriptions.Item>
              <Descriptions.Item label={t("gate.title", "发布门禁")}>{gate.data ? (gate.data.passed ? <Tag color="green">{t("gate.passed", "通过")}</Tag> : <Tag color="red">{t("gate.not_passed", "未通过")}</Tag>) : "—"}</Descriptions.Item>
            </Descriptions>
          </ProCard>
        ) },
        { key: "spec", label: t("plm.spec", "规格快照"), children: (
          <ProCard bordered extra={editable && can("spec.write", pid) &&
            <Button type="primary" onClick={() => act.mutate({ method: "PUT", path: `/api/plm/revisions/${revisionId}/spec`, body: { spec_snapshot: spec } })}>{t("common.save", "保存")}</Button>}>
            {!editable && <Alert type="info" showIcon style={{ marginBottom: 8 }} message={t("plm.spec_frozen", "已提交或发布的修订版规格不可修改")} />}
            <Typography.Title level={5}>{t("spec.release_specs", "出厂指标（COA 判定）")}</Typography.Title>
            <EditableRows<ReleaseSpec> value={spec.release_specs} onChange={(v) => setSpec({ ...spec, release_specs: v })} newRow={() => ({ metric: "", comparison: "full" })} columns={[
              { title: t("spec.metric", "指标"), dataIndex: "metric" }, { title: t("spec.label", "检验项目"), dataIndex: "label" },
              { title: t("spec.unit", "单位"), dataIndex: "unit" }, { title: t("spec.method", "检验方法"), dataIndex: "method" },
              { title: t("spec.min", "下限"), dataIndex: "min", valueType: "digit" }, { title: t("spec.max", "上限"), dataIndex: "max", valueType: "digit" },
              { title: t("spec.comparison", "判定"), dataIndex: "comparison", valueType: "select", valueEnum: { full: t("spec.cmp.full", "全数值比较"), rounded: t("spec.cmp.rounded", "修约值比较") } }]} />
            <Typography.Title level={5} style={{ marginTop: 16 }}>{t("spec.acceptance", "定型验收指标（发布门禁 G3）")}</Typography.Title>
            <EditableRows<Acceptance> value={spec.acceptance} onChange={(v) => setSpec({ ...spec, acceptance: v })} newRow={() => ({ metric: "" })} columns={[
              { title: t("spec.metric", "指标"), dataIndex: "metric" }, { title: t("spec.label", "名称"), dataIndex: "label" },
              { title: t("spec.min", "下限"), dataIndex: "min", valueType: "digit" }, { title: t("spec.max", "上限"), dataIndex: "max", valueType: "digit" }]} />
            <Typography.Title level={5} style={{ marginTop: 16 }}>{t("spec.redlines.substances", "禁用 / 限用物质")}</Typography.Title>
            <EditableRows<RedLineSubstance> value={spec.red_lines} onChange={(v) => setSpec({ ...spec, red_lines: v })} newRow={() => ({ cas: "", max_pct: 0 })} columns={[
              { title: "CAS", dataIndex: "cas" }, { title: t("spec.name", "名称"), dataIndex: "name" },
              { title: t("spec.max_pct", "上限（%，0 = 禁用）"), dataIndex: "max_pct", valueType: "digit" }, { title: t("spec.reason", "依据"), dataIndex: "reason" }]} />
          </ProCard>
        ) },
        { key: "docs", label: t("plm.documents", "文档"), children: (
          <ProTable<DocSummary> rowKey="id" search={false} options={{ reload: () => void docs.refetch() }} dataSource={latest} loading={docs.isLoading}
            headerTitle={t("plm.latest_docs", "各类文档的最新版本")}
            toolBarRender={() => editable || r.frozen_at ? [<Button key="gen" type="primary" disabled={r.state === "under_review"}
              onClick={() => setGenOpen(true)}>{t("doc.generate", "生成文档")}</Button>] : []}
            columns={[
              { title: t("doc.type", "类型"), dataIndex: "doc_type", render: (_d, d) => <Link to={`/plm/documents/${d.id}`}>{DOC_NAMES[d.doc_type]}</Link> },
              { title: t("doc.lang", "语言"), dataIndex: "lang" },
              { title: t("doc.regulation", "适用法规"), render: (_d, d) => ["sds", "label"].includes(d.doc_type) ? (d.regulation ?? "CN") : "—" },
              { title: t("doc.version", "版本"), dataIndex: "version_no", render: (_d, d) => `v${d.version_no}` },
              { title: t("plm.state", "状态"), dataIndex: "status", render: (_d, d) => <StatusTag status={d.status} /> },
              { title: t("doc.missing_col", "缺项"), render: (_d, d) => d.missing.length ? <Tag color="red">{d.missing.length}</Tag> : <Tag color="green">0</Tag> },
              { title: t("doc.warnings", "提示"), render: (_d, d) => d.warnings.length || "—" },
              { title: t("common.created", "生成时间"), dataIndex: "created_at", valueType: "dateTime" },
            ]} />
        ) },
        { key: "gate", label: <span>{t("gate.title", "发布门禁")} {gate.data && !gate.data.passed && <Tag color="red">!</Tag>}</span>, children: (
          <ProCard bordered extra={<Button onClick={() => void gate.refetch()}>{t("gate.rerun", "重新检查")}</Button>}>
            <ErrorAlert error={gate.error} />
            {submitGate && <Alert type="error" showIcon style={{ marginBottom: 8 }} message={t("gate.failed", "发布门禁未通过")} closable onClose={() => setSubmitGate(null)} />}
            {gate.data && <GateChecklist gate={gate.data} revisionId={revisionId} projectId={pid} onChange={() => void gate.refetch()} readOnly={!editable} />}
          </ProCard>
        ) },
        { key: "packages", label: t("plm.packages", "发布包"), children: (
          <ProTable<PackageView> rowKey="id" search={false} options={false} dataSource={packages.data} columns={[
            { title: t("plm.package", "发布包"), dataIndex: "id", render: (_d, p) => <Link to={`/plm/packages/${p.id}`}>{p.package_hash.slice(0, 12)}</Link> },
            { title: t("plm.state", "状态"), dataIndex: "status", render: (_d, p) => <StatusTag status={p.status} /> },
            { title: t("common.created", "提交时间"), dataIndex: "created_at", valueType: "dateTime" },
            { title: t("plm.released_at", "发布时间"), dataIndex: "released_at", valueType: "dateTime" },
          ]} />
        ) },
      ]} />}
      {r && <GenerateDocModal revision={r} open={genOpen} onClose={() => { setGenOpen(false); refresh(); }} />}
    </PageContainer>
  );
}

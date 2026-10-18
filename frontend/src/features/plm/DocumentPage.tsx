import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Alert, App, Button, Descriptions, Empty, Select, Space, Switch, Table, Tag, Typography } from "antd";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call, download } from "@/api/client";
import { useSession } from "@/app/session";
import { PdfPreview } from "@/shared/PdfPreview";
import { StructuredDiff, type Change } from "@/shared/StructuredDiff";
import { useSignature } from "@/shared/SignatureModal";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { PictogramRow } from "@/shared/ghs";
import { SourceBadge, StatusTag } from "@/shared/tags";
import { DOC_NAMES, type DocContent, type DocDetail, type DocSection, type DocSummary } from "./types";

function show(v: unknown): React.ReactNode {
  if (v == null || v === "") return <Typography.Text type="secondary">—</Typography.Text>;
  if (Array.isArray(v)) return <div>{v.map((x, i) => <div key={i}>{typeof x === "object" ? `${(x as { code?: string }).code ?? ""} ${(x as { text?: string }).text ?? JSON.stringify(x)}` : String(x)}</div>)}</div>;
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function SectionView({ s }: { s: DocSection }) {
  return (
    <ProCard size="small" bordered title={`${s.no}. ${s.title}`} style={{ marginBottom: 8 }}>
      <Descriptions size="small" column={1} bordered>
        {s.fields.map((f) => (
          <Descriptions.Item key={f.key} label={<Space size={4}>{f.label || f.key}<SourceBadge source={f.source} /></Space>}>
            {f.key === "pictograms" && Array.isArray(f.value) ? <PictogramRow codes={f.value as string[]} size={32} /> : show(f.value)}
          </Descriptions.Item>
        ))}
      </Descriptions>
      {s.tables.map((tb) => (
        <Table key={tb.key} size="small" style={{ marginTop: 8 }} pagination={false} rowKey="_row"
          dataSource={tb.rows.map((row, r) => ({ _row: r, ...Object.fromEntries(row.map((c, i) => [String(i), c])) }))}
          columns={tb.columns.map((c, i) => ({ title: c, dataIndex: String(i), render: (v: unknown) => show(v) }))} />
      ))}
      {s.paragraphs.map((p, i) => <Typography.Paragraph key={i} style={{ marginTop: 8 }}>{p}</Typography.Paragraph>)}
    </ProCard>
  );
}

/** SDS / TDS / COA / 标签校对器 (方案 §3.6): structured content with field sources beside the rendered PDF. */
export default function DocumentPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { documentId = "" } = useParams();
  const { me, can } = useSession();
  const sign = useSignature();
  const qc = useQueryClient();
  const [diffWith, setDiffWith] = useState<string | undefined>();
  const [bilingual, setBilingual] = useState(false);
  const doc = useQuery({ queryKey: ["plm", "document", documentId], queryFn: () => call<DocDetail>("GET", `/api/plm/documents/${documentId}`) });
  const siblings = useQuery({ queryKey: ["plm", "docs", doc.data?.revision_id], enabled: !!doc.data,
    queryFn: () => call<DocSummary[]>("GET", `/api/plm/revisions/${doc.data!.revision_id}/documents`) });
  const d = doc.data;
  const sameType = (siblings.data ?? []).filter((x) => d && x.doc_type === d.doc_type && x.batch_id === d.batch_id);
  const versions = sameType.filter((x) => x.lang === d?.lang && x.id !== d?.id);
  const other = d && sameType.filter((x) => x.lang !== d.lang).sort((a, b) => b.version_no - a.version_no)[0];
  const diff = useQuery({ queryKey: ["plm", "diff", documentId, diffWith], enabled: !!d && (!!diffWith || !!d.supersedes_id),
    queryFn: () => call<{ changes: Change[]; major: boolean; from: string }>("GET", `/api/plm/documents/${documentId}/diff${diffWith ? `?against=${diffWith}` : ""}`) });
  const pair = useQuery({ queryKey: ["plm", "document", other?.id], enabled: bilingual && !!other,
    queryFn: () => call<DocDetail>("GET", `/api/plm/documents/${other!.id}`) });

  const approve = async () => {
    if (!d) return;
    const coa = d.doc_type === "coa";
    const s = await sign({ title: coa ? t("doc.issue_coa", "签发 COA") : t("doc.approve", "审核通过"), comment: "optional",
      meaning: coa ? t("doc.issue_meaning", "我确认本批次检验结果合格，签发此 COA。") : t("doc.approve_meaning", "我已审核此文档（v{{v}}）的内容，确认可用于发布。", { v: d.version_no }) });
    if (!s) return;
    try {
      await call("POST", `/api/plm/documents/${d.id}/approve`, s);
      message.success(t("doc.approved", "已审核"));
      void qc.invalidateQueries({ queryKey: ["plm"] });
    } catch (e) {
      message.error(errorText(e));
    }
  };
  if (doc.error) return <PageContainer><ErrorAlert error={doc.error} /></PageContainer>;
  const content: DocContent | undefined = d?.content;
  const perm = d?.doc_type === "coa" ? "coa.sign" : "plm.document.review";

  return (
    <PageContainer loading={!d} title={d && `${DOC_NAMES[d.doc_type]} · ${d.lang} · v${d.version_no}`} subTitle={d && <StatusTag status={d.status} />}
      extra={d && <Space wrap>
        <Link to={`/plm/revisions/${d.revision_id}`}><Button>{t("plm.revision", "修订版")}</Button></Link>
        {d.has_pdf && <Button onClick={() => void download(`/api/plm/documents/${d.id}/file/pdf`, `${d.doc_type}-${d.lang}-v${d.version_no}.pdf`)}>PDF</Button>}
        {d.has_docx && <Button onClick={() => void download(`/api/plm/documents/${d.id}/file/docx`, `${d.doc_type}-${d.lang}-v${d.version_no}.docx`)}>Word</Button>}
        {d.status === "draft" && can(perm) && <Button type="primary" disabled={d.missing.length > 0 || d.created_by === me?.id} onClick={() => void approve()}>
          {d.doc_type === "coa" ? t("doc.issue_coa", "签发 COA") : t("doc.approve", "审核通过")}</Button>}
      </Space>}>
      {d && d.missing.length > 0 && <Alert type="error" showIcon style={{ marginBottom: 8 }} message={t("doc.missing", "缺项：{{m}}", { m: d.missing.join("、") })} />}
      {d && d.warnings.length > 0 && <Alert type="warning" showIcon style={{ marginBottom: 8 }} message={t("doc.warnings_title", "提示")} description={d.warnings.map((w, i) => <div key={i}>{w}</div>)} />}
      {content && (
        <ProCard split="vertical" bordered>
          <ProCard colSpan="50%" style={{ maxHeight: "78vh", overflow: "auto" }}
            title={<Space>{t("doc.structured", "结构化内容")}{other && <><Switch size="small" checked={bilingual} onChange={setBilingual} />{t("doc.bilingual", "中英对照")}</>}</Space>}>
            {bilingual && pair.data
              ? content.sections.map((s, i) => (
                  <ProCard key={s.key} ghost split="vertical">
                    <ProCard ghost><SectionView s={s} /></ProCard>
                    <ProCard ghost>{pair.data!.content.sections[i] ? <SectionView s={pair.data!.content.sections[i]} /> : <Empty />}</ProCard>
                  </ProCard>))
              : content.sections.map((s) => <SectionView key={s.key} s={s} />)}
          </ProCard>
          <ProCard title={t("doc.preview", "PDF 预览")}>
            <PdfPreview path={d?.has_pdf ? `/api/plm/documents/${d.id}/file/pdf` : null} height={760} />
          </ProCard>
        </ProCard>
      )}
      {d && (d.supersedes_id || versions.length > 0) && (
        <ProCard bordered style={{ marginTop: 16 }} title={t("doc.diff", "版本对比")}
          extra={<Select allowClear style={{ width: 220 }} placeholder={t("doc.vs_previous", "与上一版本对比")} value={diffWith} onChange={setDiffWith}
            options={versions.map((v) => ({ value: v.id, label: `v${v.version_no} · ${v.status}` }))} />}>
          {diff.data?.major && <Alert type="error" showIcon style={{ marginBottom: 8 }} message={t("doc.major", "存在重大变更（危害 / 组成 / 运输 / 结果）：需重新签批")} />}
          {diff.data ? <StructuredDiff changes={diff.data.changes} /> : <ErrorAlert error={diff.error} />}
          {diff.data && !diff.data.changes.length && <Tag color="green">{t("doc.no_changes", "无差异")}</Tag>}
        </ProCard>
      )}
    </PageContainer>
  );
}

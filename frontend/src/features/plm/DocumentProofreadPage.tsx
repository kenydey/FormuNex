import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Alert, Button, Descriptions, Empty, Space, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { ErrorAlert } from "@/shared/feedback";
import { pairSections, proofreadParity } from "./proofread";
import { DOC_NAMES, type DocDetail, type DocSummary } from "./types";

function show(v: unknown): string {
  if (v == null || v === "") return "—";
  if (Array.isArray(v)) {
    return v.map((x) => (typeof x === "object" && x !== null
      ? `${(x as { code?: string }).code ?? ""} ${(x as { text?: string }).text ?? JSON.stringify(x)}`.trim()
      : String(x))).join("；");
  }
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/** 中英并排校对页 (3-16)：按章节/字段 key 配对，缺失项显式标出；只读，不修改任何版本。 */
export default function DocumentProofreadPage() {
  const { t } = useTranslation();
  const { documentId = "" } = useParams();
  const doc = useQuery({ queryKey: ["plm", "document", documentId],
                         queryFn: () => call<DocDetail>("GET", `/api/plm/documents/${documentId}`) });
  const d = doc.data;
  const siblings = useQuery({ queryKey: ["plm", "docs", d?.revision_id], enabled: !!d,
    queryFn: () => call<DocSummary[]>("GET", `/api/plm/revisions/${d!.revision_id}/documents`) });
  const other = d && (siblings.data ?? []).filter((x) => x.doc_type === d.doc_type && x.batch_id === d.batch_id &&
    x.lang !== d.lang).sort((a, b) => b.version_no - a.version_no)[0];
  const pair = useQuery({ queryKey: ["plm", "document", other?.id], enabled: !!other,
    queryFn: () => call<DocDetail>("GET", `/api/plm/documents/${other!.id}`) });
  if (doc.error) return <PageContainer><ErrorAlert error={doc.error} /></PageContainer>;
  const zh = d?.lang === "zh-CN" ? d : pair.data;
  const en = d?.lang === "en-US" ? d : pair.data;
  const paired = zh && en ? pairSections(zh.content, en.content) : null;
  const parity = zh && en ? proofreadParity(zh.content, en.content) : null;
  const structuralGap = !!parity && (parity.missingSections.length > 0 || parity.missingFields.length > 0);

  return (
    <PageContainer title={d ? `${DOC_NAMES[d.doc_type]} · ${t("doc.proofread", "中英并排校对")}` : t("doc.proofread", "中英并排校对")}
      subTitle={d && `${d.lang} v${d.version_no} ↔ ${other ? `${other.lang} v${other.version_no}` : t("doc.proofread_no_pair", "无对照版本")}`}
      extra={<Space><Link to={`/plm/documents/${documentId}`}><Button>{t("doc.back", "返回文档")}</Button></Link></Space>}>
      {d && !other && <Alert type="warning" showIcon message={t("doc.proofread_no_pair_hint", "同一修订版下没有另一语言的同类型文档，先生成对照版本。")} />}
      {parity && <Alert type={structuralGap ? "error" : "success"} showIcon style={{ marginBottom: 12 }}
        message={structuralGap
          ? t("doc.proofread_gap", "结构不对称：缺失章节 {{s}}；缺失字段 {{f}}",
              { s: parity.missingSections.join("、") || "—", f: parity.missingFields.join("、") || "—" })
          : t("doc.proofread_ok", "结构对称：章节 {{a}}/{{b}}，字段 {{c}}/{{d}}",
              { a: parity.sectionsZh, b: parity.sectionsEn, c: parity.fieldsZh, d: parity.fieldsEn })}
        description={t("doc.proofread_note", "本页只做结构配对与并排展示，不判断翻译质量；值是否对应由校对人逐项确认。")} />}
      {paired?.map((p) => (
        <ProCard key={p.key} bordered style={{ marginBottom: 12 }}
          title={<Space>{p.key}{p.missingSide && <Tag color="red">{t("doc.proofread_missing", "仅{{side}}有",
            { side: p.missingSide === "zh" ? "中文" : "英文" })}</Tag>}
            {p.tablesMismatch && <Tag color="orange">{t("doc.proofread_table_mismatch", "表格形状不一致")}</Tag>}
            {p.paragraphsMismatch && <Tag color="orange">{t("doc.proofread_para_mismatch", "段落数不一致")}</Tag>}</Space>}>
          <ProCard split="vertical" ghost>
            <ProCard ghost title={<Typography.Text strong>{p.titleZh || "—"}</Typography.Text>}>
              <Descriptions size="small" column={1} bordered>
                {p.fields.map((f) => (
                  <Descriptions.Item key={f.key} label={<Space>{f.labelZh || f.key}
                    {f.missingSide === "en" && <Tag color="red">{t("doc.proofread_missing_en", "英文缺失")}</Tag>}</Space>}>
                    {show(f.valueZh)}
                  </Descriptions.Item>
                ))}
              </Descriptions>
            </ProCard>
            <ProCard ghost title={<Typography.Text strong>{p.titleEn || "—"}</Typography.Text>}>
              <Descriptions size="small" column={1} bordered>
                {p.fields.map((f) => (
                  <Descriptions.Item key={f.key} label={<Space>{f.labelEn || f.key}
                    {f.missingSide === "zh" && <Tag color="red">{t("doc.proofread_missing_zh", "中文缺失")}</Tag>}</Space>}>
                    {show(f.valueEn)}
                  </Descriptions.Item>
                ))}
              </Descriptions>
            </ProCard>
          </ProCard>
        </ProCard>
      ))}
      {paired && paired.length === 0 && <Empty />}
    </PageContainer>
  );
}

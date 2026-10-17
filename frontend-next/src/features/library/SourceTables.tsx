import { Card, Empty, List, Space, Spin, Table, Tag, Tooltip, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { Markdown } from "@/shared/Markdown";
import { ErrorAlert } from "@/shared/feedback";
import { TABLE_KIND_COLOR, cellText, propertyText, tableKind } from "./logic";
import type { ExtractionFormula, ExtractionTable, TableAssetView } from "./types";

const PREVIEW_ROWS = 5;

/** Table assets of a source (recipe / performance / TDS-SDS kind, row preview, normalised PropertySet). */
export function TableBadges({ sourceId }: { sourceId: string }) {
  const { t } = useTranslation();
  const q = useQuery({
    queryKey: ["kb", "table-assets", sourceId],
    queryFn: () => call<{ tables?: TableAssetView[] }>("GET", `/api/sources/${encodeURIComponent(sourceId)}/tables`),
  });
  const kindLabel = (k: string) => ({
    recipe: t("library.tables.recipe", "配方表"), performance: t("library.tables.performance", "性能表"),
    tds_sds: "TDS/SDS", other: t("library.tables.other", "表格"),
  }[k] ?? k);
  if (q.isLoading) return <Spin />;
  if (q.error) return <ErrorAlert error={q.error} />;
  const tables = Array.isArray(q.data?.tables) ? q.data!.tables! : [];
  if (!tables.length) return <Empty description={t("library.tables.none", "未抽取到表格")} />;
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      {tables.map((tb, i) => {
        const headers = Array.isArray(tb.headers) ? tb.headers : [];
        const rows = Array.isArray(tb.rows) ? tb.rows : [];
        const props = tb.property_set?.properties ?? [];
        const warnings = tb.property_set?.warnings ?? [];
        const kind = tableKind(tb.kind);
        return (
          <Card key={tb.table_id || `table-${i}`} size="small"
            title={<Space><Tag color={TABLE_KIND_COLOR[kind]}>{kindLabel(kind)}</Tag>{tb.page_no != null && <Tag>p.{tb.page_no}</Tag>}
              <Typography.Text ellipsis style={{ maxWidth: 480 }}>{tb.caption}</Typography.Text></Space>}>
            {headers.length > 0 && (
              <Table size="small" pagination={false} scroll={{ x: true }} rowKey="__k"
                columns={headers.map((h, hi) => ({ title: cellText(h), dataIndex: String(hi), key: String(hi), ellipsis: true }))}
                dataSource={rows.slice(0, PREVIEW_ROWS).map((r, ri) => ({
                  __k: ri, ...Object.fromEntries((Array.isArray(r) ? r : []).slice(0, headers.length).map((c, ci) => [String(ci), cellText(c)])),
                }))} />
            )}
            {rows.length > PREVIEW_ROWS && (
              <Typography.Text type="secondary">{t("library.tables.more", "共 {{n}} 行，仅预览前 {{k}} 行", { n: rows.length, k: PREVIEW_ROWS })}</Typography.Text>
            )}
            {props.length > 0 && (
              <div style={{ marginTop: 8 }}>
                {props.slice(0, 24).map((p, pi) => (
                  <Tooltip key={`${p.name}-${pi}`} title={p.raw_text}><Tag color="processing">{propertyText(p)}</Tag></Tooltip>
                ))}
                {props.length > 24 && <Tag>+{props.length - 24}</Tag>}
              </div>
            )}
            {warnings.length > 0 && <Typography.Text type="warning">{warnings.slice(0, 3).join("；")}</Typography.Text>}
          </Card>
        );
      })}
    </Space>
  );
}

/** MinerU structured extraction (extraction_tables / extraction_formulas), read-only. */
export function ExtractionTablesPanel({ sourceId }: { sourceId: string }) {
  const { t } = useTranslation();
  const tables = useQuery({ queryKey: ["kb", "extraction-tables", sourceId],
    queryFn: () => call<ExtractionTable[]>("GET", `/api/kb/sources/${encodeURIComponent(sourceId)}/tables`) });
  const formulas = useQuery({ queryKey: ["kb", "extraction-formulas", sourceId],
    queryFn: () => call<ExtractionFormula[]>("GET", `/api/kb/sources/${encodeURIComponent(sourceId)}/formulas`) });
  if (tables.isLoading || formulas.isLoading) return <Spin />;
  const tl = tables.data ?? [];
  const fl = formulas.data ?? [];
  const bbox = <Tooltip title={t("library.extract.bbox_tip", "bbox 真实性待 MinerU 真实样本验证")}><Tag color="warning">{t("library.extract.bbox", "bbox 待验证")}</Tag></Tooltip>;
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <ErrorAlert error={tables.error ?? formulas.error} />
      {!tl.length && !fl.length && <Empty description={t("library.extract.none", "该来源暂无结构化表格/公式（仅 MinerU 解析的文档会写入）")} />}
      {tl.length > 0 && (
        <List header={<b>{t("library.extract.tables", "表格 · {{n}}", { n: tl.length })}</b>} dataSource={tl} renderItem={(tb) => (
          <List.Item key={tb.id}>
            <Space direction="vertical" style={{ width: "100%" }}>
              <Space wrap>
                {tb.page_no != null && <Tag>{t("library.extract.page", "第 {{p}} 页", { p: tb.page_no })}</Tag>}
                {tb.n_rows != null && tb.n_cols != null && <Tag>{t("library.extract.size", "{{r}} 行 × {{c}} 列", { r: tb.n_rows, c: tb.n_cols })}</Tag>}
                {tb.bbox && bbox}
              </Space>
              {tb.caption && <Typography.Text type="secondary">{t("library.extract.caption", "表注：{{c}}", { c: tb.caption })}</Typography.Text>}
              <Markdown content={tb.markdown_text} />
            </Space>
          </List.Item>
        )} />
      )}
      {fl.length > 0 && (
        <List header={<b>{t("library.extract.formulas", "公式 · {{n}}", { n: fl.length })}</b>} dataSource={fl} renderItem={(f) => (
          <List.Item key={f.id}>
            <Space direction="vertical" style={{ width: "100%" }}>
              <Space wrap>
                {f.formula_no && <Tag color="purple">({f.formula_no})</Tag>}
                {f.page_no != null && <Tag>{t("library.extract.page", "第 {{p}} 页", { p: f.page_no })}</Tag>}
                {f.bbox && bbox}
              </Space>
              <Markdown content={`$$\n${f.latex}\n$$`} />
              <Typography.Text code copyable style={{ fontSize: 12 }}>{f.latex}</Typography.Text>
            </Space>
          </List.Item>
        )} />
      )}
    </Space>
  );
}

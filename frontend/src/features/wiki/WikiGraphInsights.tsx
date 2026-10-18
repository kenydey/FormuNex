import { Button, Card, List, Space, Tooltip, Typography } from "antd";
import { useTranslation } from "react-i18next";
import type { WikiPageGraphInsights } from "./types";

type Props = {
  insights: WikiPageGraphInsights | null | undefined;
  busy: string | null;
  canRefreshDossier: boolean;
  onOpenPath: (path: string) => void;
  onRunLint: () => void;
  onRefreshDossier: () => void;
};

/** Gap insights next to the wiki link graph: orphans / broken links → open, lint, dossier refresh. */
export function WikiGraphInsights({ insights, busy, canRefreshDossier, onOpenPath, onRunLint, onRefreshDossier }: Props) {
  const { t } = useTranslation();
  const orphans = insights?.orphans ?? [];
  const broken = insights?.broken ?? [];
  const isolates = insights?.isolates ?? [];
  const comps = insights?.components;
  return (
    <Card size="small" title={t("wiki.insights", "缺口洞察")} data-testid="wiki-graph-insights"
      extra={comps && <Typography.Text type="secondary">{t("wiki.components", "分量 {{n}} · 最大 {{max}}", { n: comps.count, max: comps.largest })}</Typography.Text>}>
      <Space wrap style={{ marginBottom: 12 }}>
        <Tooltip title={t("wiki.lint_tip", "扫描 stale / conflict / orphan 并写回 flags")}>
          <Button size="small" disabled={!!busy} loading={busy === "lint"} onClick={onRunLint}>{t("wiki.run_lint", "跑 Lint")}</Button>
        </Tooltip>
        <Button size="small" disabled={!!busy || !canRefreshDossier} loading={busy === "dossier"} onClick={onRefreshDossier}>{t("wiki.dossier_refresh", "刷新卷宗")}</Button>
      </Space>
      <Typography.Text strong>
        {t("wiki.orphans", "孤立页（无入链）· {{n}}", { n: orphans.length })}
        {isolates.length !== orphans.length ? t("wiki.isolates", " · 零边 {{n}}", { n: isolates.length }) : ""}
      </Typography.Text>
      <List size="small" dataSource={orphans} rowKey="path" style={{ maxHeight: 220, overflow: "auto", marginBottom: 12 }}
        locale={{ emptyText: t("wiki.no_orphans", "暂无孤立页") }}
        renderItem={(o) => (
          <List.Item actions={[<a key="o" onClick={() => onOpenPath(o.path)}>{t("wiki.open", "打开")}</a>]}>
            <List.Item.Meta title={o.label} description={`${o.kind} · ${o.path}`} />
          </List.Item>
        )} />
      <Typography.Text strong>{t("wiki.broken", "断链样例 · {{n}}", { n: broken.length })}</Typography.Text>
      <List size="small" dataSource={broken} rowKey={(b) => `${b.source}->${b.target}`} style={{ maxHeight: 220, overflow: "auto" }}
        locale={{ emptyText: t("wiki.no_broken", "暂无断链") }}
        renderItem={(b) => (
          <List.Item actions={[<a key="o" onClick={() => onOpenPath(b.source)}>{t("wiki.open_source", "打开源页")}</a>]}>
            <List.Item.Meta title={<Typography.Text type="danger">[[{b.target}]]</Typography.Text>}
              description={`${t("wiki.from", "来自")} ${b.source_label || b.source}${b.reason ? ` · ${b.reason}` : ""}`} />
          </List.Item>
        )} />
    </Card>
  );
}

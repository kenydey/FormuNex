import { List, Tag, Typography } from "antd";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { relatedWikiFromCitations, type WikiCitation } from "./wikiEvidence";
import { WikiReaderDrawer } from "./WikiReaderDrawer";

/** S1: wiki pages cited by an answer → open them in a reader drawer. Renders nothing when no citation is a wiki page. */
export function RelatedWikiList({ citations }: { citations?: WikiCitation[] | null }) {
  const { t } = useTranslation();
  const related = useMemo(() => relatedWikiFromCitations(citations), [citations]);
  const [open, setOpen] = useState<string | null>(null);
  const linkPages = useMemo(() => related.map((r) => ({ path: r.path, title: r.title, kind: r.kind })), [related]);
  if (related.length === 0) return null;
  return (
    <>
      <List size="small" header={<Typography.Text type="secondary">{t("wiki.related", "相关 Wiki")}</Typography.Text>} dataSource={related} rowKey="path"
        data-testid="related-wiki-list"
        renderItem={(w) => (
          <List.Item style={{ cursor: "pointer" }} onClick={() => setOpen(w.path)} title={w.path}>
            <List.Item.Meta title={<><Tag color="purple">{w.kind}</Tag><a>{w.title}</a></>}
              description={w.snippet && <Typography.Text type="secondary" ellipsis style={{ fontSize: 12 }}>{w.snippet}</Typography.Text>} />
          </List.Item>
        )} />
      <WikiReaderDrawer path={open} onClose={() => setOpen(null)} onNavigate={setOpen} linkPages={linkPages} />
    </>
  );
}

export default RelatedWikiList;

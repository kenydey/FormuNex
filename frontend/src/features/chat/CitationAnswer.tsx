import { Card, Space, Tag, Typography, theme } from "antd";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Markdown } from "@/shared/Markdown";
import { evidenceToAnchors, maskCitationMarkers, parseFootnotes, rehypeCitationRefs, splitCitationMarkdown } from "./logic";
import type { Evidence } from "./types";

/** Source page of a KB citation (opens the document detail at the page). */
export const sourceHref = (sourceId: string, page?: number | null) =>
  `/knowledge/sources/${encodeURIComponent(sourceId)}${page != null ? `?page=${page}` : ""}`;

/**
 * Chat answer with ``[^n]`` citations: the full Markdown pipeline (GFM / KaTeX / SMILES / mermaid) renders the body,
 * markers become superscript links that scroll to the footnote card (title, snippet, page badge → source page).
 */
export function CitationAnswer({ content, citations }: { content: string; citations?: Evidence[] | null }) {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const [hl, setHl] = useState<string | null>(null);
  const split = useMemo(() => splitCitationMarkdown(content), [content]);
  const masked = useMemo(() => (split ? maskCitationMarkers(split.answer) : null), [split]);
  const plugins = useMemo(() => (masked ? [rehypeCitationRefs(masked.ids)] : []), [masked]);
  if (!split || !masked) return <Markdown content={content} mermaid />;
  const anchors = evidenceToAnchors(citations);
  const footnotes = parseFootnotes(split.footnotes, anchors);
  const scrollTo = (id: string) => {
    const el = document.getElementById(`fn-${id}`);
    if (!el) return;
    el.scrollIntoView?.({ behavior: "smooth", block: "center" });
    setHl(id);
    window.setTimeout(() => setHl((cur) => (cur === id ? null : cur)), 2000);
  };
  return (
    <div>
      <div onClick={(e) => {
        const a = (e.target as HTMLElement).closest?.("a.fx-cite-link");
        if (!a) return;
        e.preventDefault();
        scrollTo((a.getAttribute("href") || "").replace(/^#fn-/, ""));
      }}>
        <Markdown content={masked.text} rehypePlugins={plugins} mermaid />
      </div>
      {footnotes.length > 0 && (
        <Space direction="vertical" size={6} style={{ width: "100%", marginTop: 8 }} data-testid="citation-footnotes">
          <Typography.Text type="secondary">{t("chat.footnotes", "引用脚注")}</Typography.Text>
          {footnotes.map((f) => (
            <Card key={f.id} id={`fn-${f.id}`} size="small"
              style={{ background: hl === f.id ? token.colorPrimaryBg : undefined, transition: "background .3s" }}
              onMouseEnter={() => setHl(f.id)} onMouseLeave={() => setHl(null)}>
              <Space size={6} wrap>
                <Tag color="blue">[{f.id}]</Tag>
                {f.anchor && <Typography.Text strong>{f.anchor.title}</Typography.Text>}
                {f.anchor?.page != null && (f.anchor.sourceId
                  ? <Link to={sourceHref(f.anchor.sourceId, f.anchor.page)} data-testid={`citation-page-jump-${f.id}`}>
                      <Tag color="processing">p.{f.anchor.page}</Tag>
                    </Link>
                  : <Tag>p.{f.anchor.page}</Tag>)}
              </Space>
              {f.anchor?.snippet && <Typography.Paragraph type="secondary" ellipsis={{ rows: 3, expandable: true }} style={{ margin: "4px 0 0" }}>
                {f.anchor.snippet}
              </Typography.Paragraph>}
              {f.anchor?.url && <Typography.Link href={f.anchor.url} target="_blank" rel="noopener noreferrer">{f.anchor.url}</Typography.Link>}
              {f.content && <Markdown content={f.content} />}
            </Card>
          ))}
        </Space>
      )}
    </div>
  );
}

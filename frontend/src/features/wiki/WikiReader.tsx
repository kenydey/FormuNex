import { Descriptions, Image, Space, Tag, Tooltip, Typography, theme } from "antd";
import { memo, useMemo, useState } from "react";
import type { Components } from "react-markdown";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Markdown } from "@/shared/Markdown";
import { metaList, parseWikiFrontMatter } from "./frontMatter";
import { buildWikiLinkIndex, isKindKeyTarget, parseWikiHref, rewriteWikiLinks, type WikiLinkIndexEntry } from "./wikilinks";

export type WikiReaderPage = {
  path: string;
  title: string;
  kind?: string;
  flags?: string[];
  source_ids?: string[];
  markdown: string;
  norm_key?: string;
  updated_at?: string | null;
};

type Props = {
  page: WikiReaderPage;
  /** Pages [[wikilinks]] may resolve to (the current page is always included). */
  linkPages?: WikiLinkIndexEntry[];
  /** Handle wikilink clicks in place (e.g. inside a Drawer); default is a router link to the page. */
  onNavigate?: (path: string) => void;
};

/** Wiki page reader: front matter card, flags, Markdown (mermaid, KaTeX, SMILES), [[wikilinks]] as router links. */
function WikiReaderImpl({ page, linkPages = [], onNavigate }: Props) {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const [preview, setPreview] = useState<string | null>(null);
  const { meta, body } = useMemo(() => parseWikiFrontMatter(page.markdown || ""), [page.markdown]);
  const index = useMemo(() => {
    const merged = [...linkPages];
    if (page.path && !merged.some((p) => p.path === page.path)) merged.push({ path: page.path, title: page.title, norm_key: page.norm_key, kind: page.kind });
    return buildWikiLinkIndex(merged);
  }, [linkPages, page.path, page.title, page.norm_key, page.kind]);
  const content = useMemo(() => rewriteWikiLinks(body, index), [body, index]);

  const flags = page.flags?.length ? page.flags : metaList(meta, "flags");
  const sourceIds = page.source_ids?.length ? page.source_ids : metaList(meta, "source_ids");
  const bounds = typeof meta.bounds_json === "string" ? meta.bounds_json : meta.bounds_json != null ? JSON.stringify(meta.bounds_json) : null;

  const components: Components = useMemo(() => ({
    a({ href, children }) {
      const wiki = parseWikiHref(href);
      if (wiki?.kind === "path") {
        return onNavigate
          ? <a href={href} title={wiki.value} onClick={(e) => { e.preventDefault(); onNavigate(wiki.value); }}>{children}</a>
          : <Link to={href!} title={wiki.value}>{children}</Link>;
      }
      if (wiki?.kind === "dead") {
        const tip = isKindKeyTarget(wiki.value)
          ? t("wiki.dead_kind", "未编译：{{target}}（检查对应 kind / norm_key 是否已入库编译）", { target: wiki.value })
          : t("wiki.dead", "未编译：{{target}}（入库后自动编译，或检查标题 / path 是否匹配）", { target: wiki.value });
        return (
          <Tooltip title={tip}>
            <span style={{ color: token.colorTextDisabled, textDecoration: "underline dotted", cursor: "not-allowed" }}>{children}</span>
          </Tooltip>
        );
      }
      const external = /^https?:/i.test(href || "");
      return <a href={href} target={external ? "_blank" : undefined} rel={external ? "noreferrer noopener" : undefined}>{children}</a>;
    },
    img({ src, alt }) {
      if (!src || typeof src !== "string") return null;
      return (
        <img src={src} alt={alt || ""} title={t("wiki.zoom", "点击放大")} onClick={() => setPreview(src)}
          style={{ maxHeight: 256, maxWidth: "100%", objectFit: "contain", cursor: "zoom-in", borderRadius: token.borderRadius }} />
      );
    },
  }), [onNavigate, t, token.colorTextDisabled, token.borderRadius]);

  return (
    <div data-testid="wiki-reader">
      <Typography.Title level={4} style={{ marginTop: 0 }}>{page.title || page.path}</Typography.Title>
      <Space wrap size={[8, 4]} style={{ marginBottom: 8 }}>
        {page.kind && <Tag>{page.kind}</Tag>}
        <Typography.Text type="secondary" code copyable={{ text: page.path }}>{page.path}</Typography.Text>
        {page.updated_at && <Typography.Text type="secondary">{t("wiki.updated_at", "更新 {{at}}", { at: page.updated_at })}</Typography.Text>}
        {flags.map((f) => <Tag key={f} color="warning">{f}</Tag>)}
      </Space>
      {(sourceIds.length > 0 || bounds) && (
        <Descriptions size="small" bordered column={1} style={{ marginBottom: 12 }} items={[
          ...(sourceIds.length ? [{ key: "src", label: t("wiki.sources", "来源"), children: (
            <Space wrap size={4}>{sourceIds.map((sid) => (
              <Tooltip key={sid} title={sid}><Tag style={{ fontFamily: "monospace" }}>{sid.length > 18 ? `${sid.slice(0, 16)}…` : sid}</Tag></Tooltip>
            ))}</Space>
          ) }] : []),
          ...(bounds ? [{ key: "bounds", label: "bounds", children: <Typography.Text ellipsis={{ tooltip: bounds }} style={{ maxWidth: 480 }}>{bounds}</Typography.Text> }] : []),
        ]} />
      )}
      <Markdown content={content || "_empty_"} components={components} mermaid />
      {preview && (
        <Image src={preview} style={{ display: "none" }} preview={{ visible: true, src: preview, onVisibleChange: (v) => !v && setPreview(null) }} />
      )}
    </div>
  );
}

export const WikiReader = memo(WikiReaderImpl);
export default WikiReader;

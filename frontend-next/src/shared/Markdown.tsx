import { memo, useEffect, useId, useRef, useState, type ComponentProps } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";
import { useUi } from "@/app/store";
import "./markdown.css";

/**
 * Chemistry-aware Markdown (chat answers, wiki pages, reports): GFM tables, KaTeX math, ```smiles fences drawn as
 * 2D structures, optional ```mermaid diagrams (strict security, lazy-loaded). Links other than http(s) / mailto /
 * anchors are neutralised.
 */

let mermaidLoad: Promise<typeof import("mermaid")> | null = null;

export function MermaidBlock({ chart }: { chart: string }) {
  const id = useId().replace(/:/g, "");
  const host = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);
  const dark = useUi((s) => s.theme) === "dark";
  useEffect(() => {
    let cancelled = false;
    setFailed(false);
    (async () => {
      try {
        mermaidLoad ??= import("mermaid");
        const mermaid = (await mermaidLoad).default;
        mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: dark ? "dark" : "default" });
        const { svg } = await mermaid.render(`fx-mermaid-${id}`, chart);
        if (!cancelled && host.current) host.current.innerHTML = svg;
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [chart, id, dark]);
  if (failed) return <pre><code className="language-mermaid">{chart}</code></pre>;
  return <div ref={host} className="fx-mermaid" />;
}

export function SmilesDrawing({ smiles, width = 260, height = 180 }: { smiles: string; width?: number; height?: number }) {
  const ref = useRef<SVGSVGElement>(null);
  const [failed, setFailed] = useState(false);
  const dark = useUi((s) => s.theme) === "dark";
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const mod = (await import("smiles-drawer")) as unknown as { default?: unknown };
        const SD = (mod.default ?? mod) as {
          SvgDrawer: new (o: Record<string, unknown>) => { draw: (tree: unknown, el: SVGSVGElement, theme: string) => void };
          parse: (s: string, ok: (tree: unknown) => void, err: () => void) => void;
        };
        const drawer = new SD.SvgDrawer({ width, height, bondThickness: 1 });
        SD.parse(smiles.trim(), (tree) => {
          if (!cancelled && ref.current) drawer.draw(tree, ref.current, dark ? "dark" : "light");
        }, () => !cancelled && setFailed(true));
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [smiles, width, height, dark]);
  return (
    <span className="fx-smiles">
      {!failed && <svg ref={ref} width={width} height={height} />}
      <code>{smiles.trim()}</code>
    </span>
  );
}

export type MarkdownProps = {
  content: string;
  components?: Components;
  mermaid?: boolean;
  rehypePlugins?: Exclude<ComponentProps<typeof ReactMarkdown>["rehypePlugins"], null | undefined>;
  remarkPlugins?: Exclude<ComponentProps<typeof ReactMarkdown>["remarkPlugins"], null | undefined>;
};

export const safeUrl = (url: string) => (/^(https?:|mailto:|#|\/)/i.test(url) ? url : "#");

function MarkdownImpl({ content, components, mermaid = false, rehypePlugins = [], remarkPlugins = [] }: MarkdownProps) {
  const base: Components = {
    code({ className, children, ...props }) {
      const text = String(children ?? "");
      if (/language-smiles/.test(className || "")) return <SmilesDrawing smiles={text} />;
      if (mermaid && /language-mermaid/.test(className || "")) return <MermaidBlock chart={text} />;
      return <code className={className} {...props}>{children}</code>;
    },
    a({ href, children }) {
      const external = /^https?:/i.test(href || "");
      return <a href={href} target={external ? "_blank" : undefined} rel={external ? "noreferrer noopener" : undefined}>{children}</a>;
    },
  };
  return (
    <div className="fx-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath, ...remarkPlugins]} rehypePlugins={[rehypeKatex, ...rehypePlugins]}
        components={{ ...base, ...components }} urlTransform={safeUrl}>
        {content}
      </ReactMarkdown>
    </div>
  );
}

export const Markdown = memo(MarkdownImpl);

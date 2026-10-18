import { theme } from "antd";
import { useEffect, useRef } from "react";

/** Scrolling monospace view of streamed `docker compose` output. */
export function LogView({ lines, height = 220 }: { lines: string[]; height?: number }) {
  const { token } = theme.useToken();
  const ref = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [lines]);
  if (!lines.length) return null;
  return (
    <pre ref={ref} data-testid="stack-log" style={{
      height, overflow: "auto", margin: 0, padding: 8, fontSize: 12, borderRadius: token.borderRadius,
      background: token.colorFillTertiary, color: token.colorText, whiteSpace: "pre-wrap", wordBreak: "break-all",
    }}>{lines.join("\n")}</pre>
  );
}

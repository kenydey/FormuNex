import { Button, Result, Space, Typography } from "antd";
import { Component, type ErrorInfo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

/** First stack frames that point at app code (the message alone rarely locates the crash). */
export function appFrames(stack: string | undefined, n = 4): string {
  return (stack || "").split("\n").filter((l) => l.includes("src/") || l.includes("FormuNex") || l.includes("<")).slice(0, n).join("\n");
}

/** A failed lazy chunk (stale deploy, flaky network) is fixed by reloading, not by retrying the render. */
export const isChunkError = (e: Error) => /dynamically imported module|Loading chunk|Importing a module script failed/i.test(e.message);

function Fallback({ error, onRetry, fullPage }: { error: Error; onRetry: () => void; fullPage?: boolean }) {
  const { t } = useTranslation();
  const frames = appFrames(error.stack);
  return (
    <Result status="error" style={fullPage ? { minHeight: "100vh", paddingTop: "15vh" } : undefined}
      title={isChunkError(error) ? t("shell.error.chunk", "页面资源加载失败（可能已发布新版本）") : t("shell.error.title", "界面发生错误")}
      subTitle={<Typography.Text code style={{ wordBreak: "break-all" }}>{error.message || t("shell.error.unknown", "未知错误")}</Typography.Text>}
      extra={<Space>
        {!fullPage && !isChunkError(error) && <Button onClick={onRetry}>{t("shell.error.retry", "重试")}</Button>}
        <Button type="primary" onClick={() => window.location.reload()}>{t("shell.error.reload", "重新加载")}</Button>
      </Space>}>
      {frames && <pre style={{ maxHeight: 160, overflow: "auto", fontSize: 12, margin: 0 }}>{frames}</pre>}
    </Result>
  );
}

/**
 * Error boundary: ``fullPage`` at the app root (outside the router), otherwise around the shell's page outlet with
 * ``resetKey`` = pathname, so navigating away from a crashed page recovers without a reload.
 */
export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string; fullPage?: boolean }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("FormuNex UI error:", error, info.componentStack);
  }

  componentDidUpdate(prev: { resetKey?: string }) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState({ error: null });
  }

  render() {
    if (this.state.error) return <Fallback error={this.state.error} fullPage={this.props.fullPage} onRetry={() => this.setState({ error: null })} />;
    return this.props.children;
  }
}

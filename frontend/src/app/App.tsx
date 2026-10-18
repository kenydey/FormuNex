import { ProConfigProvider } from "@ant-design/pro-components";
import { App as AntApp, ConfigProvider } from "antd";
import enUS from "antd/locale/en_US";
import zhCN from "antd/locale/zh_CN";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect } from "react";
import { RouterProvider } from "react-router-dom";
import dayjs from "dayjs";
import "dayjs/locale/zh-cn";
import i18n from "./i18n";
import { router } from "./router";
import { SessionProvider } from "./session";
import { useUi } from "./store";
import { themeConfig } from "./theme";
import { SignatureProvider } from "@/shared/SignatureModal";
import { ApiError } from "@/api/client";
import { ErrorBoundary } from "@/features/chat/ErrorBoundary";
import { startDeepLinks } from "@/desktop/runtime";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: false,
      retry: (n, e) => !(e instanceof ApiError && e.status < 500) && n < 2,
    },
  },
});

export default function App() {
  const { theme, locale } = useUi();
  useEffect(() => {
    // Desktop: open formunex://… links in the running window.
    void startDeepLinks((route) => void router.navigate(route));
  }, []);
  useEffect(() => {
    void i18n.changeLanguage(locale);
    dayjs.locale(locale === "zh-CN" ? "zh-cn" : "en");
    document.documentElement.lang = locale;
  }, [locale]);
  return (
    <ConfigProvider locale={locale === "zh-CN" ? zhCN : enUS} theme={themeConfig(theme)}>
      <ProConfigProvider dark={theme === "dark"}>
        <AntApp>
          <ErrorBoundary fullPage>
            <QueryClientProvider client={queryClient}>
              <SessionProvider>
                <SignatureProvider>
                  <RouterProvider router={router} />
                </SignatureProvider>
              </SessionProvider>
            </QueryClientProvider>
          </ErrorBoundary>
        </AntApp>
      </ProConfigProvider>
    </ConfigProvider>
  );
}

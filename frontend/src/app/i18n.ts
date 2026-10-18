/**
 * i18n (方案 §3.1): Chinese text is written inline as the default value — ``t("plm.gate.title", "发布门禁")`` —
 * and English lives in locales/en-US.ts. ``i18n.test.ts`` fails when a key used in the code has no English entry.
 */
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "@/locales/en-US";

void i18n.use(initReactI18next).init({
  resources: { "en-US": { translation: en }, "zh-CN": { translation: {} } },
  lng: "zh-CN",
  fallbackLng: false,
  interpolation: { escapeValue: false },
  returnNull: false,
  keySeparator: false,
  nsSeparator: false,
});

export default i18n;

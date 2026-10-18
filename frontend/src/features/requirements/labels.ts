import type { TFunction } from "i18next";
import type { ConstraintKey } from "./constraints";

/** Localised catalogue-constraint labels (static keys so the i18n extractor sees them). */
export function constraintLabel(t: TFunction, key: ConstraintKey): string {
  switch (key) {
    case "voc_limit_gpl": return t("requirements.c.voc_limit_gpl", "VOC 上限");
    case "cure_temperature_c": return t("requirements.c.cure_temperature_c", "固化温度上限");
    case "ph_target": return t("requirements.c.ph_target", "pH 目标");
    case "salt_spray_hours": return t("requirements.c.salt_spray_hours", "耐盐雾目标");
    case "film_weight_gsm": return t("requirements.c.film_weight_gsm", "干膜重目标");
    case "cleaning_efficiency": return t("requirements.c.cleaning_efficiency", "清洗率目标");
    default: return key;
  }
}

/** Localised name of a trace consumer (engine stage). */
export function consumerLabel(t: TFunction, c: string): string {
  switch (c) {
    case "recommend": return t("requirements.trace.c.recommend", "配方推荐");
    case "doe": return t("requirements.trace.c.doe", "DOE");
    case "validate": return t("requirements.trace.c.validate", "合规校验");
    case "process": return t("requirements.trace.c.process", "工艺");
    default: return c;
  }
}

export function availabilityLabel(t: TFunction, a: string | undefined | null): string {
  switch (a ?? "in_stock") {
    case "in_stock": return t("requirements.avail.in_stock", "在库");
    case "restricted": return t("requirements.avail.restricted", "受限");
    case "discontinued": return t("requirements.avail.discontinued", "停产");
    default: return String(a);
  }
}

export const AVAIL_COLOR: Record<string, string> = { in_stock: "success", restricted: "warning", discontinued: "error" };

export function kbStatusLabel(t: TFunction, s: string): string {
  switch (s) {
    case "queued": return t("requirements.kb.queued", "待入库");
    case "fetching": return t("requirements.kb.fetching", "获取全文");
    case "indexing": return t("requirements.kb.indexing", "入库中");
    case "indexed": return t("requirements.kb.indexed", "已入库");
    case "skipped": return t("requirements.kb.skipped", "已在库");
    case "failed": return t("requirements.kb.failed", "失败");
    case "archived": return t("requirements.kb.archived", "已归档");
    default: return s;
  }
}

import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

/** Link to the environment-flag toggle in the settings centre (``/admin/settings/env?focus=<flag>``). */
export function FlagLink({ flag }: { flag: string }) {
  const { t } = useTranslation();
  return <Link to={`/admin/settings/env?focus=${encodeURIComponent(flag)}`}>{t("wiki.open_flag", "去开启")}</Link>;
}

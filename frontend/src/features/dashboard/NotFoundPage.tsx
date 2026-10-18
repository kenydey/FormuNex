import { Button, Result } from "antd";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

export default function NotFoundPage() {
  const { t } = useTranslation();
  return <Result status="404" title="404" subTitle={t("common.not_found", "页面不存在")}
    extra={<Link to="/dashboard"><Button type="primary">{t("nav.dashboard", "我的工作台")}</Button></Link>} />;
}

import { ExportOutlined } from "@ant-design/icons";
import { Button, Drawer, Skeleton } from "antd";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ErrorAlert } from "@/shared/feedback";
import { wikiApi } from "./api";
import { wikiHref } from "./paths";
import type { WikiLinkIndexEntry } from "./wikilinks";
import { WikiReader } from "./WikiReader";

/** Wiki page in a side drawer; [[wikilinks]] navigate inside the drawer, "open" goes to the full wiki route. */
export function WikiReaderDrawer({ path, onClose, onNavigate, linkPages }: {
  path: string | null;
  onClose: () => void;
  onNavigate: (path: string) => void;
  linkPages?: WikiLinkIndexEntry[];
}) {
  const { t } = useTranslation();
  const page = useQuery({ queryKey: ["wiki", "page", path], queryFn: () => wikiApi.byPath(path!), enabled: !!path });
  return (
    <Drawer open={!!path} onClose={onClose} width={640} destroyOnHidden title={t("wiki.reader", "Wiki 阅读")}
      extra={path && <Link to={wikiHref(path)}><Button size="small" icon={<ExportOutlined />}>{t("wiki.open_full", "在 Wiki 中打开")}</Button></Link>}>
      <ErrorAlert error={page.error} />
      {page.isLoading ? <Skeleton active /> : page.data && <WikiReader page={page.data} linkPages={linkPages} onNavigate={onNavigate} />}
    </Drawer>
  );
}

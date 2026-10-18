import { Table, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

export interface Change {
  path: string;
  kind: "added" | "removed" | "changed";
  old: unknown;
  new: unknown;
  major: boolean;
}

const show = (v: unknown) =>
  v === null || v === undefined ? "—" : typeof v === "string" ? v : JSON.stringify(v, null, 0);

/** Field-level document diff (方案 §5.5.8); major changes need sign-off again. */
export function StructuredDiff({ changes }: { changes: Change[] }) {
  const { t } = useTranslation();
  return (
    <Table<Change>
      size="small"
      rowKey="path"
      pagination={false}
      dataSource={changes}
      columns={[
        { title: t("diff.field", "字段"), dataIndex: "path", width: 240,
          render: (p: string, r) => <span>{r.major && <Tag color="red">{t("diff.major", "重大")}</Tag>}<code>{p}</code></span> },
        { title: t("diff.kind", "变化"), dataIndex: "kind", width: 80,
          render: (k: string) => <Tag color={k === "added" ? "green" : k === "removed" ? "red" : "blue"}>{t(`diff.${k}`, { added: "新增", removed: "删除", changed: "修改" }[k] ?? k)}</Tag> },
        { title: t("diff.old", "原值"), dataIndex: "old", render: (v) => <Typography.Text delete type="secondary">{show(v)}</Typography.Text> },
        { title: t("diff.new", "新值"), dataIndex: "new", render: (v) => <Typography.Text>{show(v)}</Typography.Text> },
      ]}
    />
  );
}

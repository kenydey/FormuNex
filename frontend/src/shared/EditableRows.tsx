import { EditableProTable, type ProColumns } from "@ant-design/pro-components";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

type Row<T> = T & { _key: string };
let seq = 0;
const key = () => `r${Date.now().toString(36)}${(seq++).toString(36)}`;

/** Inline-editable list (every row always editable) over plain objects; the internal row key never leaks out. */
export function EditableRows<T extends object>({ value, onChange, columns, newRow, addLabel, max }: {
  value: T[] | undefined;
  onChange: (rows: T[]) => void;
  columns: ProColumns<Row<T>>[];
  newRow: () => T;
  addLabel?: string;
  max?: number;
}) {
  const { t } = useTranslation();
  const [rows, setRows] = useState<Row<T>[]>(() => (value ?? []).map((v) => ({ ...v, _key: key() })));
  useEffect(() => {
    // Re-key only when the outside value really changed (e.g. a reload), not on our own echo.
    const strip = rows.map(({ _key, ...r }) => r);
    if (JSON.stringify(strip) !== JSON.stringify(value ?? [])) setRows((value ?? []).map((v) => ({ ...v, _key: key() })));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  const editable = useMemo(() => rows.map((r) => r._key), [rows]);
  // recordCreatorProps.newRecordType must stay "dataSource": with the default cache mode the
  // new row lives in pro-table's internal newLineRecord cache, whose edit state never lands in
  // our controlled editableKeys (no editable.onChange), so it renders read-only (no inputs) and
  // never reaches `value`. DataSource mode inserts it into rows, and `editable` picks its key up.
  const emit = (next: Row<T>[]) => {
    setRows(next);
    onChange(next.map(({ _key, ...r }) => r as unknown as T));
  };
  return (
    <EditableProTable<Row<T>>
      rowKey="_key"
      size="small"
      value={rows}
      onChange={(next) => emit([...next] as Row<T>[])}
      columns={[...columns, {
        title: t("common.actions", "操作"), valueType: "option", width: 70,
        render: () => null,
      }]}
      maxLength={max}
      recordCreatorProps={{ creatorButtonText: addLabel ?? t("common.add_row", "添加一行"), newRecordType: "dataSource", record: () => ({ ...newRow(), _key: key() }) as Row<T> }}
      editable={{
        type: "multiple",
        editableKeys: editable,
        actionRender: (_row, _cfg, dom) => [dom.delete],
        onValuesChange: (_r, all) => emit(all as Row<T>[]),
        deletePopconfirmMessage: t("common.confirm_delete", "确定删除？"),
        onDelete: async (k) => emit(rows.filter((r) => r._key !== k)),
      }}
    />
  );
}

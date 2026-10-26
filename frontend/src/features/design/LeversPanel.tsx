import { ProCard } from "@ant-design/pro-components";
import { Alert, App, Button, Empty, List, Popconfirm, Space, Tag, Typography } from "antd";
import { useMutation } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useSession } from "@/app/session";
import { EditableRows } from "@/shared/EditableRows";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { factorHintsToLevers, leverProblems, leverToRow, rowToLever, type LeverRow } from "./logic";
import type { FactorCandidate, LeverSpec } from "./types";
import { useRequirement } from "./useRequirement";

/** Inline-editable lever table (continuous range or discrete levels with an optional material map). */
export function LeversEditor({ value, onChange }: { value: LeverRow[]; onChange: (rows: LeverRow[]) => void }) {
  const { t } = useTranslation();
  return (
    <EditableRows<LeverRow>
      value={value} onChange={onChange} addLabel={t("design.levers.add", "添加因子")}
      newRow={() => ({ name: "", low: 0, high: 10, unit: "wt%", kind: "continuous", levels: "", material_map: "" })}
      columns={[
        { title: t("design.levers.name", "因子名称"), dataIndex: "name", width: 180 },
        { title: t("design.levers.kind", "类型"), dataIndex: "kind", valueType: "select", width: 110, valueEnum: {
          continuous: { text: t("design.levers.continuous", "连续") }, discrete: { text: t("design.levers.discrete", "离散") } } },
        { title: t("design.levers.low", "下限"), dataIndex: "low", valueType: "digit", width: 100 },
        { title: t("design.levers.high", "上限"), dataIndex: "high", valueType: "digit", width: 100 },
        { title: t("design.levers.unit", "单位"), dataIndex: "unit", width: 90 },
        { title: t("design.levers.levels", "水平（离散，逗号分隔，≥2 个）"), dataIndex: "levels",
          fieldProps: { placeholder: t("design.levers.levels_ph", "例如：聚酰胺, 酚醛 或 1, 2, 3") } },
        { title: t("design.levers.material_map", "材料替换映射（水平=成分名）"), dataIndex: "material_map", tooltip:
          t("design.levers.material_map_tip", "字符串水平命中时，用映射的成分名替换该成分（重量不变）；不填则保持跳过"),
          fieldProps: { placeholder: t("design.levers.material_map_ph", "例如：树脂B=聚氨酯树脂X") } },
      ]}
    />
  );
}

/** Project levers: edit, load domain defaults, take AI/KB factor suggestions, save into the project requirement. */
export function LeversPanel({ onSaved }: { onSaved?: (levers: LeverSpec[]) => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { can } = useSession();
  const { project, requirement, levers, saveLevers, projectId } = useRequirement();
  const [rows, setRows] = useState<LeverRow[]>([]);
  const [dirty, setDirty] = useState(false);
  const [hints, setHints] = useState<FactorCandidate[] | null>(null);
  const leversKey = JSON.stringify(levers);
  useEffect(() => {
    setRows(levers.map(leverToRow));
    setDirty(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leversKey]);

  const defaults = useMutation({
    mutationFn: () => {
      const q = new URLSearchParams({ domain: requirement?.domain ?? "anticorrosion_coating" });
      if (requirement?.substrate) q.set("substrate", String(requirement.substrate));
      const cure = requirement?.cure_temperature_c;
      if (typeof cure === "number") q.set("cure_temperature_c", String(cure));
      return call<{ levers: LeverSpec[] }>("GET", `/api/meta/default-levers?${q}`);
    },
    onSuccess: (r) => { setRows(r.levers.map(leverToRow)); setDirty(true); },
    onError: (e) => message.error(errorText(e)),
  });
  const suggest = useMutation({
    mutationFn: () => call<{ factors: FactorCandidate[]; count: number }>("POST", "/api/doe/suggest-factors", requirement),
    onSuccess: (r) => setHints(r.factors),
    onError: (e) => message.error(errorText(e)),
  });
  const problems = leverProblems(rows);
  const save = () => {
    const out = rows.map(rowToLever);
    saveLevers.mutate(out, {
      onSuccess: () => { message.success(t("common.saved", "已保存")); setDirty(false); onSaved?.(out); },
      onError: (e) => message.error(errorText(e)),
    });
  };

  if (!projectId) return <Alert type="info" showIcon message={t("design.need_project", "请先在顶部选择项目。")} />;
  return (
    <>
      <ErrorAlert error={project.error} />
      <ProCard bordered title={t("design.levers.title", "DOE 因子 · Levers")} loading={project.isLoading}
        extra={<Space wrap>
          <Popconfirm title={t("design.levers.defaults_confirm", "用产品域默认因子替换当前列表？")} onConfirm={() => defaults.mutate()}>
            <Button loading={defaults.isPending}>{t("design.levers.defaults", "载入默认因子")}</Button>
          </Popconfirm>
          <Button loading={suggest.isPending} disabled={!can("doe.write")} onClick={() => suggest.mutate()}>{t("design.levers.suggest", "AI 建议因子")}</Button>
          <Button type="primary" disabled={!dirty || problems.length > 0 || !can("project.write")} loading={saveLevers.isPending} onClick={save}>
            {t("common.save", "保存")}
          </Button>
        </Space>}>
        <Typography.Paragraph type="secondary">
          {t("design.levers.hint", "未定义因子时将自动从当前配方推导。建议为当前基材显式定义 g/L 或 wt% 因子；离散因子可填写材料替换映射。")}
        </Typography.Paragraph>
        <LeversEditor value={rows} onChange={(r) => { setRows(r); setDirty(true); }} />
        {problems.length > 0 && <Alert style={{ marginTop: 12 }} type="warning" showIcon
          message={t("design.levers.problems", "因子有误：{{p}}", { p: problems.join("；") })} />}
      </ProCard>
      {hints && (
        <ProCard bordered style={{ marginTop: 16 }} title={t("design.levers.hints", "KB + 需求 levers 因子建议")}
          extra={hints.length > 0 && <Button onClick={() => { setRows(factorHintsToLevers(hints).map(leverToRow)); setDirty(true); }}>
            {t("design.levers.apply", "应用到 levers")}</Button>}>
          {hints.length === 0 ? <Empty description={t("design.levers.no_hints", "没有可用的因子建议")} /> : (
            <List size="small" dataSource={hints} rowKey="name" renderItem={(f) => (
              <List.Item>
                <List.Item.Meta
                  title={<Space wrap><Typography.Text code>{f.name}</Typography.Text>
                    {f.kind === "discrete" && f.levels ? <Tag color="gold">{t("design.levers.discrete", "离散")}: {f.levels.join(", ")}</Tag>
                      : <Tag>{f.low} – {f.high} {f.unit}</Tag>}
                    <Tag>{f.source}</Tag></Space>}
                  description={f.rationale} />
              </List.Item>
            )} />
          )}
        </ProCard>
      )}
    </>
  );
}

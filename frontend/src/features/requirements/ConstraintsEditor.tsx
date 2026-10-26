import { DeleteOutlined, UndoOutlined } from "@ant-design/icons";
import { ProCard } from "@ant-design/pro-components";
import { Button, Col, Empty, Form, InputNumber, Popconfirm, Row, Select, Slider, Typography } from "antd";
import { useTranslation } from "react-i18next";
import { EditableRows } from "@/shared/EditableRows";
import {
  CONSTRAINT_CATALOG, addConstraint, appliesToDomain, constraintDef, customConstraintRows, getConstraintValue, removeConstraint,
  resetConstraints, setConstraintValue, syncCustomConstraints, type ConstraintKey, type ConstraintRequirement,
  type CustomConstraintRow,
} from "./constraints";
import { constraintLabel } from "./labels";

/**
 * 工艺约束 (legacy ConstraintsEditor): catalogue constraints stored on the Requirement plus free-form
 * ``constraint_values``. Both feed the recommend scoring and DOE screening (see EffectTrace).
 */
export function ConstraintsEditor<R extends ConstraintRequirement>({ requirement, activeKeys, onChange, disabled }: {
  requirement: R;
  activeKeys: ConstraintKey[];
  onChange: (req: R, keys: ConstraintKey[]) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const domain = requirement.domain;
  const active = activeKeys.map(constraintDef).filter((d): d is NonNullable<typeof d> => !!d && appliesToDomain(d, domain));
  const addable = CONSTRAINT_CATALOG.filter((c) => appliesToDomain(c, domain) && !activeKeys.includes(c.key));
  const customRows = customConstraintRows(requirement);

  return (
    <ProCard size="small" bordered title={t("requirements.constraints.title", "工艺约束")}
      subTitle={t("requirements.constraints.sub", "进入配方推荐评分与 DOE 预筛")}
      extra={
        <Popconfirm title={t("requirements.constraints.reset_confirm", "按当前产品领域重置为默认约束？")} onConfirm={() => { const r = resetConstraints(requirement); onChange(r.req, r.keys); }} disabled={disabled}>
          <Button size="small" icon={<UndoOutlined />} disabled={disabled}>{t("requirements.constraints.reset", "重置默认")}</Button>
        </Popconfirm>
      }>
      {active.length === 0 && customRows.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t("requirements.constraints.empty", "暂无约束，可从下方添加")} />}
      {active.map((def) => {
        const v = getConstraintValue(requirement, def.key);
        return (
          <Row key={def.key} gutter={12} align="middle" style={{ marginBottom: 8 }}>
            <Col flex="160px"><Typography.Text>{constraintLabel(t, def.key)}</Typography.Text></Col>
            <Col flex="auto">
              <Slider min={def.min} max={def.max} step={def.step} value={v} disabled={disabled}
                onChange={(n) => onChange(setConstraintValue(requirement, def.key, n), activeKeys)} />
            </Col>
            <Col flex="150px">
              <InputNumber size="small" min={def.min} max={def.max} step={def.step} value={v} disabled={disabled} suffix={def.unit || undefined}
                onChange={(n) => n != null && onChange(setConstraintValue(requirement, def.key, n), activeKeys)} style={{ width: "100%" }} />
            </Col>
            <Col flex="32px">
              <Button size="small" type="text" danger icon={<DeleteOutlined />} disabled={disabled} aria-label={t("common.delete", "删除")}
                onClick={() => { const r = removeConstraint(requirement, activeKeys, def.key); onChange(r.req, r.keys); }} />
            </Col>
          </Row>
        );
      })}
      <Typography.Title level={5} style={{ marginTop: 16 }}>{t("requirements.constraints.custom", "自定义约束")}</Typography.Title>
      <Typography.Paragraph type="secondary">{t("requirements.constraints.custom_help", "与目标契约同形态：名称、单位、类型（上限 / 下限 / 区间 / 目标值）与数值；单值行同步写入约束值，区间行只作区间记录。旧单值自定义项自动转为目标值行。")}</Typography.Paragraph>
      <EditableRows<CustomConstraintRow>
        value={customRows}
        onChange={(rows) => onChange(syncCustomConstraints(requirement, customRows, rows), activeKeys)}
        newRow={() => ({ name: "", unit: "", kind: "target", value: 0 })}
        columns={[
          { title: t("requirements.constraints.custom_name", "约束名称"), dataIndex: "name", formItemProps: { rules: [{ required: true }] } },
          { title: t("spec.unit", "单位"), dataIndex: "unit", width: 90 },
          { title: t("requirements.constraints.custom_kind", "类型"), dataIndex: "kind", valueType: "select", width: 130, valueEnum: {
            upper: t("requirements.constraints.kind_upper", "≤ 上限"), lower: t("requirements.constraints.kind_lower", "≥ 下限"),
            range: t("requirements.constraints.kind_range", "区间"), target: t("requirements.constraints.kind_target", "目标值") } },
          { title: t("spec.min", "下限"), dataIndex: "low", valueType: "digit", width: 110 },
          { title: t("spec.max", "上限"), dataIndex: "high", valueType: "digit", width: 110 },
          { title: t("requirements.constraints.custom_value", "值"), dataIndex: "value", valueType: "digit", width: 110 },
        ]}
      />
      <Form layout="vertical" style={{ marginTop: 16 }}>
        {addable.length > 0 && (
          <Form.Item label={t("requirements.constraints.add_preset", "添加预设约束")}>
            <Select value={null} disabled={disabled} placeholder={t("requirements.constraints.pick", "选择约束…")} style={{ maxWidth: 320 }}
              options={addable.map((c) => ({ value: c.key, label: constraintLabel(t, c.key) }))}
              onChange={(k: ConstraintKey | null) => { if (k) { const r = addConstraint(requirement, activeKeys, k); onChange(r.req, r.keys); } }} />
          </Form.Item>
        )}
      </Form>
    </ProCard>
  );
}

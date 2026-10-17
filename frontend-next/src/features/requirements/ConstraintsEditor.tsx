import { DeleteOutlined, PlusOutlined, UndoOutlined } from "@ant-design/icons";
import { ProCard } from "@ant-design/pro-components";
import { Button, Col, Empty, Form, Input, InputNumber, Popconfirm, Row, Select, Slider, Space, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  CONSTRAINT_CATALOG, addConstraint, appliesToDomain, constraintDef, customConstraints, getConstraintValue, removeConstraint,
  removeCustomConstraint, resetConstraints, setConstraintValue, setCustomConstraint, type ConstraintKey, type ConstraintRequirement,
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
  const [name, setName] = useState("");
  const [value, setValue] = useState<number | null>(0);
  const domain = requirement.domain;
  const active = activeKeys.map(constraintDef).filter((d): d is NonNullable<typeof d> => !!d && appliesToDomain(d, domain));
  const addable = CONSTRAINT_CATALOG.filter((c) => appliesToDomain(c, domain) && !activeKeys.includes(c.key));
  const custom = customConstraints(requirement);
  const addCustom = () => {
    if (!name.trim()) return;
    onChange(setCustomConstraint(requirement, name, value ?? 0), activeKeys);
    setName("");
    setValue(0);
  };

  return (
    <ProCard size="small" bordered title={t("requirements.constraints.title", "工艺约束")}
      subTitle={t("requirements.constraints.sub", "进入配方推荐评分与 DOE 预筛")}
      extra={
        <Popconfirm title={t("requirements.constraints.reset_confirm", "按当前产品领域重置为默认约束？")} onConfirm={() => { const r = resetConstraints(requirement); onChange(r.req, r.keys); }} disabled={disabled}>
          <Button size="small" icon={<UndoOutlined />} disabled={disabled}>{t("requirements.constraints.reset", "重置默认")}</Button>
        </Popconfirm>
      }>
      {active.length === 0 && custom.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t("requirements.constraints.empty", "暂无约束，可从下方添加")} />}
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
              <InputNumber size="small" min={def.min} max={def.max} step={def.step} value={v} disabled={disabled} addonAfter={def.unit || undefined}
                onChange={(n) => n != null && onChange(setConstraintValue(requirement, def.key, n), activeKeys)} style={{ width: "100%" }} />
            </Col>
            <Col flex="32px">
              <Button size="small" type="text" danger icon={<DeleteOutlined />} disabled={disabled} aria-label={t("common.delete", "删除")}
                onClick={() => { const r = removeConstraint(requirement, activeKeys, def.key); onChange(r.req, r.keys); }} />
            </Col>
          </Row>
        );
      })}
      {custom.length > 0 && (
        <>
          <Typography.Text type="secondary">{t("requirements.constraints.custom", "自定义约束")}</Typography.Text>
          {custom.map(([n, v]) => (
            <Row key={n} gutter={12} align="middle" style={{ marginTop: 8 }}>
              <Col flex="160px"><Typography.Text>{n}</Typography.Text></Col>
              <Col flex="auto">
                <InputNumber size="small" value={v} disabled={disabled} style={{ width: 200 }}
                  onChange={(x) => x != null && onChange(setCustomConstraint(requirement, n, x), activeKeys)} />
              </Col>
              <Col flex="32px">
                <Button size="small" type="text" danger icon={<DeleteOutlined />} disabled={disabled} aria-label={t("common.delete", "删除")}
                  onClick={() => onChange(removeCustomConstraint(requirement, n), activeKeys)} />
              </Col>
            </Row>
          ))}
        </>
      )}
      <Form layout="vertical" style={{ marginTop: 16 }}>
        {addable.length > 0 && (
          <Form.Item label={t("requirements.constraints.add_preset", "添加预设约束")}>
            <Select value={null} disabled={disabled} placeholder={t("requirements.constraints.pick", "选择约束…")} style={{ maxWidth: 320 }}
              options={addable.map((c) => ({ value: c.key, label: constraintLabel(t, c.key) }))}
              onChange={(k: ConstraintKey | null) => { if (k) { const r = addConstraint(requirement, activeKeys, k); onChange(r.req, r.keys); } }} />
          </Form.Item>
        )}
        <Form.Item label={t("requirements.constraints.add_custom", "添加自定义约束")}>
          <Space.Compact>
            <Input placeholder={t("requirements.constraints.custom_name", "约束名称")} value={name} disabled={disabled}
              onChange={(e) => setName(e.target.value)} onPressEnter={addCustom} style={{ width: 200 }} />
            <InputNumber placeholder={t("requirements.constraints.custom_value", "值")} value={value} disabled={disabled} onChange={setValue} style={{ width: 120 }} />
            <Button icon={<PlusOutlined />} disabled={disabled || !name.trim()} onClick={addCustom}>{t("requirements.constraints.add", "添加")}</Button>
          </Space.Compact>
        </Form.Item>
      </Form>
    </ProCard>
  );
}

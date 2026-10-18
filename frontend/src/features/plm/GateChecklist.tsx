import { CheckCircleTwoTone, CloseCircleTwoTone, ExclamationCircleTwoTone, MinusCircleTwoTone } from "@ant-design/icons";
import { App, Button, Collapse, List, Space, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useSession } from "@/app/session";
import { useSignature } from "@/shared/SignatureModal";
import { errorText } from "@/shared/feedback";
import { StatusTag } from "@/shared/tags";
import type { Gate, GateCheck } from "./types";

const ICON: Record<string, JSX.Element> = {
  pass: <CheckCircleTwoTone twoToneColor="#52c41a" />, fail: <CloseCircleTwoTone twoToneColor="#ff4d4f" />,
  waived: <ExclamationCircleTwoTone twoToneColor="#faad14" />, not_applicable: <MinusCircleTwoTone twoToneColor="#bfbfbf" />,
};
const WAIVE_PERM: Record<string, string> = { objectives: "spec.write", data_gaps: "regulatory.review" };

/** 发布门禁 Checklist (方案 §3.6): every check passes / fails / is waived, with evidence; waivers are e-signed. */
export function GateChecklist({ gate, revisionId, projectId, onChange, readOnly }: {
  gate: Gate; revisionId: string; projectId?: string | null; onChange: () => void; readOnly?: boolean;
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { can } = useSession();
  const sign = useSignature();
  const waive = async (check: GateCheck, item: string) => {
    const s = await sign({
      title: t("gate.waive", "豁免门禁项"), comment: "required", commentLabel: t("gate.waive_reason", "豁免理由"),
      meaning: t("gate.waive_meaning", "我确认豁免 {{code}} 中的这一项，并对此决定负责。", { code: check.code }),
    });
    if (!s) return;
    try {
      await call("POST", `/api/plm/revisions/${revisionId}/gate/waivers`, { check: check.key, item, reason: s.comment, password: s.password });
      message.success(t("gate.waived", "已豁免"));
      onChange();
    } catch (e) {
      message.error(errorText(e));
    }
  };
  return (
    <Collapse defaultActiveKey={gate.checks.filter((c) => c.status === "fail").map((c) => c.key)} items={gate.checks.map((c) => ({
      key: c.key,
      label: <Space>{ICON[c.status]}<Typography.Text strong>{c.code}</Typography.Text>{c.title}<StatusTag status={c.status} />
        {c.waivable && <Tag>{t("gate.waivable", "可豁免")}</Tag>}</Space>,
      children: (
        <>
          {c.notes.map((n, i) => <Typography.Paragraph key={i} type="secondary">{n}</Typography.Paragraph>)}
          <List size="small" dataSource={c.items} locale={{ emptyText: t("gate.ok", "无问题") }} renderItem={(i) => (
            <List.Item extra={i.waived ? <Tag color="gold">{t("gate.waived_by", "已豁免：{{r}}", { r: i.waiver?.reason })}</Tag>
              : i.waivable && !readOnly && can(WAIVE_PERM[c.key] ?? "release.sign", projectId)
                ? <Button size="small" onClick={() => void waive(c, i.key)}>{t("gate.waive", "豁免")}</Button> : null}>
              {i.message}
            </List.Item>
          )} />
          {Object.keys(c.evidence).length > 0 && <pre style={{ fontSize: 12, margin: 0, opacity: 0.75 }}>{JSON.stringify(c.evidence, null, 1)}</pre>}
        </>
      ),
    }))} />
  );
}

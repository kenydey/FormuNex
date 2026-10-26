import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Alert, App, Button, Descriptions, List, Space, Steps, Table, Tag, Typography } from "antd";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call, download } from "@/api/client";
import { useSession } from "@/app/session";
import { useSignature } from "@/shared/SignatureModal";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { StatusTag } from "@/shared/tags";
import { GateChecklist } from "./GateChecklist";
import type { PackageView } from "./types";

/** 四级签批 (方案 §5.5.10): 研发 → EHS/法规 → 质量 → 发布管理员, e-signed over the package hash. */
export default function PackagePage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { packageId = "" } = useParams();
  const { me, can } = useSession();
  const sign = useSignature();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["plm", "package", packageId], queryFn: () => call<PackageView>("GET", `/api/plm/packages/${packageId}`) });
  const p = q.data;
  const current = p?.flow.steps.find((s) => s.current);
  const myTurn = !!current && !!me?.roles.includes(current.role) && !p!.flow.steps.some((s) => s.decided_by === me?.id);
  const last = current && p && current.seq === p.flow.steps.length;
  const run = async (path: string, body: unknown) => {
    try {
      await call("POST", path, body);
      message.success(t("common.done", "完成"));
      void qc.invalidateQueries({ queryKey: ["plm"] });
    } catch (e) {
      message.error(errorText(e));
    }
  };
  const decide = async (decision: "approve" | "reject") => {
    const s = await sign({
      title: decision === "reject" ? t("sign.reject", "驳回") : last ? t("sign.release", "最终发布") : t("sign.approve", "签批通过"),
      comment: decision === "reject" ? "required" : "optional",
      meaning: decision === "reject" ? t("sign.reject_meaning", "驳回后修订版退回候选状态，需修改后重新提交。")
        : last ? t("sign.release_meaning", "我批准发布此产品修订版；签名绑定发布包哈希 {{h}}。", { h: p!.package_hash.slice(0, 16) })
        : t("sign.approve_meaning", "我以“{{role}}”身份批准此发布包（哈希 {{h}}）。", { role: t(`role.${current!.role}`, current!.role), h: p!.package_hash.slice(0, 16) }),
    });
    if (s) await run(`/api/plm/packages/${packageId}/sign`, { decision, password: s.password, comment: s.comment });
  };
  const withdraw = async () => {
    const s = await sign({ title: t("sign.withdraw", "撤回发布"), comment: "required", commentLabel: t("sign.withdraw_reason", "撤回理由"),
      meaning: t("sign.withdraw_meaning", "撤回后该修订版的全部发布文档作废，并通知已配置的连接器。") });
    if (s) await run(`/api/plm/packages/${packageId}/withdraw`, { reason: s.comment, password: s.password });
  };
  if (q.error) return <PageContainer><ErrorAlert error={q.error} /></PageContainer>;
  return (
    <PageContainer loading={!p} title={t("plm.package", "发布包")} subTitle={p && <StatusTag status={p.status} />}
      extra={p && <Space>
        <Link to={`/plm/revisions/${p.revision_id}`}><Button>{t("plm.revision", "修订版")}</Button></Link>
        {p.has_delivery && <Button onClick={() => void download(`/api/plm/packages/${p.id}/delivery`, `release-${p.id.slice(0, 8)}.zip`)}>{t("plm.delivery", "下载交付包 ZIP")}</Button>}
        {p.status === "released" && can("release.publish") && <Button onClick={() => void run(`/api/plm/packages/${p.id}/publish`, {})}>{t("plm.republish", "重新推送连接器")}</Button>}
        {p.status === "released" && can("release.withdraw") && <Button danger onClick={() => void withdraw()}>{t("sign.withdraw", "撤回发布")}</Button>}
      </Space>}>
      {p && <>
        <ProCard bordered>
          <Steps current={p.flow.steps.findIndex((s) => s.current)} status={p.flow.status === "rejected" ? "error" : p.status === "released" ? "finish" : "process"}
            items={p.flow.steps.map((s) => ({ title: t(`role.${s.role}`, s.role), description: <span><StatusTag status={s.status} />{s.comment && <div>{s.comment}</div>}</span> }))} />
          {myTurn && p.status === "under_review" && (can("release.sign") || can("release.publish")) && (
            <Space style={{ marginTop: 16 }}>
              <Button type="primary" onClick={() => void decide("approve")}>{last ? t("sign.release", "最终发布") : t("sign.approve", "签批通过")}</Button>
              <Button danger onClick={() => void decide("reject")}>{t("sign.reject", "驳回")}</Button>
            </Space>
          )}
          {current && !myTurn && p.status === "under_review" && <Alert style={{ marginTop: 16 }} type="info" showIcon
            message={t("sign.waiting", "等待“{{role}}”签批", { role: t(`role.${current.role}`, current.role) })} />}
        </ProCard>
        <ProCard gutter={16} ghost style={{ marginTop: 16 }} wrap>
          <ProCard colSpan={{ xs: 24, md: 12 }} bordered title={t("sign.signatures", "电子签名")}
            extra={p.signatures.length > 0 && (p.signatures_valid ? <Tag color="green">{t("sign.valid", "签名校验通过")}</Tag> : <Tag color="red">{t("sign.invalid", "签名校验失败")}</Tag>)}>
            <Table size="small" rowKey="id" pagination={false} dataSource={p.signatures} columns={[
              { title: t("sign.step", "步骤"), dataIndex: "step" },
              { title: t("sign.signer", "签名人"), render: (_v, s) => s.display_name || s.username },
              { title: t("sign.meaning", "含义"), dataIndex: "meaning_zh" },
              { title: t("sign.time", "时间"), dataIndex: "signed_at", render: (v: string) => v.slice(0, 19).replace("T", " ") },
              { title: t("sign.hash", "签名哈希"), dataIndex: "signed_hash", render: (v: string) => <Typography.Text code>{v.slice(0, 12)}</Typography.Text> },
            ]} />
          </ProCard>
          <ProCard colSpan={{ xs: 24, md: 12 }} bordered title={t("plm.package_info", "发布包")}>
            <Descriptions size="small" column={1}>
              <Descriptions.Item label={t("plm.hash", "发布包哈希")}><Typography.Text code copyable>{p.package_hash}</Typography.Text></Descriptions.Item>
              <Descriptions.Item label={t("plm.documents", "文档")}>{p.document_ids.map((id) => <Link key={id} to={`/plm/documents/${id}`} style={{ marginRight: 8 }}>{id.slice(0, 8)}</Link>)}</Descriptions.Item>
              <Descriptions.Item label={t("plm.released_at", "发布时间")}>{p.released_at ?? "—"}</Descriptions.Item>
            </Descriptions>
            {p.publish_results.length > 0 && <List size="small" header={t("plm.publish_results", "连接器推送")} dataSource={p.publish_results}
              renderItem={(r) => <List.Item><Tag color={r.ok ? "green" : "red"}>{r.connector}</Tag>{r.action} · {r.detail} · {r.at.slice(0, 19)}</List.Item>} />}
          </ProCard>
        </ProCard>
        {p.gate && <ProCard bordered style={{ marginTop: 16 }} title={t("plm.gate_snapshot", "提交时的门禁结果")}>
          <GateChecklist gate={p.gate} revisionId={p.revision_id} onChange={() => undefined} readOnly />
        </ProCard>}
      </>}
    </PageContainer>
  );
}

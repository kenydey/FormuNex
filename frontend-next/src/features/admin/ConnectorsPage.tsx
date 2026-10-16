import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Descriptions, List, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";

interface PlmConnectors { configured: { name: string; target: string }[]; reserved: { name: string; description: string }[] }
interface ElnConnector { name: string; configured: boolean; can_push: boolean }

export default function ConnectorsPage() {
  const { t } = useTranslation();
  const plm = useQuery({ queryKey: ["plm", "connectors"], queryFn: () => call<PlmConnectors>("GET", "/api/plm/connectors") });
  const eln = useQuery({ queryKey: ["lab", "eln", "connectors"], queryFn: () => call<ElnConnector[]>("GET", "/api/lab/eln/connectors") });
  return (
    <PageContainer>
      <ProCard gutter={16} ghost wrap>
        <ProCard colSpan={{ xs: 24, md: 12 }} bordered title={t("connectors.publish", "发布连接器")}>
          <List
            header={<Typography.Text strong>{t("connectors.configured", "已配置")}</Typography.Text>}
            dataSource={plm.data?.configured ?? []}
            locale={{ emptyText: t("connectors.none", "未配置（发布时只生成交付包 ZIP）") }}
            renderItem={(c) => <List.Item><Tag color="green">{c.name}</Tag>{c.target}</List.Item>}
          />
          <List
            header={<Typography.Text strong>{t("connectors.reserved", "预留（本期不开发）")}</Typography.Text>}
            dataSource={plm.data?.reserved ?? []}
            renderItem={(c) => <List.Item><Tag>{c.name}</Tag>{c.description}</List.Item>}
          />
        </ProCard>
        <ProCard colSpan={{ xs: 24, md: 12 }} bordered title={t("connectors.eln", "外部 ELN")}>
          {(eln.data ?? []).map((c) => (
            <Descriptions key={c.name} size="small" column={1} bordered style={{ marginBottom: 12 }} title={c.name}>
              <Descriptions.Item label={t("connectors.state", "状态")}>{c.configured ? <Tag color="green">{t("connectors.ok", "已配置")}</Tag> : <Tag>{t("connectors.not_configured", "未配置")}</Tag>}</Descriptions.Item>
              <Descriptions.Item label={t("connectors.push", "推送任务单")}>{c.can_push ? t("common.yes", "是") : t("connectors.read_only", "只读")}</Descriptions.Item>
            </Descriptions>
          ))}
          <Typography.Paragraph type="secondary">
            {t("connectors.env_hint", "连接器在服务器环境变量中配置：FORMUNEX_PLM_PUBLISH_WEBHOOK_URL / _SECRET、FORMUNEX_LAB_ELABFTW_URL / _API_KEY。")}
          </Typography.Paragraph>
        </ProCard>
      </ProCard>
    </PageContainer>
  );
}

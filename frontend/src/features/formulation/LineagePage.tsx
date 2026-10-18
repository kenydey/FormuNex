import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Button, Space } from "antd";
import { ApartmentOutlined, EditOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { fx } from "./api";
import { ProvenanceDrawer } from "./Provenance";
import { VersionHistory } from "./VersionHistory";

/** One formulation lineage: revision list / graph and diffs (route lab/lineage/:lineageId). */
export default function LineagePage() {
  const { lineageId = "" } = useParams();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [prov, setProv] = useState(false);
  const lineage = useQuery({ queryKey: ["formulation", "lineage", lineageId], queryFn: () => fx.lineage(lineageId) });
  const latest = lineage.data?.versions[lineage.data.versions.length - 1];
  return (
    <PageContainer title={latest?.name ?? t("formulation.lineage.title", "配方谱系")} subTitle={lineageId.slice(0, 8)}
      extra={latest && <Space>
        <Button icon={<ApartmentOutlined />} onClick={() => setProv(true)}>{t("formulation.prov.open", "证据谱系")}</Button>
        <Button type="primary" icon={<EditOutlined />} onClick={() => navigate(`/lab/formulations?version=${latest.id}`)}>{t("formulation.lineage.edit_latest", "在编辑器打开最新版")}</Button>
      </Space>}>
      <ProCard bordered>
        <VersionHistory lineageId={lineageId} onOpenVersion={(id) => navigate(`/lab/formulations?version=${id}`)} />
      </ProCard>
      {latest && <ProvenanceDrawer open={prov} onClose={() => setProv(false)} nodeType="formulation" nodeId={latest.id} title={latest.name} />}
    </PageContainer>
  );
}

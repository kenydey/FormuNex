import { ProCard } from "@ant-design/pro-components";
import { App, Radio, Space, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useSession } from "@/app/session";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { fx } from "./api";

/** Recommendation mode: hybrid (KB + LLM) / llm_only / kb_only (legacy FormulationModeSelector). */
export function ModeSelector() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { can } = useSession();
  const qc = useQueryClient();
  const mode = useQuery({ queryKey: ["formulation", "mode"], queryFn: fx.mode });
  const set = useMutation({
    mutationFn: fx.setMode,
    onSuccess: () => { message.success(t("formulation.mode.updated", "已更新（下次请求生效）")); void qc.invalidateQueries({ queryKey: ["formulation", "mode"] }); },
    onError: (e) => message.error(t("formulation.mode.failed", "更新失败：{{e}}", { e: errorText(e) })),
  });
  return (
    <ProCard bordered title={t("formulation.mode.title", "配方推荐模式")} loading={mode.isLoading}>
      <Typography.Paragraph type="secondary">
        {t("formulation.mode.intro", "控制配方推荐的生成策略：叠加模式同时使用知识库证据和 LLM 合成。对应环境变量")} <code>FORMUNEX_FORMULATION_MODE</code>
      </Typography.Paragraph>
      <ErrorAlert error={mode.error} />
      <Radio.Group value={mode.data?.current} disabled={set.isPending || !can("admin.llm")} onChange={(e) => set.mutate(e.target.value as string)}>
        <Space direction="vertical">
          {(mode.data?.choices ?? []).map((c) => (
            <Radio key={c.value} value={c.value}>
              <Typography.Text strong>{c.label}</Typography.Text>
              <div><Typography.Text type="secondary">{c.desc}</Typography.Text></div>
            </Radio>
          ))}
        </Space>
      </Radio.Group>
    </ProCard>
  );
}

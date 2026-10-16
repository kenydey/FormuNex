import { App, Form, Input, Modal, Select } from "antd";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { errorText } from "@/shared/feedback";

interface Product { id: string; code: string; name_zh: string }
interface Lineage { lineage_id: string; versions: { id: string; version: number; name: string }[] }

/** 设为定型 (方案 §5.4): winning experiment + formulation version → product revision candidate. */
export function WinningModal({ experimentId, open, onClose }: { experimentId: number | null; open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const [form] = Form.useForm();
  const products = useQuery({ queryKey: ["plm", "products"], enabled: open, queryFn: () => call<Product[]>("GET", "/api/plm/products") });
  const lineages = useQuery({ queryKey: ["formulations", "lineages", ""], enabled: open, queryFn: () => call<Lineage[]>("GET", "/api/formulations/versions?limit=50") });
  const win = useMutation({
    mutationFn: (v: { product_id: string; formulation_version_id: string; note: string }) =>
      call<{ revision_id: string; rev: string; warnings: string[] }>("POST", "/api/cockpit/winning", { ...v, experiment_id: experimentId }),
    onSuccess: (o) => {
      o.warnings.forEach((w) => message.warning(w));
      message.success(t("winning.done", "已生成修订版 {{rev}}（候选）", { rev: o.rev }));
      onClose();
      navigate(`/plm/revisions/${o.revision_id}`);
    },
    onError: (e) => message.error(errorText(e)),
  });
  return (
    <Modal open={open} title={t("winning.title", "设为定型配方")} onCancel={onClose} confirmLoading={win.isPending} destroyOnHidden
      onOk={async () => win.mutate(await form.validateFields())}>
      <Form form={form} layout="vertical">
        <Form.Item label={t("winning.experiment", "定型实验")}>#{experimentId}</Form.Item>
        <Form.Item name="product_id" label={t("winning.product", "产品")} rules={[{ required: true }]}>
          <Select showSearch optionFilterProp="label" options={(products.data ?? []).map((p) => ({ value: p.id, label: `${p.code} ${p.name_zh}` }))} />
        </Form.Item>
        <Form.Item name="formulation_version_id" label={t("winning.version", "配方版本")} rules={[{ required: true }]}>
          <Select showSearch optionFilterProp="label" options={(lineages.data ?? []).flatMap((l) => l.versions.map((v) => ({ value: v.id, label: `${v.name} v${v.version}` })))} />
        </Form.Item>
        <Form.Item name="note" label={t("winning.note", "说明")}><Input.TextArea rows={2} /></Form.Item>
      </Form>
    </Modal>
  );
}

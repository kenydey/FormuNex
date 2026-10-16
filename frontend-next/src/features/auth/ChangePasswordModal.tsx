import { Form, Input, Modal, message } from "antd";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { errorText } from "@/shared/feedback";

export function ChangePasswordModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const [form] = Form.useForm();
  return (
    <Modal open={open} title={t("shell.change_password", "修改密码")} onCancel={onClose} destroyOnClose
      onOk={async () => {
        const v = await form.validateFields();
        try {
          await call("POST", "/api/identity/password", { old_password: v.old_password, new_password: v.new_password });
          message.success(t("password.changed", "密码已修改，其他会话已全部退出"));
          onClose();
        } catch (e) {
          message.error(errorText(e));
        }
      }}>
      <Form form={form} layout="vertical">
        <Form.Item name="old_password" label={t("password.old", "当前密码")} rules={[{ required: true }]}>
          <Input.Password autoComplete="current-password" />
        </Form.Item>
        <Form.Item name="new_password" label={t("password.new", "新密码")} extra={t("password.policy", "至少 10 位，含大写、小写、数字、符号中的三类")}
          rules={[{ required: true, min: 10 }]}>
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Form.Item name="confirm" label={t("password.confirm", "确认新密码")} dependencies={["new_password"]}
          rules={[{ required: true }, ({ getFieldValue }) => ({
            validator: (_r, v) => (v === getFieldValue("new_password") ? Promise.resolve() : Promise.reject(new Error(t("password.mismatch", "两次输入不一致")))),
          })]}>
          <Input.Password autoComplete="new-password" />
        </Form.Item>
      </Form>
    </Modal>
  );
}

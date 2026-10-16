import { Alert, Form, Input, Modal } from "antd";
import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

export interface SignRequest {
  title: string;
  /** What signing means, shown above the password field (方案 §4.4: signature meaning). */
  meaning: string;
  /** Ask for a comment / reason; ``required`` makes it mandatory (rejections, waivers, withdrawals). */
  comment?: "optional" | "required";
  commentLabel?: string;
}

export interface SignResult {
  password: string;
  comment: string;
}

type Opener = (req: SignRequest) => Promise<SignResult | null>;
const Ctx = createContext<Opener | null>(null);

/** Electronic signature: re-enter the password, see what the signature means, optionally give a reason. */
export function SignatureProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const [req, setReq] = useState<SignRequest | null>(null);
  const resolver = useRef<((r: SignResult | null) => void) | null>(null);
  const [form] = Form.useForm<SignResult>();

  const open = useCallback<Opener>((r) => {
    form.resetFields();
    setReq(r);
    return new Promise((resolve) => {
      resolver.current = resolve;
    });
  }, [form]);

  const close = (result: SignResult | null) => {
    resolver.current?.(result);
    resolver.current = null;
    setReq(null);
  };

  return (
    <Ctx.Provider value={open}>
      {children}
      <Modal
        open={!!req}
        title={req?.title}
        okText={t("sign.ok", "签名确认")}
        onCancel={() => close(null)}
        onOk={async () => {
          const v = await form.validateFields();
          close({ password: v.password, comment: v.comment ?? "" });
        }}
        destroyOnHidden
      >
        <Alert type="info" showIcon style={{ marginBottom: 16 }} message={req?.meaning} />
        <Form form={form} layout="vertical">
          {req?.comment && (
            <Form.Item name="comment" label={req.commentLabel ?? t("sign.comment", "意见")}
              rules={req.comment === "required" ? [{ required: true, whitespace: true }] : []}>
              <Input.TextArea rows={3} />
            </Form.Item>
          )}
          <Form.Item name="password" label={t("sign.password", "登录密码（电子签名身份验证）")}
            rules={[{ required: true }]}>
            <Input.Password autoComplete="current-password" autoFocus />
          </Form.Item>
        </Form>
      </Modal>
    </Ctx.Provider>
  );
}

export function useSignature(): Opener {
  const open = useContext(Ctx);
  if (!open) throw new Error("useSignature outside SignatureProvider");
  return open;
}

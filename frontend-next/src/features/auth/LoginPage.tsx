import { LockOutlined, UserOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Form, Input, Typography } from "antd";
import { useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useSession } from "@/app/session";
import { errorText } from "@/shared/feedback";
import { useDesktop } from "@/desktop/runtime";

export default function LoginPage() {
  const { t } = useTranslation();
  const { me, login } = useSession();
  const navigate = useNavigate();
  const location = useLocation();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const state = location.state as { from?: string; username?: string } | null;
  const target = state?.from || "/dashboard";
  const desktopInfo = useDesktop((s) => s.info);
  if (me) return <Navigate to={target} replace />;

  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", background: "linear-gradient(135deg,#0f3550,#1f5f8b)" }}>
      <Card style={{ width: 380 }}>
        <div style={{ textAlign: "center", marginBottom: 24 }}>
          <img src="/favicon.svg" width={48} height={48} alt="" />
          <Typography.Title level={3} style={{ margin: "8px 0 0" }}>{t("app.title", "方枢 FormuNex")}</Typography.Title>
          <Typography.Text type="secondary">{t("login.subtitle", "配方研发与化学品合规平台")}</Typography.Text>
        </div>
        {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
        <Form
          layout="vertical"
          initialValues={{ username: state?.username }}
          onFinish={async (v: { username: string; password: string }) => {
            setBusy(true);
            setError(null);
            try {
              await login(v.username, v.password);
              navigate(target, { replace: true });
            } catch (e) {
              setError(errorText(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          <Form.Item name="username" rules={[{ required: true, message: t("login.username_required", "请输入用户名") }]}>
            <Input prefix={<UserOutlined />} placeholder={t("login.username", "用户名")} autoComplete="username" autoFocus />
          </Form.Item>
          <Form.Item name="password" rules={[{ required: true, message: t("login.password_required", "请输入密码") }]}>
            <Input.Password prefix={<LockOutlined />} placeholder={t("login.password", "密码")} autoComplete="current-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={busy}>{t("login.submit", "登录")}</Button>
        </Form>
        <Typography.Paragraph type="secondary" style={{ marginTop: 16, fontSize: 12 }}>
          {t("login.first_admin", "首次部署请在服务器执行 backend/scripts/create_user.py 创建管理员账号。")}
        </Typography.Paragraph>
        {desktopInfo?.api_base && (
          <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0, textAlign: "center", fontSize: 12 }}>
            {t("login.server", "服务器：{{s}}", { s: desktopInfo.api_base })} · <Link to="/setup">{t("login.change_server", "更改连接")}</Link>
          </Typography.Paragraph>
        )}
      </Card>
    </div>
  );
}

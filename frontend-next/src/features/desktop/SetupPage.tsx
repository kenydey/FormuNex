import { CheckCircleOutlined, CloudServerOutlined, DesktopOutlined, ReloadOutlined } from "@ant-design/icons";
import { Alert, App, Button, Card, Col, Descriptions, Form, Input, Result, Row, Space, Steps, Table, Tag, Typography } from "antd";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { desktop, onStackLog, type DesktopConfig, type DockerStatus, type StackStatus } from "@/desktop/bridge";
import { applyDesktopInfo, useDesktop } from "@/desktop/runtime";
import { errorText } from "@/shared/feedback";
import { DOCKER_DOWNLOAD, MIN_MEMORY_GB, formatBytes, memoryWarning, stackReady } from "./logic";
import { LogView } from "./LogView";

type Step = "mode" | "remote" | "docker" | "images" | "start" | "admin" | "done";

/** First-run wizard (方案 §2.2 / §3.5): local Docker stack or a FormuNex server. */
export default function SetupPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const info = useDesktop((s) => s.info);
  const [step, setStep] = useState<Step>("mode");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const save = async (patch: Partial<DesktopConfig>) => {
    if (!info) throw new Error("desktop not initialised");
    const config = await desktop.saveConfig({ ...info.config, ...patch });
    const fresh = await desktop.info();
    applyDesktopInfo(fresh);
    return config;
  };

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  if (!info) {
    return <Result status="info" title={t("desktop.browser_only", "该页面仅在桌面版中可用")} />;
  }

  const localSteps: Step[] = ["mode", "docker", "images", "start", "admin", "done"];
  const current = step === "remote" ? 1 : Math.max(0, localSteps.indexOf(step));
  const items = step === "remote" || info.config.mode === "remote"
    ? [{ title: t("desktop.step.mode", "连接方式") }, { title: t("desktop.step.server", "服务器") }]
    : [t("desktop.step.mode", "连接方式"), t("desktop.step.docker", "Docker"), t("desktop.step.images", "镜像"),
       t("desktop.step.start", "启动"), t("desktop.step.admin", "管理员"), t("desktop.step.done", "完成")].map((title) => ({ title }));

  return (
    <div style={{ minHeight: "100vh", padding: "32px 16px", display: "flex", justifyContent: "center" }}>
      <Card style={{ width: "100%", maxWidth: 920 }} title={<Space><img src="/favicon.svg" width={24} alt="" />{t("desktop.setup", "方枢 FormuNex 首次设置")}</Space>}
        extra={<Typography.Text type="secondary">v{info.app_version}</Typography.Text>}>
        <Steps size="small" current={current} items={items} style={{ marginBottom: 24 }} />
        {error ? <Alert type="error" showIcon message={errorText(error)} style={{ marginBottom: 16 }} closable onClose={() => setError(null)} /> : null}
        {step === "mode" && <ModeStep onLocal={() => run(async () => { await save({ mode: "local" }); setStep("docker"); })}
          onRemote={() => setStep("remote")} busy={busy} />}
        {step === "remote" && <RemoteStep initial={info.config.remote_url} busy={busy} onBack={() => setStep("mode")}
          onSave={(url) => run(async () => {
            const h = await desktop.probeHealth(url);
            if (!h.reachable) throw new Error(t("desktop.remote_unreachable", "无法连接服务器：{{e}}", { e: h.error ?? h.status }));
            await save({ mode: "remote", remote_url: url, setup_complete: true });
            navigate("/login", { replace: true });
          })} />}
        {step === "docker" && <DockerStep onNext={() => setStep("images")} onBack={() => setStep("mode")} />}
        {step === "images" && <ImagesStep onNext={() => setStep("start")} onBack={() => setStep("docker")} />}
        {step === "start" && <StartStep onNext={() => setStep("admin")} onBack={() => setStep("images")} />}
        {step === "admin" && <AdminStep onDone={(username) => run(async () => {
          await save({ setup_complete: true });
          navigate("/login", { replace: true, state: { username } });
        })} />}
      </Card>
    </div>
  );
}

function ModeStep({ onLocal, onRemote, busy }: { onLocal: () => void; onRemote: () => void; busy: boolean }) {
  const { t } = useTranslation();
  return (
    <Row gutter={16}>
      <Col xs={24} md={12}>
        <Card hoverable onClick={busy ? undefined : onLocal} style={{ height: "100%" }}>
          <Space direction="vertical">
            <DesktopOutlined style={{ fontSize: 32 }} />
            <Typography.Title level={5} style={{ margin: 0 }}>{t("desktop.mode.local", "本机单机版")}</Typography.Title>
            <Typography.Text type="secondary">{t("desktop.mode.local_desc", "用 Docker Desktop 在本机运行完整组件（PostgreSQL、Redis、Neo4j、API、任务队列），数据只存在本机。建议 12 GB 以上内存。")}</Typography.Text>
            <Button type="primary" loading={busy}>{t("desktop.mode.choose", "选择")}</Button>
          </Space>
        </Card>
      </Col>
      <Col xs={24} md={12}>
        <Card hoverable onClick={onRemote} style={{ height: "100%" }}>
          <Space direction="vertical">
            <CloudServerOutlined style={{ fontSize: 32 }} />
            <Typography.Title level={5} style={{ margin: 0 }}>{t("desktop.mode.remote", "连接服务器")}</Typography.Title>
            <Typography.Text type="secondary">{t("desktop.mode.remote_desc", "连接团队部署的 FormuNex 服务器（多人协作、审批流、集中审计），本机不运行任何容器。")}</Typography.Text>
            <Button>{t("desktop.mode.choose", "选择")}</Button>
          </Space>
        </Card>
      </Col>
    </Row>
  );
}

function RemoteStep({ initial, busy, onSave, onBack }: { initial: string; busy: boolean; onSave: (url: string) => void; onBack: () => void }) {
  const { t } = useTranslation();
  return (
    <Form layout="vertical" initialValues={{ url: initial }} onFinish={(v: { url: string }) => onSave(v.url.trim())} style={{ maxWidth: 560 }}>
      <Form.Item name="url" label={t("desktop.remote_url", "服务器地址")} extra={t("desktop.remote_hint", "须为 https（证书按系统信任库校验，企业自签 CA 请先安装到系统）；仅本机地址可用 http。")}
        rules={[{ required: true, message: t("desktop.remote_required", "请输入服务器地址") }]}>
        <Input placeholder="https://formunex.example.com" autoFocus />
      </Form.Item>
      <Space>
        <Button onClick={onBack}>{t("desktop.back", "上一步")}</Button>
        <Button type="primary" htmlType="submit" loading={busy}>{t("desktop.connect", "测试并连接")}</Button>
      </Space>
    </Form>
  );
}

function DockerStep({ onNext, onBack }: { onNext: () => void; onBack: () => void }) {
  const { t } = useTranslation();
  const [st, setSt] = useState<DockerStatus | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const check = async () => {
    setLoading(true);
    setError(null);
    try {
      setSt(await desktop.dockerStatus());
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => void check(), []);
  const ok = !!st?.installed && !!st.server_version && !!st.compose_version;
  const memWarn = memoryWarning(st?.mem_total_bytes ?? null);
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      {error ? <Alert type="error" showIcon message={errorText(error)} /> : null}
      {st && !st.installed && (
        <Alert type="warning" showIcon message={t("desktop.docker_missing", "未检测到 Docker Desktop")}
          description={t("desktop.docker_missing_desc", "本机单机版需要 Docker Desktop（Windows 需启用 WSL 2）。安装并启动后点“重新检测”。注意：Docker Desktop 对大型企业需付费订阅。")}
          action={<Button onClick={() => void openUrl(DOCKER_DOWNLOAD)}>{t("desktop.download_docker", "下载 Docker Desktop")}</Button>} />
      )}
      {st?.installed && (
        <Descriptions bordered size="small" column={1} items={[
          { key: "p", label: t("desktop.docker_path", "docker 路径"), children: st.path ?? "—" },
          { key: "c", label: t("desktop.docker_client", "客户端"), children: st.client_version ?? "—" },
          { key: "s", label: t("desktop.docker_engine", "引擎"), children: st.server_version ? <Tag color="success">{st.server_version}</Tag> : <Tag color="error">{t("desktop.not_running", "未运行")}</Tag> },
          { key: "o", label: "docker compose", children: st.compose_version ?? <Tag color="error">{t("desktop.missing", "缺失")}</Tag> },
          { key: "m", label: t("desktop.resources", "可用资源"), children: st.mem_total_bytes ? `${formatBytes(st.mem_total_bytes)} · ${st.cpus ?? "?"} CPU` : "—" },
        ]} />
      )}
      {st?.message && <Alert type="warning" showIcon message={st.message} />}
      {memWarn && <Alert type="warning" showIcon message={t("desktop.mem_warn", "Docker 可用内存 {{gb}} GB，低于建议的 {{min}} GB：可在 Docker Desktop 设置 → Resources 中调高。", { gb: memWarn, min: MIN_MEMORY_GB })} />}
      <Space>
        <Button onClick={onBack}>{t("desktop.back", "上一步")}</Button>
        <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void check()}>{t("desktop.recheck", "重新检测")}</Button>
        <Button type="primary" disabled={!ok} onClick={onNext}>{t("desktop.next", "下一步")}</Button>
      </Space>
    </Space>
  );
}

async function openUrl(url: string) {
  const { openUrl: open } = await import("@tauri-apps/plugin-opener");
  await open(url);
}

/** Run a streamed stack command, collecting its output lines. */
function useStreamed() {
  const [lines, setLines] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    setLines([]);
    const off = await onStackLog((l) => setLines((prev) => [...prev.slice(-400), l]));
    try {
      await fn();
      return true;
    } catch (e) {
      setError(e);
      return false;
    } finally {
      off();
      setBusy(false);
    }
  };
  return { lines, busy, error, run };
}

function ImagesStep({ onNext, onBack }: { onNext: () => void; onBack: () => void }) {
  const { t } = useTranslation();
  const s = useStreamed();
  const [done, setDone] = useState(false);
  const importTar = async () => {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const path = await open({ multiple: false, filters: [{ name: "Docker images", extensions: ["tar", "gz", "tgz"] }] });
    if (typeof path === "string" && (await s.run(() => desktop.importImages(path)))) setDone(true);
  };
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Typography.Paragraph type="secondary">
        {t("desktop.images_desc", "联网环境直接拉取镜像；内网 / 离线环境请导入随安装包提供的离线镜像包 formunex-images-<版本>.tar.gz。")}
      </Typography.Paragraph>
      <Space wrap>
        <Button type="primary" loading={s.busy} onClick={() => void s.run(() => desktop.stackPull()).then((ok) => ok && setDone(true))}>
          {t("desktop.pull", "联网拉取镜像")}</Button>
        <Button loading={s.busy} onClick={() => void importTar()}>{t("desktop.import", "导入离线镜像包…")}</Button>
      </Space>
      {s.error ? <Alert type="error" showIcon message={errorText(s.error)} /> : null}
      {done && <Alert type="success" showIcon message={t("desktop.images_ready", "镜像已就绪")} />}
      <LogView lines={s.lines} />
      <Space>
        <Button onClick={onBack}>{t("desktop.back", "上一步")}</Button>
        <Button type={done ? "primary" : "default"} onClick={onNext} disabled={s.busy}>
          {done ? t("desktop.next", "下一步") : t("desktop.skip_images", "跳过（镜像已在本机）")}</Button>
      </Space>
    </Space>
  );
}

function StartStep({ onNext, onBack }: { onNext: () => void; onBack: () => void }) {
  const { t } = useTranslation();
  const s = useStreamed();
  const [status, setStatus] = useState<StackStatus | null>(null);
  const [started, setStarted] = useState(false);
  const timer = useRef<number | null>(null);
  const poll = async () => {
    try {
      setStatus(await desktop.stackStatus());
    } catch {
      // keep polling
    }
  };
  useEffect(() => {
    if (!started) return;
    void poll();
    timer.current = window.setInterval(() => void poll(), 3000);
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, [started]);
  const ready = stackReady(status);
  useEffect(() => { if (ready && timer.current) window.clearInterval(timer.current); }, [ready]);
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Typography.Paragraph type="secondary">
        {t("desktop.start_desc", "首次启动会初始化数据库（自动执行迁移），通常需要 1–3 分钟。API 只监听 127.0.0.1。")}
      </Typography.Paragraph>
      <Button type="primary" loading={s.busy} disabled={ready} onClick={() => { setStarted(true); void s.run(() => desktop.stackUp()); }}>
        {t("desktop.start_stack", "启动本机服务")}</Button>
      {s.error ? <Alert type="error" showIcon message={errorText(s.error)} /> : null}
      {status && <ServicesTable status={status} />}
      {ready && <Alert type="success" showIcon icon={<CheckCircleOutlined />} message={t("desktop.stack_ready", "服务已就绪")} />}
      <LogView lines={s.lines} />
      <Space>
        <Button onClick={onBack} disabled={s.busy}>{t("desktop.back", "上一步")}</Button>
        <Button type="primary" disabled={!ready} onClick={onNext}>{t("desktop.next", "下一步")}</Button>
      </Space>
    </Space>
  );
}

export function ServicesTable({ status }: { status: StackStatus }) {
  const { t } = useTranslation();
  return (
    <>
      <Space style={{ marginBottom: 8 }}>
        <Typography.Text>{t("desktop.api_health", "API 健康检查")}：</Typography.Text>
        {status.health.reachable
          ? <Tag color={status.health.status === "ok" ? "success" : "warning"}>{status.health.status}</Tag>
          : <Tag>{status.health.error ?? status.health.status}</Tag>}
      </Space>
      <Table size="small" pagination={false} rowKey="service" dataSource={status.services}
        locale={{ emptyText: t("desktop.no_services", "尚无容器") }}
        columns={[
          { title: t("desktop.service", "服务"), dataIndex: "service" },
          { title: t("desktop.state", "状态"), dataIndex: "state", render: (v: string) => <Tag color={v === "running" ? "success" : v === "exited" ? "default" : "processing"}>{v}</Tag> },
          { title: t("desktop.health", "健康"), dataIndex: "health", render: (v: string) => (v ? <Tag color={v === "healthy" ? "success" : v === "unhealthy" ? "error" : "processing"}>{v}</Tag> : "—") },
          { title: t("desktop.detail", "详情"), dataIndex: "status" },
        ]} />
    </>
  );
}

function AdminStep({ onDone }: { onDone: (username: string) => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [password, setPassword] = useState<string | null>(null);
  const [username, setUsername] = useState("admin");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  if (password) {
    return (
      <Space direction="vertical" style={{ width: "100%" }}>
        <Alert type="success" showIcon message={t("desktop.admin_created", "管理员 {{u}} 已创建", { u: username })}
          description={t("desktop.admin_pw_note", "初始密码只显示这一次，同时已保存到系统钥匙串（条目 com.formunex.desktop / admin-password）。登录后请在右上角修改密码。")} />
        <Typography.Paragraph copyable={{ text: password, onCopy: () => message.success(t("desktop.copied", "已复制")) }} code style={{ fontSize: 18 }}>
          {password}</Typography.Paragraph>
        <Button type="primary" onClick={() => onDone(username)}>{t("desktop.go_login", "完成，去登录")}</Button>
      </Space>
    );
  }
  return (
    <Form layout="vertical" style={{ maxWidth: 420 }} initialValues={{ username: "admin", display: "管理员" }}
      onFinish={async (v: { username: string; display: string }) => {
        setBusy(true);
        setError(null);
        try {
          setUsername(v.username);
          setPassword(await desktop.createAdmin(v.username, v.display));
        } catch (e) {
          setError(e);
        } finally {
          setBusy(false);
        }
      }}>
      {error ? <Alert type="error" showIcon message={errorText(error)} style={{ marginBottom: 12 }}
        description={t("desktop.admin_exists_hint", "若管理员已存在（重新安装），可直接去登录。")}
        action={<Button size="small" onClick={() => onDone(username)}>{t("desktop.go_login", "完成，去登录")}</Button>} /> : null}
      <Form.Item name="username" label={t("desktop.admin_user", "管理员用户名")} rules={[{ required: true, pattern: /^[A-Za-z0-9_.-]+$/, message: t("desktop.admin_user_rule", "只能包含字母、数字、_ - .") }]}>
        <Input />
      </Form.Item>
      <Form.Item name="display" label={t("desktop.admin_display", "显示名称")}><Input /></Form.Item>
      <Button type="primary" htmlType="submit" loading={busy}>{t("desktop.create_admin", "创建管理员（自动生成密码）")}</Button>
    </Form>
  );
}

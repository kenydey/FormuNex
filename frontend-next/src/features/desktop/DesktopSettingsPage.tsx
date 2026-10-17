import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Alert, App, Button, Descriptions, Input, Result, Select, Space, Table, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { desktop, onStackLog } from "@/desktop/bridge";
import { applyDesktopInfo, useDesktop } from "@/desktop/runtime";
import { ErrorAlert, errorText } from "@/shared/feedback";
import { formatBytes } from "./logic";
import { LogView } from "./LogView";
import { ServicesTable } from "./SetupPage";

async function reveal(path: string) {
  const { revealItemInDir } = await import("@tauri-apps/plugin-opener");
  await revealItemInDir(path);
}

/** Desktop shell settings (方案 §3.5): connection, local stack control, logs, backups, upgrades, shell updates. */
export default function DesktopSettingsPage() {
  const { t } = useTranslation();
  const info = useDesktop((s) => s.info);
  const navigate = useNavigate();
  if (!info) return <PageContainer><Result status="info" title={t("desktop.browser_only", "该页面仅在桌面版中可用")} /></PageContainer>;
  const local = info.config.mode === "local";
  return (
    <PageContainer subTitle={t("desktop.settings_sub", "连接方式、本机服务、备份与更新")}>
      <ProCard bordered title={t("desktop.connection", "连接")} extra={<Button onClick={() => navigate("/setup")}>{t("desktop.change_connection", "更改连接方式")}</Button>}>
        <Descriptions size="small" column={1} items={[
          { key: "m", label: t("desktop.mode", "方式"), children: <Tag color={local ? "blue" : "purple"}>{local ? t("desktop.mode.local", "本机单机版") : t("desktop.mode.remote", "连接服务器")}</Tag> },
          { key: "b", label: t("desktop.api_base", "API 地址"), children: info.api_base ?? "—" },
          { key: "v", label: t("desktop.app_version", "桌面版本"), children: info.app_version },
          { key: "d", label: t("desktop.data_dir", "数据目录"), children: <Space>{info.data_dir}<Button size="small" type="link" onClick={() => void reveal(info.data_dir)}>{t("desktop.open_dir", "打开")}</Button></Space> },
        ]} />
      </ProCard>
      {local && <StackCard />}
      {local && <BackupCard />}
      {local && <UpgradeCard current={info.config.version} />}
      <ShellUpdateCard />
    </PageContainer>
  );
}

function useStreamed() {
  const [lines, setLines] = useState<string[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setError(null);
    setLines([]);
    const off = await onStackLog((l) => setLines((p) => [...p.slice(-400), l]));
    try {
      await fn();
    } catch (e) {
      setError(e);
    } finally {
      off();
      setBusy(null);
    }
  };
  return { lines, busy, error, run };
}

function StackCard() {
  const { t } = useTranslation();
  const { modal } = App.useApp();
  const status = useQuery({ queryKey: ["desktop", "stack"], queryFn: desktop.stackStatus, refetchInterval: 5000 });
  const s = useStreamed();
  const [service, setService] = useState<string | undefined>("api");
  const logs = useQuery({ queryKey: ["desktop", "logs", service], queryFn: () => desktop.stackLogs(service, 300), enabled: false });
  const services = status.data?.services.map((x) => x.service) ?? [];
  const after = () => void status.refetch();
  return (
    <ProCard bordered style={{ marginTop: 16 }} title={t("desktop.local_stack", "本机服务")}
      extra={<Space wrap>
        <Button loading={s.busy === "up"} disabled={!!s.busy} onClick={() => void s.run("up", desktop.stackUp).then(after)}>{t("desktop.start", "启动")}</Button>
        <Button loading={s.busy === "restart"} disabled={!!s.busy} onClick={() => void s.run("restart", desktop.stackRestart).then(after)}>{t("desktop.restart", "重启")}</Button>
        <Button danger disabled={!!s.busy} onClick={() => modal.confirm({
          title: t("desktop.stop_confirm", "停止本机服务？"), content: t("desktop.stop_note", "数据保留在 Docker 卷中，下次启动继续使用。"),
          onOk: () => s.run("down", desktop.stackDown).then(after),
        })}>{t("desktop.stop", "停止")}</Button>
      </Space>}>
      <ErrorAlert error={status.error} />
      {s.error ? <Alert type="error" showIcon message={errorText(s.error)} style={{ marginBottom: 8 }} /> : null}
      {status.data && <ServicesTable status={status.data} />}
      <LogView lines={s.lines} />
      <Space style={{ marginTop: 16 }} wrap>
        <Typography.Text strong>{t("desktop.logs", "日志")}</Typography.Text>
        <Select allowClear style={{ width: 160 }} value={service} onChange={setService} placeholder={t("desktop.all_services", "全部服务")}
          options={services.map((v) => ({ value: v, label: v }))} />
        <Button loading={logs.isFetching} onClick={() => void logs.refetch()}>{t("desktop.show_logs", "查看最近 300 行")}</Button>
      </Space>
      <ErrorAlert error={logs.error} />
      {logs.data !== undefined && <LogView lines={logs.data.split("\n")} height={320} />}
    </ProCard>
  );
}

function BackupCard() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const backups = useQuery({ queryKey: ["desktop", "backups"], queryFn: desktop.listBackups });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  return (
    <ProCard bordered style={{ marginTop: 16 }} title={t("desktop.backups", "数据库备份")}
      subTitle={t("desktop.backups_sub", "pg_dump 自定义格式；每次升级前自动备份，保留最近 10 份")}
      extra={<Button loading={busy} onClick={async () => {
        setBusy(true);
        setError(null);
        try {
          const b = await desktop.backupNow();
          message.success(t("desktop.backup_done", "已备份：{{n}}", { n: b.name }));
          void backups.refetch();
        } catch (e) {
          setError(e);
        } finally {
          setBusy(false);
        }
      }}>{t("desktop.backup_now", "立即备份")}</Button>}>
      {error ? <Alert type="error" showIcon message={errorText(error)} style={{ marginBottom: 8 }} /> : null}
      <Table size="small" rowKey="name" pagination={false} dataSource={backups.data} loading={backups.isLoading}
        locale={{ emptyText: t("desktop.no_backups", "暂无备份") }}
        columns={[
          { title: t("desktop.file", "文件"), dataIndex: "name" },
          { title: t("desktop.size", "大小"), dataIndex: "bytes", render: (v: number) => formatBytes(v), width: 120 },
          { title: "", width: 90, render: (_v, b) => <Button size="small" type="link" onClick={() => void reveal(b.path)}>{t("desktop.open_dir", "打开")}</Button> },
        ]} />
      <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
        {t("desktop.restore_hint", "恢复：停止服务后执行 docker compose -p formunex exec -T postgres pg_restore --clean -U formunex -d formunex < 备份文件（详见 docs/desktop.md）。")}
      </Typography.Paragraph>
    </ProCard>
  );
}

function UpgradeCard({ current }: { current: string }) {
  const { t } = useTranslation();
  const { modal } = App.useApp();
  const [version, setVersion] = useState("");
  const s = useStreamed();
  return (
    <ProCard bordered style={{ marginTop: 16 }} title={t("desktop.upgrade", "升级服务镜像")}
      subTitle={t("desktop.upgrade_sub", "先自动 pg_dump 备份，再拉取新镜像并重建容器（数据库迁移随 API 启动自动执行）")}>
      <Space wrap>
        <Typography.Text>{t("desktop.current_version", "当前镜像版本")}：<Tag>{current}</Tag></Typography.Text>
        <Input style={{ width: 180 }} placeholder="0.3.0" value={version} onChange={(e) => setVersion(e.target.value.trim())} />
        <Button type="primary" disabled={!version || version === current || !!s.busy} loading={!!s.busy} onClick={() => modal.confirm({
          title: t("desktop.upgrade_confirm", "升级到 {{v}}？", { v: version }),
          content: t("desktop.upgrade_note", "升级期间服务会短暂不可用。"),
          onOk: () => s.run("upgrade", async () => {
            await desktop.upgrade(version);
            applyDesktopInfo(await desktop.info());
          }),
        })}>{t("desktop.upgrade_go", "备份并升级")}</Button>
      </Space>
      {s.error ? <Alert type="error" showIcon message={errorText(s.error)} style={{ marginTop: 8 }} /> : null}
      <div style={{ marginTop: 8 }}><LogView lines={s.lines} /></div>
    </ProCard>
  );
}

function ShellUpdateCard() {
  const { t } = useTranslation();
  const { modal, message } = App.useApp();
  const [state, setState] = useState<{ checking?: boolean; note?: string; error?: unknown }>({});
  const check = async () => {
    setState({ checking: true });
    try {
      const { check: checkUpdate } = await import("@tauri-apps/plugin-updater");
      const update = await checkUpdate();
      if (!update) {
        setState({ note: t("desktop.up_to_date", "已是最新版本") });
        return;
      }
      setState({ note: t("desktop.update_found", "发现新版本 {{v}}", { v: update.version }) });
      modal.confirm({
        title: t("desktop.update_install", "安装 {{v}} 并重启？", { v: update.version }),
        content: update.body,
        onOk: async () => {
          await update.downloadAndInstall();
          message.success(t("desktop.update_restart", "更新已安装，正在重启"));
          const { relaunch } = await import("@tauri-apps/plugin-process");
          await relaunch();
        },
      });
    } catch (e) {
      const text = errorText(e);
      setState(/not (allowed|found|initialized)|plugin/i.test(text)
        ? { note: t("desktop.updater_off", "本构建未配置更新源（需签名公钥），请从发布页下载新版本") }
        : { error: e });
    }
  };
  return (
    <ProCard bordered style={{ marginTop: 16 }} title={t("desktop.shell_update", "桌面程序更新")}
      extra={<Button loading={state.checking} onClick={() => void check()}>{t("desktop.check_update", "检查更新")}</Button>}>
      {state.note && <Alert type="info" showIcon message={state.note} />}
      {state.error ? <Alert type="error" showIcon message={errorText(state.error)} /> : null}
      {!state.note && !state.error && <Typography.Text type="secondary">{t("desktop.update_hint", "外壳更新经签名校验；服务镜像版本在上方单独升级。")}</Typography.Text>}
    </ProCard>
  );
}

/**
 * 结构绘制器弹窗：Ketcher 画布 + SMILES 文本 fallback，双向同步。
 *
 * - 画布 → 文本：点“读取画布”把当前画布结构写回文本框。
 * - 文本 → 画布：编辑文本后点“载入到画布”，非法 SMILES 只提示、不崩溃、画布保持原样。
 * - 确认时读取画布的 SMILES 与 SMARTS（Ketcher 客户端计算，无后端参与），
 *   交给调用方决定用途（骨架替代 / 子结构检索 / 填入表单）。
 */
import { lazy, Suspense, useRef, useState } from "react";
import { Button, Input, Modal, Space, Spin, Typography } from "antd";
import { useTranslation } from "react-i18next";
import type { Ketcher } from "ketcher-core";

const KetcherCanvas = lazy(() =>
  import("./KetcherCanvas").then((m) => ({ default: m.KetcherCanvas })),
);

export interface DrawnStructure {
  smiles: string;
  smarts: string;
}

export interface EditorAction {
  key: string;
  label: string;
  primary?: boolean;
}

export function StructureEditorModal({
  open,
  initialSmiles,
  title,
  actions,
  onApply,
  onClose,
}: {
  open: boolean;
  initialSmiles?: string;
  title?: string;
  actions: EditorAction[];
  onApply: (structure: DrawnStructure, actionKey: string) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const ketcherRef = useRef<Ketcher | null>(null);
  const [text, setText] = useState(initialSmiles ?? "");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const apply = async (actionKey: string) => {
    const ketcher = ketcherRef.current;
    if (!ketcher) return;
    setBusy(true);
    setNotice(null);
    try {
      const smiles = (await ketcher.getSmiles()).trim();
      if (!smiles) {
        setNotice(t("struct_editor.empty", "请先在画布上绘制结构（或在下方文本框输入 SMILES 后点“载入到画布”）。"));
        return;
      }
      const smarts = (await ketcher.getSmarts()).trim();
      onApply({ smiles, smarts }, actionKey);
    } catch (e) {
      setNotice(t("struct_editor.read_failed", "读取画布结构失败：{{msg}}", { msg: e instanceof Error ? e.message : String(e) }));
    } finally {
      setBusy(false);
    }
  };

  const loadTextToCanvas = async () => {
    const ketcher = ketcherRef.current;
    const smiles = text.trim();
    if (!ketcher || !smiles) return;
    setBusy(true);
    setNotice(null);
    try {
      await ketcher.setMolecule(smiles);
    } catch {
      setNotice(t("struct_editor.invalid_smiles", "SMILES 无法解析，画布保持原样，请检查后重试。"));
    } finally {
      setBusy(false);
    }
  };

  const readCanvasToText = async () => {
    const ketcher = ketcherRef.current;
    if (!ketcher) return;
    setBusy(true);
    setNotice(null);
    try {
      setText((await ketcher.getSmiles()).trim());
    } catch {
      setNotice(t("struct_editor.read_failed", "读取画布结构失败，请重试。", { msg: "" }));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      width={960}
      destroyOnHidden
      onCancel={onClose}
      title={title ?? t("struct_editor.title", "绘制分子结构")}
      footer={
        <Space>
          <Button onClick={onClose}>{t("requirements.cancel", "取消")}</Button>
          {actions.map((a) => (
            <Button key={a.key} type={a.primary ? "primary" : "default"} loading={busy} onClick={() => void apply(a.key)}>
              {a.label}
            </Button>
          ))}
        </Space>
      }
    >
      <Suspense fallback={<Spin tip={t("struct_editor.loading", "正在加载结构编辑器…")} />}>
        <KetcherCanvas
          initialSmiles={initialSmiles}
          onReady={(k) => {
            ketcherRef.current = k;
          }}
          onError={(message) => setNotice(message)}
        />
      </Suspense>
      <Space direction="vertical" style={{ width: "100%", marginTop: 12 }}>
        <Space.Compact style={{ width: "100%" }}>
          <Input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={t("struct_editor.smiles_ph", "或在此输入 SMILES 文本（如 CCO 乙醇），点“载入到画布”同步到画布")}
            onPressEnter={() => void loadTextToCanvas()}
          />
          <Button loading={busy} onClick={() => void loadTextToCanvas()}>
            {t("struct_editor.load_to_canvas", "载入到画布")}
          </Button>
          <Button loading={busy} onClick={() => void readCanvasToText()}>
            {t("struct_editor.read_from_canvas", "读取画布")}
          </Button>
        </Space.Compact>
        {notice && <Typography.Text type="warning">{notice}</Typography.Text>}
        <Typography.Text type="secondary">
          {t("struct_editor.help", "画布绘制与 SMILES 文本双向同步；文本非法时只提示，不会清空画布。")}
        </Typography.Text>
      </Space>
    </Modal>
  );
}

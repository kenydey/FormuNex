import { PageContainer, ProCard } from "@ant-design/pro-components";
import { Alert, App, Button, Form, Input, InputNumber, Modal, Radio, Select, Space, Switch, Tag, Typography } from "antd";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Image as KImage, Layer, Rect, Stage, Text as KText, Transformer } from "react-konva";
import type Konva from "konva";
import { useTranslation } from "react-i18next";
import { authFetch } from "@/api/auth";
import { call } from "@/api/client";
import { useSession } from "@/app/session";
import { errorText } from "@/shared/feedback";
import { ELEMENT_NAMES, SIZES, clamp, defaultLayout, missingRequired, type ElementType, type LabelElement, type Layout } from "./labelLayout";

const PX = 4; // screen pixels per millimetre
const PICTO_SAMPLE = ["GHS02", "GHS07"];

function usePictogram(code: string): HTMLImageElement | undefined {
  const [img, setImg] = useState<HTMLImageElement>();
  useEffect(() => {
    const i = new window.Image();
    i.src = `/ghs/${code}.svg`;
    i.onload = () => setImg(i);
  }, [code]);
  return img;
}

function Picto({ code, x, y, side }: { code: string; x: number; y: number; side: number }) {
  const img = usePictogram(code);
  return img ? <KImage image={img} x={x} y={y} width={side} height={side} listening={false} /> : null;
}

/** 标签设计器 (方案 §3.1, react-konva): drag / resize on a 1 mm grid, GB 15258 element checklist, live PDF preview. */
export default function LabelDesignerPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { can } = useSession();
  const [layout, setLayout] = useState<Layout>(() => defaultLayout([100, 150]));
  const [selected, setSelected] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewDoc, setPreviewDoc] = useState<string | undefined>();
  const [saveOpen, setSaveOpen] = useState(false);
  const [form] = Form.useForm();
  const tr = useRef<Konva.Transformer>(null);
  const stage = useRef<Konva.Stage>(null);
  const templates = useQuery({ queryKey: ["plm", "label-templates"], queryFn: () => call<{ id: string; name: string; lang: string; version: number; is_default: boolean }[]>("GET", "/api/plm/label-templates") });
  const sel = layout.elements.find((e) => e.id === selected);

  useEffect(() => {
    const node = selected ? stage.current?.findOne(`#${selected}`) : null;
    tr.current?.nodes(node ? [node] : []);
    tr.current?.getLayer()?.batchDraw();
  }, [selected, layout]);

  const update = (id: string, patch: Partial<LabelElement>) =>
    setLayout((l) => ({ ...l, elements: l.elements.map((e) => (e.id === id ? clamp({ ...e, ...patch }, l.size_mm) : e)) }));
  const add = (type: ElementType) => {
    const id = `${type}-${Date.now().toString(36)}`;
    setLayout((l) => ({ ...l, elements: [...l.elements, clamp({ id, type, x: 4, y: 4, w: 40, h: type === "qr" ? 18 : 8, font_size: 7, text: type === "text" ? "批号：{{batch_no}}" : "" }, l.size_mm)] }));
    setSelected(id);
  };
  const preview = useMutation({
    mutationFn: async () => {
      const res = await authFetch("/api/plm/label-templates/preview", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ layout, document_id: previewDoc, variables: { batch_no: "B2610-01", production_date: "2026-10-16", net_content: "18 L" } }) });
      if (!res.ok) throw new Error((await res.json()).detail ?? res.statusText);
      const overflow = res.headers.get("X-Label-Overflow");
      if (overflow) message.warning(t("label.overflow", "以下元素内容放不下：{{e}}", { e: overflow }));
      return URL.createObjectURL(await res.blob());
    },
    onSuccess: (u) => setPreviewUrl((old) => { if (old) URL.revokeObjectURL(old); return u; }),
    onError: (e) => message.error(errorText(e)),
  });
  const save = useMutation({
    mutationFn: (v: { name: string; lang: string; is_default: boolean }) => call("POST", "/api/plm/label-templates", { ...v, layout }),
    onSuccess: () => { message.success(t("common.saved", "已保存")); setSaveOpen(false); void templates.refetch(); },
    onError: (e) => message.error(errorText(e)),
  });
  const load = async (id: string) => {
    const tpl = await call<{ layout: Layout }>("GET", `/api/plm/label-templates/${id}`);
    setLayout(tpl.layout);
    setSelected(null);
  };
  const [w, h] = layout.size_mm;
  const missing = missingRequired(layout);

  return (
    <PageContainer subTitle={t("label.sub", "拖拽排版，1 mm 吸附；生成标签时使用默认版式，文字会按 GB 15258 内容自动填充")}
      extra={<Space>
        <Select style={{ width: 220 }} placeholder={t("label.open", "打开版式")} onChange={(id: string) => void load(id)}
          options={(templates.data ?? []).map((x) => ({ value: x.id, label: `${x.name} v${x.version} ${x.lang}${x.is_default ? " ★" : ""}` }))} />
        <Button onClick={() => preview.mutate()} loading={preview.isPending}>{t("label.preview", "PDF 预览")}</Button>
        {(can("plm.document.write") || can("admin.templates")) && <Button type="primary" onClick={() => { form.setFieldsValue({ lang: "zh-CN", is_default: true }); setSaveOpen(true); }}>{t("label.save", "保存版式")}</Button>}
      </Space>}>
      <ProCard split="vertical" bordered>
        <ProCard colSpan={220} title={t("label.elements", "元素")}>
          <Space direction="vertical" style={{ width: "100%" }}>
            <Select value={JSON.stringify(layout.size_mm)} style={{ width: "100%" }}
              onChange={(v: string) => { const s = JSON.parse(v) as [number, number]; setLayout(defaultLayout(s)); setSelected(null); }}
              options={SIZES.map((s) => ({ value: JSON.stringify(s), label: `${s[0]} × ${s[1]} mm` }))} />
            {(Object.keys(ELEMENT_NAMES) as ElementType[]).map((k) => <Button key={k} block size="small" onClick={() => add(k)}>+ {t(`label.el.${k}`, ELEMENT_NAMES[k])}</Button>)}
            <Space><Switch size="small" checked={layout.border} onChange={(b) => setLayout({ ...layout, border: b })} />{t("label.border", "边框")}</Space>
          </Space>
        </ProCard>
        <ProCard style={{ overflow: "auto", background: "#e8e8e8" }}>
          {missing.length > 0 && <Alert type="warning" showIcon style={{ marginBottom: 8 }}
            message={t("label.missing", "缺少 GB 15258 要素：{{m}}", { m: missing.map((m) => ELEMENT_NAMES[m]).join("、") })} />}
          <Stage ref={stage} width={w * PX + 2} height={h * PX + 2} onMouseDown={(e) => { if (e.target === e.target.getStage()) setSelected(null); }}>
            <Layer>
              <Rect x={0} y={0} width={w * PX} height={h * PX} fill="#fff" stroke={layout.border ? "#000" : "#ccc"} listening={false} />
              {layout.elements.map((e) => (
                <Rect key={e.id} id={e.id} x={e.x * PX} y={e.y * PX} width={e.w * PX} height={e.h * PX}
                  fill={e.type === "qr" ? "#f0f0f0" : "rgba(31,95,139,0.06)"} stroke={selected === e.id ? "#1f5f8b" : "#9ab"} dash={[4, 3]} draggable
                  onClick={() => setSelected(e.id)} onTap={() => setSelected(e.id)}
                  onDragEnd={(ev) => update(e.id, { x: ev.target.x() / PX, y: ev.target.y() / PX })}
                  onTransformEnd={(ev) => {
                    const n = ev.target;
                    const patch = { x: n.x() / PX, y: n.y() / PX, w: (n.width() * n.scaleX()) / PX, h: (n.height() * n.scaleY()) / PX };
                    n.scaleX(1); n.scaleY(1);
                    update(e.id, patch);
                  }} />
              ))}
              {layout.elements.map((e) => e.type === "pictograms"
                ? PICTO_SAMPLE.map((c, i) => <Picto key={e.id + c} code={c} x={e.x * PX + i * (Math.min(e.h, e.w / 2) * PX + 4)} y={e.y * PX} side={Math.min(e.h, e.w / 2 - 1) * PX} />)
                : <KText key={e.id + "t"} x={e.x * PX + 2} y={e.y * PX + 2} width={e.w * PX - 4} listening={false} fontSize={Math.max(9, (e.font_size ?? 7) * PX * 0.35)}
                    align={e.align ?? "left"} fontStyle={e.bold ? "bold" : "normal"} fill="#333" text={e.type === "text" ? e.text ?? "" : t(`label.el.${e.type}`, ELEMENT_NAMES[e.type])} />)}
              <Transformer ref={tr} rotateEnabled={false} keepRatio={false} boundBoxFunc={(o, n) => (n.width < 12 || n.height < 12 ? o : n)} />
            </Layer>
          </Stage>
        </ProCard>
        <ProCard colSpan={260} title={sel ? t(`label.el.${sel.type}`, ELEMENT_NAMES[sel.type]) : t("label.props", "属性")}>
          {sel ? (
            <Form layout="vertical" size="small">
              <Space>
                {(["x", "y", "w", "h"] as const).map((k) => <Form.Item key={k} label={`${k} (mm)`}><InputNumber style={{ width: 52 }} value={sel[k]} onChange={(v) => update(sel.id, { [k]: Number(v) })} /></Form.Item>)}
              </Space>
              {sel.type !== "pictograms" && sel.type !== "qr" && <>
                <Form.Item label={t("label.font", "字号 pt")}><InputNumber min={4} max={36} step={0.5} value={sel.font_size} onChange={(v) => update(sel.id, { font_size: Number(v) })} /></Form.Item>
                <Form.Item label={t("label.bold", "加粗")}><Switch checked={!!sel.bold} onChange={(b) => update(sel.id, { bold: b })} /></Form.Item>
              </>}
              {sel.type !== "qr" && <Form.Item label={t("label.align", "对齐")}>
                <Radio.Group value={sel.align ?? "left"} onChange={(e) => update(sel.id, { align: e.target.value })} options={[{ value: "left", label: "左" }, { value: "center", label: "中" }, { value: "right", label: "右" }]} />
              </Form.Item>}
              {sel.type === "text" && <Form.Item label={t("label.text", "文字（可用变量 {{batch_no}} {{production_date}} {{net_content}}）")}>
                <Input.TextArea rows={3} value={sel.text} onChange={(e) => update(sel.id, { text: e.target.value })} />
              </Form.Item>}
              <Button danger block onClick={() => { setLayout({ ...layout, elements: layout.elements.filter((e) => e.id !== sel.id) }); setSelected(null); }}>{t("common.delete", "删除")}</Button>
            </Form>
          ) : <Typography.Text type="secondary">{t("label.pick", "点击画布上的元素编辑位置与样式")}</Typography.Text>}
          <div style={{ marginTop: 16 }}>
            <Typography.Text strong>{t("label.preview_with", "预览内容")}</Typography.Text>
            <Input placeholder={t("label.doc_id", "标签文档 ID（留空用示例内容）")} value={previewDoc} onChange={(e) => setPreviewDoc(e.target.value || undefined)} />
          </div>
          <div style={{ marginTop: 8 }}>{missing.length === 0 && <Tag color="green">{t("label.complete", "GB 15258 要素齐全")}</Tag>}</div>
        </ProCard>
      </ProCard>
      {previewUrl && <ProCard bordered style={{ marginTop: 16 }} title={t("label.preview", "PDF 预览")}>
        <iframe title="label" src={previewUrl} style={{ width: "100%", height: 640, border: 0 }} />
      </ProCard>}
      <Modal open={saveOpen} title={t("label.save", "保存版式")} onCancel={() => setSaveOpen(false)} confirmLoading={save.isPending}
        onOk={async () => save.mutate(await form.validateFields())}>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label={t("label.name", "版式名称")} rules={[{ required: true }]}><Input placeholder="18 L 桶标" /></Form.Item>
          <Form.Item name="lang" label={t("doc.lang", "语言")}><Select options={[{ value: "zh-CN", label: "中文" }, { value: "en-US", label: "English" }]} /></Form.Item>
          <Form.Item name="is_default" label={t("label.default", "设为默认版式")} valuePropName="checked"><Switch /></Form.Item>
        </Form>
      </Modal>
    </PageContainer>
  );
}

/** Label layout model shared by the designer and its tests (mirrors backend render/label_layout.py). */
export type ElementType = "product_name" | "ingredients" | "pictograms" | "signal_word" | "hazard_statements"
  | "precautionary_statements" | "supplier" | "emergency_phone" | "sds_reference" | "qr" | "text";

export interface LabelElement { id: string; type: ElementType; x: number; y: number; w: number; h: number; font_size?: number; align?: "left" | "center" | "right"; bold?: boolean; text?: string }
export interface Layout { size_mm: [number, number]; border: boolean; elements: LabelElement[] }

export const ELEMENT_NAMES: Record<ElementType, string> = {
  product_name: "化学品标识", ingredients: "危险组分", pictograms: "象形图", signal_word: "信号词",
  hazard_statements: "危险性说明", precautionary_statements: "防范说明", supplier: "供应商标识",
  emergency_phone: "应急咨询电话", sds_reference: "资料参阅提示", qr: "二维码", text: "自定义文字",
};
export const REQUIRED: ElementType[] = ["product_name", "pictograms", "signal_word", "hazard_statements", "precautionary_statements", "supplier", "emergency_phone"];
/** GB 15258 recommended label sizes (mm). */
export const SIZES: [number, number][] = [[50, 75], [75, 100], [100, 150], [150, 200], [200, 300]];

export const snap = (v: number, step = 1) => Math.round(v / step) * step;

/** Keep an element inside the label and on the 1 mm grid. */
export function clamp(e: LabelElement, size: [number, number]): LabelElement {
  const w = Math.max(3, Math.min(snap(e.w), size[0]));
  const h = Math.max(3, Math.min(snap(e.h), size[1]));
  return { ...e, w, h, x: Math.max(0, Math.min(snap(e.x), size[0] - w)), y: Math.max(0, Math.min(snap(e.y), size[1] - h)) };
}

export function missingRequired(layout: Layout): ElementType[] {
  const have = new Set(layout.elements.map((e) => e.type));
  return REQUIRED.filter((r) => !have.has(r));
}

/** A sensible starting layout for a w×h label (GB 15258 element order, top to bottom). */
export function defaultLayout(size: [number, number]): Layout {
  const [w, h] = size;
  const m = 4, iw = w - 2 * m;
  const at = (id: string, type: ElementType, y: number, hh: number, extra: Partial<LabelElement> = {}): LabelElement =>
    clamp({ id, type, x: m, y: Math.round(h * y), w: iw, h: Math.max(4, Math.round(h * hh)), ...extra }, size);
  return { size_mm: size, border: true, elements: [
    at("name", "product_name", 0.03, 0.07, { font_size: 11, align: "center", bold: true }),
    at("ingr", "ingredients", 0.10, 0.04, { font_size: 7, align: "center" }),
    at("pic", "pictograms", 0.15, 0.15, { align: "center" }),
    at("sw", "signal_word", 0.31, 0.05, { font_size: 12, align: "center" }),
    at("h", "hazard_statements", 0.37, 0.13, { font_size: 7 }),
    at("p", "precautionary_statements", 0.51, 0.27, { font_size: 6.5 }),
    { ...at("sup", "supplier", 0.80, 0.08, { font_size: 6 }), w: Math.round(iw * 0.75) },
    { ...at("tel", "emergency_phone", 0.89, 0.04, { font_size: 6 }), w: Math.round(iw * 0.75) },
    { ...at("sds", "sds_reference", 0.94, 0.03, { font_size: 6 }), w: Math.round(iw * 0.75) },
    clamp({ id: "qr", type: "qr", x: w - m - Math.round(iw * 0.2), y: Math.round(h * 0.80), w: Math.round(iw * 0.2), h: Math.round(iw * 0.2) }, size),
  ] };
}

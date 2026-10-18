import { Space, Tag, Tooltip } from "antd";
import { useTranslation } from "react-i18next";

const PICTO_NAMES: Record<string, [string, string]> = {
  GHS01: ["爆炸物", "Exploding bomb"], GHS02: ["火焰", "Flame"], GHS03: ["圆圈上方火焰", "Flame over circle"],
  GHS04: ["气瓶", "Gas cylinder"], GHS05: ["腐蚀", "Corrosion"], GHS06: ["骷髅和交叉骨", "Skull and crossbones"],
  GHS07: ["感叹号", "Exclamation mark"], GHS08: ["健康危害", "Health hazard"], GHS09: ["环境", "Environment"],
};

export function GhsPictogram({ code, size = 40 }: { code: string; size?: number }) {
  const { i18n } = useTranslation();
  const name = PICTO_NAMES[code]?.[i18n.language === "en-US" ? 1 : 0] ?? code;
  return (
    <Tooltip title={`${code} ${name}`}>
      <img src={`/ghs/${code}.svg`} alt={`${code} ${name}`} width={size} height={size} style={{ display: "inline-block" }} />
    </Tooltip>
  );
}

export function PictogramRow({ codes, size }: { codes: string[]; size?: number }) {
  if (!codes.length) return <span style={{ opacity: 0.6 }}>—</span>;
  return <Space size={4}>{codes.map((c) => <GhsPictogram key={c} code={c} size={size} />)}</Space>;
}

export function SignalWord({ word }: { word?: string | null }) {
  const { t } = useTranslation();
  if (!word) return <Tag>{t("ghs.no_signal_word", "无信号词")}</Tag>;
  return <Tag color={word === "危险" ? "red" : "orange"}>{word === "危险" ? t("ghs.danger", "危险") : t("ghs.warning", "警告")}</Tag>;
}

export function HazardTag({ hazardClass, category }: { hazardClass: string; category: string }) {
  const severe = /^1/.test(category);
  return <Tag color={severe ? "volcano" : "gold"}>{hazardClass} · {category}</Tag>;
}

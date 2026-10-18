import { App, Select } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { errorText } from "@/shared/feedback";
import type { Formulation, Lineage } from "./types";

/** Pick a saved formulation version (latest revision of each lineage first) and load its snapshot. */
export function FormulationPicker({ domain, value, onPick, style }: {
  domain?: string; value?: string; onPick: (versionId: string | undefined, f: Formulation | null) => void; style?: React.CSSProperties;
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const lineages = useQuery({ queryKey: ["formulations", "lineages", domain ?? ""],
    queryFn: () => call<Lineage[]>("GET", `/api/formulations/versions?limit=50${domain ? `&domain=${encodeURIComponent(domain)}` : ""}`) });
  const options = (lineages.data ?? []).map((l) => ({
    label: l.versions[0]?.name ?? l.lineage_id,
    options: [...l.versions].reverse().map((v) => ({ value: v.id, label: `${v.name} · v${v.version}${v.change_summary ? ` · ${v.change_summary}` : ""}` })),
  }));
  const pick = async (id: string | undefined) => {
    if (!id) return onPick(undefined, null);
    try {
      const d = await call<{ snapshot: Partial<Formulation>; name: string; domain: string }>("GET", `/api/formulations/versions/detail/${id}`);
      onPick(id, { rationale: "", predicted: {}, predicted_std: {}, score: null, warnings: [], ...d.snapshot,
        name: d.snapshot.name || d.name, domain: d.snapshot.domain || d.domain, ingredients: d.snapshot.ingredients ?? [] });
    } catch (e) {
      message.error(errorText(e));
    }
  };
  return (
    <Select allowClear showSearch optionFilterProp="label" style={{ minWidth: 320, ...style }} value={value} loading={lineages.isLoading}
      placeholder={t("design.pick_formulation", "选择已保存的配方版本")} options={options} onChange={(v) => void pick(v)}
      notFoundContent={lineages.error ? errorText(lineages.error) : undefined} />
  );
}

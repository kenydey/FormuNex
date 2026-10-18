import { ProCard, ProTable } from "@ant-design/pro-components";
import { Button, Input, Space, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { ErrorAlert } from "@/shared/feedback";
import { StructureEditorModal } from "@/shared/StructureEditor";
import { AVAIL_COLOR, availabilityLabel } from "../labels";
import { structureMode } from "./materialForm";

interface ChemicalHit {
  name: string; role?: string; smiles?: string | null; formula?: string | null; cas_no?: string | null; zh_name?: string | null;
  molar_mass?: number | null; reason?: string; similarity?: number | null; availability?: string;
}

type Search = { mode: "substructure" | "scaffold"; query: string } | null;

/** 结构搜索 (legacy MaterialsPanel "structure" tab): SMARTS substructure filter and Murcko-scaffold drop-in substitutes. */
export function StructureSearchTab() {
  const { t } = useTranslation();
  const [search, setSearch] = useState<Search>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const q = useQuery({
    queryKey: ["materials", "structure", search],
    enabled: !!search,
    queryFn: async () => {
      const s = search!;
      const p = new URLSearchParams({ top_k: "20", [s.mode === "scaffold" ? "smiles" : "smarts"]: s.query });
      const res = await call<{ hits?: ChemicalHit[] }>("GET", `/api/chemical/${s.mode === "scaffold" ? "scaffold-substitutes" : "substructure"}?${p}`);
      return (res.hits ?? []).map((h, i) => ({ ...h, _k: `${h.name}-${i}` }));
    },
  });
  const title = search && (search.mode === "scaffold"
    ? t("requirements.struct.scaffold_title", "Murcko 骨架替代候选（SMILES）：{{q}}", { q: search.query })
    : t("requirements.struct.sub_title", "SMARTS 子结构匹配：{{q}}", { q: search.query }));
  return (
    <>
      <ProCard bordered size="small" style={{ marginBottom: 12 }}>
        <Space direction="vertical" style={{ width: "100%" }}>
          <Input.Search enterButton={t("requirements.struct.search", "结构检索")} loading={q.isFetching && search?.mode === "substructure"}
            placeholder={t("requirements.struct.smarts_ph", "SMARTS 子结构，如 C(=O)O（羧基）/ c1ccccc1（苯环）/ [Si](C)(C)(C)C")}
            onSearch={(v) => v.trim() && setSearch({ mode: structureMode(v), query: v.trim() })} />
          <Input.Search enterButton={t("requirements.struct.scaffold", "骨架替代")} loading={q.isFetching && search?.mode === "scaffold"}
            placeholder={t("requirements.struct.smiles_ph", "或输入目标材料 SMILES，找同骨架可替换候选…")}
            onSearch={(v) => v.trim() && setSearch({ mode: "scaffold", query: v.trim() })} />
          <Typography.Text type="secondary">
            {t("requirements.struct.help", "子结构：筛选材料库中含指定官能团 / 环的材料（SMARTS）。骨架替代：同 Murcko 骨架 = drop-in 候选，侧链差异是主要变点 —— 适合在库材料停产 / 受限时的替换决策。")}
          </Typography.Text>
          <Button onClick={() => setEditorOpen(true)}>
            {t("requirements.struct.draw", "绘制结构…")}
          </Button>
        </Space>
      </ProCard>
      <StructureEditorModal
        open={editorOpen}
        title={t("requirements.struct.draw_title", "绘制分子结构并检索")}
        actions={[
          { key: "scaffold", label: t("requirements.struct.scaffold", "骨架替代"), primary: true },
          { key: "substructure", label: t("requirements.struct.search", "结构检索") },
        ]}
        onApply={(s, actionKey) => {
          // 画布绘制的 SMILES 走骨架替代；SMARTS 走子结构检索，均复用已有后端接口。
          setSearch(actionKey === "substructure" ? { mode: "substructure", query: s.smarts } : { mode: "scaffold", query: s.smiles });
          setEditorOpen(false);
        }}
        onClose={() => setEditorOpen(false)}
      />
      <ErrorAlert error={q.error} />
      {search && (
        <ProTable<ChemicalHit & { _k: string }>
          rowKey="_k" search={false} options={false} loading={q.isFetching} dataSource={q.data} pagination={false}
          headerTitle={`${title} · ${t("requirements.struct.count", "{{n}} 条", { n: q.data?.length ?? 0 })}`}
          locale={{ emptyText: t("requirements.struct.empty", "无匹配 — 尝试更简单的 SMARTS（如 C(=O)O）或确认 SMILES 合法") }}
          columns={[
            { title: t("requirements.mat.name", "名称"), dataIndex: "name", render: (_d, h) => <Space direction="vertical" size={0}>{h.name}{h.zh_name && <Typography.Text type="secondary">{h.zh_name}</Typography.Text>}</Space> },
            { title: t("requirements.mat.role", "角色"), dataIndex: "role", width: 110 },
            { title: t("requirements.struct.cas_formula", "CAS / 分子式"), width: 200, render: (_d, h) => [h.cas_no, h.formula].filter(Boolean).join(" · ") || "—" },
            { title: t("requirements.struct.similarity", "相似度 / 说明"), render: (_d, h) => [h.similarity != null ? `≈${Math.round(h.similarity * 100)}%` : null, h.reason].filter(Boolean).join(" ") || "—" },
            { title: t("requirements.mat.status", "状态"), width: 90, render: (_d, h) => <Tag color={AVAIL_COLOR[h.availability ?? "in_stock"]}>{availabilityLabel(t, h.availability)}</Tag> },
          ]} />
      )}
    </>
  );
}

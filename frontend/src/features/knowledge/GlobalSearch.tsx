import { SearchOutlined } from "@ant-design/icons";
import { Button, Input, List, Modal, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { call } from "@/api/client";
import { useUi } from "@/app/store";
import { ErrorAlert } from "@/shared/feedback";

interface Chunk { id: string; source_id: string; text: string; heading_path: string; page: number | null; meta?: Record<string, unknown> | null }

/** Cmd/Ctrl+K: quick hybrid search over the knowledge base (方案 §3.2). */
export function GlobalSearch() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const projectId = useUi((s) => s.projectId);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [hits, setHits] = useState<Chunk[]>([]);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const search = async (query: string) => {
    if (!query.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setHits(await call<Chunk[]>("POST", "/api/kb/hybrid-search", { query, top_k: 8, project_id: projectId, include_global: true }));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Button type="text" icon={<SearchOutlined />} onClick={() => setOpen(true)}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>Ctrl K</Typography.Text>
      </Button>
      <Modal open={open} onCancel={() => setOpen(false)} footer={null} width={720} title={t("search.title", "全局检索")}>
        <Input.Search autoFocus enterButton loading={busy} placeholder={t("search.placeholder", "检索知识库：配方、专利、标准、实施例…")} onSearch={search} />
        <ErrorAlert error={error} />
        <List
          style={{ marginTop: 12, maxHeight: 480, overflow: "auto" }}
          dataSource={hits}
          renderItem={(h) => (
            <List.Item style={{ cursor: "pointer" }} onClick={() => { setOpen(false); navigate(`/knowledge/sources/${h.source_id}`); }}>
              <List.Item.Meta
                title={<span>{h.heading_path || h.source_id} {h.page != null && <Tag>p.{h.page}</Tag>}</span>}
                description={<Typography.Paragraph ellipsis={{ rows: 2 }} style={{ margin: 0 }}>{h.text}</Typography.Paragraph>}
              />
            </List.Item>
          )}
        />
      </Modal>
    </>
  );
}

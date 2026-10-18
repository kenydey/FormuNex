import { Empty, Spin } from "antd";
import { useEffect, useState } from "react";
import { fetchBlobUrl } from "@/api/client";
import { ErrorAlert } from "./feedback";

/** Browser PDF viewer on a protected file (the token stays in the fetch, not in the URL). */
export function PdfPreview({ path, page, height = 720 }: { path?: string | null; page?: number; height?: number }) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    if (!path) return;
    let revoked: string | null = null;
    setUrl(null);
    setError(null);
    fetchBlobUrl(path).then((u) => {
      revoked = u;
      setUrl(u);
    }, setError);
    return () => {
      if (revoked) URL.revokeObjectURL(revoked);
    };
  }, [path]);
  if (!path) return <Empty />;
  if (error) return <ErrorAlert error={error} />;
  if (!url) return <Spin />;
  return <iframe title="pdf" src={page ? `${url}#page=${page}` : url} style={{ width: "100%", height, border: 0 }} />;
}

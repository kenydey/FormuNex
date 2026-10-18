import { Alert, Result } from "antd";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/api/client";
import { useSession } from "@/app/session";

export function errorText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}

export function ErrorAlert({ error }: { error: unknown }) {
  if (!error) return null;
  return <Alert type="error" showIcon message={errorText(error)} style={{ marginBottom: 12 }} />;
}

/** Render children only for users holding *permission* (globally or in *projectId*). */
export function Can({ permission, projectId, children, fallback = null }: {
  permission: string; projectId?: string | null; children: ReactNode; fallback?: ReactNode;
}) {
  const { can } = useSession();
  return <>{can(permission, projectId) ? children : fallback}</>;
}

export function Forbidden({ permission }: { permission: string }) {
  const { t } = useTranslation();
  return <Result status="403" title="403" subTitle={t("common.forbidden", "缺少权限：{{p}}", { p: permission })} />;
}

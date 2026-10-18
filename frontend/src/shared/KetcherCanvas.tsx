/**
 * Ketcher 画布（懒加载模块）。
 *
 * 本文件只被 StructureEditorModal 经 React.lazy 动态 import，
 * ketcher-react + ketcher-standalone（含 indigo wasm worker）不会进入主包。
 * 样式随 chunk 一起加载：`ketcher-react/dist/index.css`。
 */
import { useEffect, useRef } from "react";
import { Editor } from "ketcher-react";
import { StandaloneStructServiceProvider } from "ketcher-standalone";
import type { Ketcher } from "ketcher-core";
import "ketcher-react/dist/index.css";

const PROVIDER = new StandaloneStructServiceProvider();

export function KetcherCanvas({
  initialSmiles,
  onReady,
  onError,
}: {
  /** 打开时载入画布的 SMILES；非法时忽略并回调 onError（画布保持空白，不崩溃）。 */
  initialSmiles?: string;
  /** Ketcher 初始化完成，传出实例供外层读取 SMILES / SMARTS。 */
  onReady: (ketcher: Ketcher) => void;
  onError: (message: string) => void;
}) {
  const readyRef = useRef(false);
  const pendingRef = useRef<string | undefined>(initialSmiles);

  useEffect(() => {
    pendingRef.current = initialSmiles;
  }, [initialSmiles]);

  return (
    <div style={{ height: 480, border: "1px solid #d9d9d9", borderRadius: 8, overflow: "hidden" }}>
      <Editor
        // 模板库从 staticResourcesUrl + "/templates/" 预取；本应用不随包发布
        // Ketcher 模板文件，传空字符串则模板面板为空，不影响绘制与 SMILES/SMARTS 导出。
        staticResourcesUrl=""
        structServiceProvider={PROVIDER}
        errorHandler={(message) => onError(message)}
        onInit={async (ketcher) => {
          if (readyRef.current) return;
          readyRef.current = true;
          onReady(ketcher);
          const smiles = pendingRef.current?.trim();
          if (smiles) {
            try {
              await ketcher.setMolecule(smiles);
            } catch {
              onError("SMILES 无法解析，已打开空白画布，请重新绘制或修改文本后载入。");
            }
          }
        }}
      />
    </div>
  );
}

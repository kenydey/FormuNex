import { useNavigate } from "react-router-dom";
import { fx } from "./api";
import { materialMapOf } from "./logic";
import type { Formulation } from "./types";

/** Router state the formulation editor (lab/formulations) reads to load an adopted formulation. */
export interface AdoptState { adopt: Formulation; recommendId?: string | null }

/** Open a recommended formulation in the editor; records an adopt signal for the round (fail-open). */
export function useOpenInEditor() {
  const navigate = useNavigate();
  return (form: Formulation, opts: { recommendId?: string | null; index?: number; projectId?: string | null; levers?: { name: string; material_map?: Record<string, string> | null }[] } = {}) => {
    if (opts.recommendId) {
      void fx.adopt(opts.recommendId, {
        adopt_signal: "button", formula_index: opts.index,
        formula_snapshot: { name: form.name, ingredients: form.ingredients, score: form.score ?? null, material_map: materialMapOf(opts.levers) },
        project_id: opts.projectId,
      }).catch(() => undefined);
    }
    const state: AdoptState = { adopt: form, recommendId: opts.recommendId };
    navigate("/lab/formulations", { state });
  };
}

# FormuNex User Guide

**AI-assisted formulation R&D platform for metal surface treatment** — covering
anti-corrosion coatings, degreasers, and surface treatment agents.

This guide is written for R&D engineers and lab technicians, and walks through
every feature and the end-to-end workflow.

> 中文版见 [使用指南.md](./使用指南.md)。

---

## Table of contents

1. [Overview](#1-overview)
2. [Design principle: adapter + offline fallback](#2-design-principle-adapter--offline-fallback)
3. [UI tour](#3-ui-tour)
4. [The full closed-loop workflow](#4-the-full-closed-loop-workflow)
5. [Feature reference](#5-feature-reference)
6. [DOE feedback & model training](#6-doe-feedback--model-training)
7. [Import & export](#7-import--export)
8. [Session history](#8-session-history-f5)
9. [API reference](#9-api-reference)
10. [Install & run](#10-install--run)
11. [Configuration](#11-configuration)
12. [Enabling the real engines](#12-enabling-the-real-engines)
13. [FAQ & scope notes](#13-faq--scope-notes)

> **2026-09 addenda (§5.21–§5.25):** Knowledge Hub (quality-ops / Wiki / dossier), recommendation explain + constraint effect tracing, manual supplier quotes & `stale_price`, project-level auto-loop & soft-correct, persistent hybrid search vs session FAISS.

> **2026-10 addenda (§5.26):** third-round left-nav slimming (41 web entries), project shortcuts, custom process parameters & structured constraints, substance tox/eco records, multi-regulation & generic product coding.

---

## 1. Overview

FormuNex takes an R&D requirement (substrate, salt-spray target, film weight,
cure temperature, cleaning efficiency, VOC limit, …) and runs a complete loop:

```
requirement → patent/literature retrieval → RAG-grounded research
            → recommended formulations → IP novelty / risk check
            → DOE plan → cure/interface simulation
            → Bayesian closed-loop optimization → Top-N formula leaderboard
            → process-parameter optimization
                          ↑                                  │
                          └──── DOE experimental results ◄───┘
                                (train data-driven models)
                          ↑                                  │
                          └──── one-click self-driving loop ─┘
                                (data → retrain → optimize → next active-learning DOE)
```

**Core value**: raise formulation success rate and shorten the development
cycle. As real experimental data accumulates, data-driven models progressively
supersede the empirical surrogate, so recommendations and optimization move
ever closer to measured reality.

**Product domains** (including autodeposition):

| Domain | Key metrics |
|--------|-------------|
| `anticorrosion_coating` | salt-spray hours, film weight, adhesion, pencil hardness |
| `degreaser` | cleaning efficiency, foam index, bath life |
| `surface_treatment` | coating weight, salt-spray hours, adhesion-promotion index |
| `autodeposition_coating` | coating weight, salt-spray hours, cost / VOC |

---

## 2. Design principle: adapter + offline fallback

Every heavy external engine sits behind an **adapter** with a **deterministic
offline fallback**. FormuNex therefore **runs end-to-end today** — no GPU, no
API key, no C++ toolchain — and "lights up" the real engine automatically once
it is installed.

| Capability | Real engine (optional) | Built-in fallback |
|------------|------------------------|-------------------|
| Research / chat | 9 LLM providers — Claude, OpenAI, Gemini, Grok, Meta (Groq), DeepSeek, Qwen, Kimi, MiniMax | rule-based synthesis over the knowledge base |
| NL intent parsing | configured LLM (`complete_json`) | regex/keyword heuristic |
| Patent search | `patent_client` (optional, `.[patents]` — see the warning in the install section) | curated seed corpus per domain |
| Literature search | `arxiv`, Semantic Scholar (HTTP API, no SDK), OpenAlex | (offline returns no extra hits) |
| Internet search | `duckduckgo-search` | (offline returns no extra hits) |
| File ingestion | `markitdown` (PDF/DOCX/XLSX/PPTX/HTML/images…) → `pypdf`/`python-docx` | plain-text decoder |
| RAG (session chat) | ColBERT/PyLate (GPU) · **BM25+FAISS** (CPU) · sentence-transformers | in-memory TF-IDF |
| RAG (persistent KB) | **hybrid BM25 + embedding cosine** over `document_chunks` (`/api/kb/hybrid-search`); BM25 prefilter when near scan cap / hot p95 | keyword-only without embeddings |
| Grounded Q&A | ChemCrow agent (chemistry questions) · paper-qa (semantic synthesis) | TF-IDF re-rank → configured LLM → snippet |
| Property prediction | RDKit (8 descriptors) · MoLFormer (reserved) | transparent empirical surrogate |
| Rheology / Tg | Fox equation + Mooney viscosity (knowledge-base `tg_k`) | skipped if data missing |
| Color (CIELAB / ΔE₀₀) | `colour-science` | (skipped) |
| VOC / density | `thermo` mass-weighted density | nominal 1.3 kg/L assumption |
| Compound data | PubChemPy (SMILES / molar mass) | hand-curated raw-material library |
| Stoichiometry & safety | ChemFormula + acid/base, SVHC, VOC-category checks | self-contained formula parser + rule checks |
| Optimization | BayBE · BoTorch GP (qNEHVI) · Optuna (NSGA-II/TPE, CPU) — all optional | **native** numpy UCB Bayesian optimizer (default) |
| Active-learning DOE | BayBE / trained surrogate + EI on DOE grid (optional extras) | **native** DOE (LHS / factorial / CCD / …) — default |
| IP analysis | LLM JSON (`complete_json`) over retrieved patents | offline keyword overlap → risk tag |
| Process optimizer | shared engine over manufacturing parameters | Arrhenius / empirical outcome models |
| Cure / MD simulation | HTPolyNet · LUNAR · LAMMPS (Docker) | analytic approximation |

> Native **DOE** and the **numpy Bayesian optimizer** are the default product
> path. BayBE / Optuna / BoTorch are optional extras — missing them does not
> block recommend, DOE, or optimize. The genuinely lightweight, high-value
> parts — the **DOE engine** (full / fractional factorial, Plackett-Burman,
> central composite, Latin hypercube, AI-active selection) and the **Bayesian
> optimizer** — are implemented for real in pure numpy.

---

## 3. UI tour

A light workbench layout: a **left navigation** grouped by workflow stage, a
**top project selector** that scopes almost every page to the current project,
and a content area of pages or hub tabs. Pick a project first; without one,
most pages show only an empty state.

**Left navigation (41 second-level entries on the web, plus the dashboard)**:

| Group | Entries (in order) |
|---|---|
| Dashboard | My dashboard |
| Knowledge retrieval | Search, Knowledge sources & tree, Knowledge Q&A, Retrieval evidence audit |
| Literature & research | Library, External search & deep research, Research artifacts, Embodiment drafts, Patent / IP analysis, Wiki knowledge base |
| R&D requirements | Path wizard, Projects & specification contracts |
| Formulation & experiments | AI formula recommend, Formulation editor, DOE design, Experiment work records, Measurements & review, Material substitution, Inverse design, Methods & instruments, External ELN |
| Optimization & analysis | Surrogate models, Response surface, Uncertainty diagnostics, Optimization loop, Formulation properties, Rheology fitting |
| Documentation & release | Products & revisions, Formula-database mapping, Label designer, Regulatory assistant |
| Materials & regulations | Substances & regulatory data, Raw materials, Substance tox/eco records, Material knowledge graph |
| System admin | MCP tool approvals, Users & roles, Company configuration, Settings center, Audit log, Organization dashboard (plus a desktop-only entry: Desktop & local services) |

Entries no longer in the left nav, still reachable (routes kept): **quality-ops**
lives on the search hub's ops tab; **connectors** on the settings-center tab;
**agent memory** in the knowledge-Q&A page header; **project sources** only via
the contract page header.

**Research page (Literature & research → External search & deep research)**
uses tabs instead of the old three columns: **External search** (multi-source
patent / literature / internet search), **Source Q&A** (grounded Q&A over the
selected sources, with citation chips per answer), **Deep research**
(multi-source synthesis pipeline), and **STORM long-form**. The page header
also links to **internal KB search** and **research artifacts**.

- **Top bar**: global search, a project selector + **「+」** new-project button
  (needs `project.write`; shares the same modal as the project list page),
  a task center, a **to-do** badge (pending review measurements, linking to the
  measurements & review page), a light/dark theme toggle, a 中 / EN toggle, and
  an avatar menu (change password, log out). The project list's row actions
  (rename, delete) share the `project.write` permission.
- **Settings center** (System admin → Settings center, needs `admin.llm`):
  tabs are Model, API keys & retrieval routing, API access token, Skills,
  Environment variables, Dependency management, KB / KG maintenance, and
  Connectors. Enable/disable chat skills under the **Skills** tab; install from
  GitHub, a local zip/SKILL.md upload, or paste — dry-run preview + security
  review, then one-click confirm. Origin badges: bundled / github / local;
  user-installed skills can be uninstalled. Import MCP servers under the
  **Connectors** tab: paste/upload Claude/Cursor `mcpServers` JSON, or pull a
  `mcp.json` from GitHub; imported servers default to disabled; Probe still
  requires `mcp_client_enabled`. Optional domain packs (e.g. `coatings-silane`)
  install from the Skills tab's domain-pack area without changing the default
  formula-recommend playbook. Type `/` in the chat composer to pick enabled
  skills; GitHub-installed skills support update checks via pinned SHA.
- **Knowledge Q&A** (Knowledge retrieval → Knowledge Q&A): streamed Q&A over
  the project's knowledge base, with sessions persisted per project and a
  session list to return to earlier conversations. The composer **「+」**
  picker selects chat skills, connectors, Evidence mode, and MCP servers
  (max 4); explicit `/mcp <server> <tool> {json}` calls run readonly tools,
  while write-like tools require **session approval**
  (`POST /api/connectors/mcp/{id}/approve-session`) — not a permanent
  global grant.
- **Wave A (OpenScience) — report steel-stamp, Evidence fix-loop, MCP in chat**:
  - **Publication preflight**: before exporting a STORM report, FormuNex runs
    citation / placeholder / bare-numeric checks. Open **blocking** findings
    return HTTP 409 until you fix the markdown or override with actor+reason
    (`POST /api/wiki/preflight/override` then finalize). Draft generation still
    writes to disk (with `preflight` meta). Flag: `publication_preflight_enabled`
    (default on for export).
  - **Evidence reviewer fix-loop**: when `evidence_reviewer_enabled` and
    `evidence_reviewer_fix_loop_enabled` are on, a non-pass review appends an
    `[Auditor]` note and re-prompts the LLM (sync ≤3 rounds, stream ≤1). Chat
    stays fail-open; response includes `reviewer_fix`.
  - **MCP in composer**: enabled MCP servers appear under **「+」→ MCP servers**
    (max 4). Selection injects a generated `mcp-<id>` skill doc into the prompt.
    Explicit `/mcp <server> <tool> {json}` calls run readonly tools; write-like
    tools require **session approval** (`POST /api/connectors/mcp/{id}/approve-session`)
    — not a permanent global grant.
- **Wave B (OpenScience) — PaperQA, frozen literature, light screening**:
  - **PaperQA engine**: Evidence mode can use PaperQA with the **active chat LLM**
    (DeepSeek / OpenAI-compatible), not only an OpenAI key. Failures fall through
    to hybrid RAG. Kill-switch: `paperqa_enabled` (default on). Optional
    `paperqa_llm_model` / `paperqa_embedding`.
  - **Literature freeze**: Knowledge Hub → Reports shows a **文献冻结** strip.
    Capture project sources + search hits into a manifest, then Freeze. STORM /
    dossier packs prefer the frozen set. APIs under `/api/wiki/literature/*`.
    Flags: `literature_manifest_enabled` (default on);
    `frozen_corpus_required_for_export` (default off — turn on after soak).
  - **Light screening**: expand **筛选** on the freeze strip; inclusion/exclusion
    keywords mark items `match` / `no_match` / `uncertain`. Optional
    `screening_auto_freeze` writes matches into the frozen corpus. Flag
    `literature_screening_enabled` defaults off.
- **Wave C (OpenScience) — citation expand, provenance, citations skill, ChEBI**:
  - **Citation graph expand**: Evidence answers can attach OpenAlex
    backward/forward neighbors for seed DOIs (`citation_expand` on ChatResponse).
    DOI verify adds `notice_kind`
    (`none` / `notice` / `retracted_work` / `corrected` / `unknown`) from Crossref
    update links. Flag `citation_expand_enabled` (default on); DOI verify still uses
    `evidence_doi_verify_enabled`.
  - **Structured provenance**: ChatResponse / SSE done include
    `evidence_provenance` with `doi_status`, `evidence_availability`
    (`supported` / `partial` / `unavailable` / `unknown`), and short notes.
    Research chat shows a **Provenance · 证据诚实度** strip (availability +
    retracted / unresolved counts). Flag `evidence_provenance_enabled` (default on).
  - **Citations skill**: bundled `citations` Chat Skill (resolve-before-cite).
    Enable under **Settings → Skills**; pick via `/` in the composer. Reminds the
    model not to invent footnotes and to respect retraction / correction notices.
  - **ChEBI connector**: chemistry lookups enrich PubChem with ChEBI (EBI OLS)
    when `connectors_builtin_enabled` is on — fail-open beside PubChem.
- **Wave D (OpenScience) — sources audit, locator honesty, freeze OA enrich**:
  - **Sources audit**: ChatResponse / SSE may include `sources_audit` (claim →
    grade `supported`/`partial`/`unsupported`/`contradicted` + locators). Bundled
    `sources` skill (resolve claim↔passage). Flag `sources_audit_enabled` (default on).
  - **Locator honesty**: Citation chips show `pp. N` or **页码未知**. Export
    preflight can flag numeric lines whose `[^n]` footnotes lack page/¶
    (`citation_locator_preflight_enabled`, default on → warning;
    `citation_locator_preflight_blocking` upgrades to blocking).
  - **Freeze OA enrich**: Knowledge Hub freeze strip **补全文** batches Unpaywall/PMC
    fetch into the literature manifest (`POST /api/wiki/literature/enrich-oa`).
    Successful persist invalidates freeze (re-freeze after). Flag
    `literature_oa_enrich_enabled` (default on).
  - **Lightweight RO-Crate** (Wave D2): Hub STORM **导出 RO-Crate** builds a zip with
    `ro-crate-metadata.json`, report markdown, literature manifest, preflight, and
    `steel-stamp.json` honesty summary (no replay claim). Flag
    `ro_crate_export_enabled` (default off).
  - **Peer-review skill**: bundled `peer-review` (BLOCKING / OBSERVATION). Hub
    **审稿一遍** injects the STORM draft into the chat composer and selects the skill
    — does not auto-rewrite.
- **Wave E-Lit (OpenScience) — Literature Library MVP**:
  - **Hub「文献库」tab**: catalog over the same `literature_manifest` (tags, notes,
    manual collections, search/filter). Shares Capture / Freeze / OA enrich with the
    Reports freeze strip. Flag `literature_library_enabled` (**default off** — soak
    then enable); tab stays visible and shows a Settings CTA when off.
  - **DOI / ChemRxiv import**: paste DOIs or ChemRxiv URLs/UUIDs
    (`POST /api/wiki/literature/import-ids`). **No arXiv / PMID** (chemistry stack
    uses ChemRxiv via OpenAlex). Duplicates UI + merge
    (`GET .../duplicates`, `POST .../merge`) preserve screening and remap freeze ids.
  - **BibTeX / RIS**: export/import under `/api/wiki/literature/export.{bib,ris}` and
    `POST .../import` (ChemRxiv `eprint` supported; arXiv entries rejected).
- **Two kinds of keys (do not confuse them)**:
  - **Platform API bearer token** (`FORMUNEX_API_TOKEN`): protects `/api/*` when
    `FORMUNEX_API_AUTH_ENABLED=true`. For intranet dev, set auth to `false`. When
    on, enter the same token at the top of Settings (runtime localStorage; do not bake into the image).
  - **LLM / search API keys** (DeepSeek, Tavily, SerpAPI, …): configured under
    Settings → LLM or API keys, persisted in server `.env` — **not** the platform bearer.

---

## 4. The full closed-loop workflow

### Step ⓪ — research: pick a project, then search and ask

1. Pick a project in the top bar (or create one with 「+」). Under
   **Literature & research → External search & deep research**: the
   **External search** tab runs multi-source search (patents / literature /
   internet, plus local-file uploads — PDF/DOCX/XLSX/PPTX/HTML/images, parsed
   via markitdown); the **Source Q&A** tab answers questions about the
   selected sources, with citation chips per answer; the **Deep research** tab
   runs the DeepResearchEngine multi-source synthesis pipeline and generates a
   report; the **STORM long-form** tab generates a cited long-form report.
2. Under **Knowledge retrieval → Search**, run hybrid search over the
   project's knowledge base (BM25 / cosine / hybrid); **Knowledge sources &
   tree** manages sources and catalogs; **Retrieval evidence audit** shows
   claim → evidence grades (supported / partial / unsupported / contradicted).

> Offline, the patent search returns the curated seed corpus; literature,
> internet search needs its optional extra (see §12).
> After loading, source status indicators reflect which sources are actually
> available in the current environment.

### Step ① — set the requirement contract and generate recommendations

1. Under **R&D requirements → Projects & specification contracts**: the
   **Scenario** tab sets domain and substrate; the **Objectives** tab sets
   optimization targets, weights and directions; the **Process window** tab
   sets manufacturing parameter ranges; the **Custom process parameters** tab
   adds numeric-range or discrete-list parameters (unique keys, no clash with
   the 4 fixed keys, range low < high); the **Constraints** tab writes
   structured constraint rows (values mirror into the single-value field,
   range rows excluded); the **Redlines** tab lists banned/restricted
   substances. The header **✨ NL Intent** box is currently API-only (`POST /api/intent/parse`,
   LLM when configured, else regex fallback; see §5.10) — the contract page has
   no parse input box yet. A one-sentence brief — *"Waterborne epoxy
   anti-corrosion coating, 1000 h salt spray, cures at 120 °C"* — parsed via
   the API yields structured fields to fill into the contract. Click **Save** to apply the
   contract.
2. Project management: create a project from the top-bar 「+」 or the project
   list's **New project** button; row actions **rename** and **delete** the
   project (knowledge-base disposition delete | global, default delete;
   business data is always deleted).
3. Under **Formulation & experiments → AI formula recommend**: the
   **Generate** tab generates recommendations from the contract (high-active /
   baseline / lean variants, scored and sorted by the target metric), then
   switches to the **Leaderboard** tab automatically. Expand a card to see its
   ingredient table, predicted metrics with uncertainty (incl. **PVC / CPVC**,
   **Tg (°C)**, **viscosity_relative**, and optional **lab_L/a/b + ΔE₀₀**), the
   **3D molecular-viewer panel** (see §5.10), **Why recommended** (see §5.22),
   and a **🔍 IP compliance analysis** button (see §5.11). The leaderboard also
   supports manual formulation entry (`POST /api/formulations/manual`) and
   AI-assisted formulation editing (`POST /api/research/modify`, async SSE).

The platform will:
- retrieve patent/literature evidence for the domain (offline seed corpus);
- build a semantic-embedding (or TF-IDF) RAG index and recall the most relevant
  items;
- generate recommended formulation variants from the knowledge-base templates,
  scored and sorted by the target metric;
- synthesize a mechanism explanation and research conversation.

![Recommend · leaderboard](./images/05-recommend.png)

### Step ② — run the optimization loop

Under **Optimization & analysis → Optimization loop**, the tabs are
**Pareto & freeze** (default), **Point review**, and **Loop iteration**. The
platform runs asynchronous Bayesian multi-objective optimization:

- the optimizer auto-selects the strongest installed engine (**BoTorch** →
  **Optuna** → built-in numpy UCB);
- it samples the design space of key formulation levers (e.g. zinc inhibitor
  loading, resin/hardener ratio);
- each candidate is validated for stoichiometry and scored with the
  empirical/data blended predictor (weighted multi-objective aggregation);
- results feed back into the optimizer and converge.

**Result**: the Pareto front and Top formulations; the point-review tab lets
you confirm / reject candidates one by one; a frozen formulation moves on to
Step ⑥ documentation & release.

### Step ③ — generate a DOE and feed measured results back

1. Under **Formulation & experiments → DOE design**, the **Designer** tab
   offers design types (orthogonal, central composite CCD, Plackett-Burman,
   mixture, D-optimal, or **🧠 AI active selection**); click **Fill from
   process window** to center factors on the contract set-points, then
   **Generate design**. AI-active rows are highlighted in violet — these are
   the points expected improvement (EI) flagged as most informative.
2. The system produces a run table (one row per experiment, natural factor
   values + a blank measured column).
3. Under **Formulation & experiments → Experiment work records**: the
   **Tasks** tab dispatches tasks; the **Ledger** tab (AG Grid) takes the
   actual executed parameters and measured metrics (Excel paste supported);
   click **save ledger & sync**. **Training records** and **cross-batch
   search** tabs review history; **similar historical formulations** finds
   similar past formulas.
4. Two feedback paths:
   - **Manual**: type the lab-measured metric values directly into the
     "measured" column, then sync and train.
   - **Batch**: click **Export CSV** to hand off to the lab → they fill it in →
     click **Import CSV** to upload.
5. Once a metric accumulates ≥ `min_train_samples` (default 4) samples, the
   platform automatically trains a data-driven model for that (domain, metric).

**Result**: the model-quality dashboard (Optimization & analysis → Surrogate
models) shows R² + RMSE; recommendations and optimization switch to the
"empirical + data" blend.

> **The closed loop**: the more measured data, the higher the data-driven model
> weight (blend weight `w = n/(n+8)`), and the closer predictions track reality.

### Step ④ — process-parameter optimization

Process optimization is no longer a standalone modal; it lives in the contract
and the DOE: set manufacturing parameter ranges on the **contract → Process
window** tab (cure temperature, cure time, dispersion RPM, film thickness, bath
temperature, immersion time, pH set-point, accelerator factor, …) and add
product-specific parameters on the **Custom process parameters** tab; in the
**DOE designer**, **Fill from process window** centers factors on those
set-points. The optimization loop samples the process space under the same
window constraints. Domain-specific outcome models (Arrhenius cure conversion,
Q10 cleaning, phosphating power-law) score each candidate. The result shows
the optimal process recipe and a mini convergence chart.

### Step ⑤ — one-click self-driving loop

Under **Optimization & analysis → Optimization loop → Loop iteration**, click
run. In a single async task the platform:

1. pulls every measured record for the active domain from the registry;
2. retrains the data-driven model (RMSE / R² are reported per metric);
3. runs the optimizer (engine is auto-selected — BoTorch / Optuna / numpy);
4. uses the freshly-retrained surrogate to compute EI on the DOE grid and
   suggests the next active-learning batch.

The page shows engine, sample count, R²/RMSE cards (with a ↓ trend arrow as
RMSE drops over rounds), the convergence chart, and the next DOE batch (AI rows
highlighted in violet). Export the next batch as CSV and the loop continues
across rounds.

### Step ⑥ — documentation & release (after freezing)

1. Under **Documentation & release → Products & revisions**: create a product
   (auto-coded per the company coding rule; the code belongs to the product
   and is shared by its revisions); create a revision and link a formulation
   version (an empty lineage shows inline guidance; the version field is
   optional; the dropdown filters by project and still lists unscoped global
   versions).
2. Generate SDS / TDS / COA / GHS label documents: by default the China
   regulation is checked; tick applicable regulations in the company
   configuration (China / EU / US) to compute and export one edition per
   regulation. SDS §11/12 come from **Materials & regulations → Substance
   tox/eco records**; SDS §8 still uses GBZ only (see §5.26).
3. The revision detail's **release gate** tab shows G1–Gn checks; approve,
   sign (sign or publish permission), and release. The gate stays fail-closed:
   the CLP / OSHA rule sets are draft and require EHS sign-off before release.
4. The **Label designer** validates GB 15258 completeness before emitting a
   label; the **Regulatory assistant** looks up regulation texts.

---

## 5. Feature reference

### 5.1 Multi-objective optimization (weighted Pareto aggregation)

Industrial formulation is usually a trade-off — high salt-spray endurance *and*
low cost *and* low VOC. Each domain ships with default objective sets:

| Domain | Default objectives |
|--------|--------------------|
| Anti-corrosion | salt_spray (maximize, 0.5) + cost (minimize, 0.25) + sustainability (maximize, 0.25) |
| Degreaser | cleaning (maximize, 0.5) + cost (minimize, 0.3) + VOC (minimize, 0.2) |
| Surface treatment | salt_spray (maximize, 0.5) + coating weight (maximize, 0.2) + cost (minimize, 0.3) |

Each objective is min-max normalized then weighted; "minimize" objectives are
inverted so higher is always better. You can override metrics, weights and
directions via the API's `objectives` field.

### 5.2 Cost, sustainability & PVC / CPVC scoring

All 32 seed materials in the knowledge base carry `price_cny_per_kg` and
`voc_contrib` (volatile fraction, 0–1). Every prediction computes:

- `cost_cny_per_kg` — mass-fraction-weighted formulation cost;
- `voc_gpl` — VOC content (g/L); density defaults to ~1.3 kg/L but is computed
  from a real mass-weighted mixture density when the `thermo` library is present;
- `sustainability_idx` — sustainability index (0–100, penalizing high VOC and cost);
- **`pvc`** — pigment volume concentration (%); volume fraction of pigments and
  fillers vs. total non-volatile;
- **`cpvc`** — critical PVC, computed from the Asbeck formula using oil
  absorption from the knowledge base;
- **`pvc_to_cpvc_ratio`** — ratio used to flag over-filled coatings (poor gloss
  / sealing when > 1).

When a formulation's VOC exceeds the requirement's `voc_limit_gpl`, the card
shows an amber warning. The safety checker also raises warnings for acid/base
conflicts and known REACH SVHC ingredients.

### 5.3 Rheology, Tg & color

- **Tg (°C)** — multi-component glass transition temperature via the Fox
  equation, using `tg_k` values curated on resins/hardeners. Returns `None`
  when any component lacks `tg_k`.
- **viscosity_relative** — Mooney pigment-volume viscosity model
  (`η_r = exp(2.5·φ / (1 − φ/φ_max))`, `φ_max ≈ 0.64`).
- **viscoelastic_index** — 0–1 index combining PVC/CPVC and Tg distance from
  room temperature.
- **lab_L / lab_a / lab_b + delta_e** — CIELAB color & CIEDE2000 vs. white
  reference, computed from `spectral_reflectance` on pigments when the
  `colour-science` library is installed (the leaderboard card renders a 2 rem
  swatch when these fields are present).

### 5.4 Prediction confidence intervals

Every predicted metric carries a `predicted_std` uncertainty estimate:

- **sklearn random forest**: standard deviation across individual tree
  predictions (ensemble uncertainty);
- **numpy ridge fallback**: training-set RMSE as a conservative constant.

Cards show `value ± std`; when std exceeds 20% of the value it is rendered amber
to flag low-confidence predictions.

### 5.5 Convergence visualization

The optimizer returns the best-so-far curve (`history`), rendered as a recharts
line chart — X axis = iteration, Y axis = best objective score, hover for exact
values. The same chart is reused inside the Self-Driving Loop modal.

### 5.6 Model-quality dashboard

Each trained model shows a half-circle SVG R² gauge (green >85% / amber >60% /
red otherwise) plus backend type (sklearn-rf or numpy-ridge), sample count,
RMSE, and cross-validated R².

### 5.7 Multi-LLM provider support

The research synthesis and grounded Q&A can run on any of **nine LLM
providers**, selectable from the ⚙ **Settings** dialog (or via environment
variables). All seven OpenAI-compatible providers share the single `openai` SDK
with a per-provider `base_url`; Claude uses `anthropic` and Gemini uses
`google-genai`.

| Provider | Recommended model | SDK / base URL |
|----------|-------------------|----------------|
| Anthropic (Claude) | `claude-sonnet-4-6` | `anthropic` |
| OpenAI | `gpt-4o` | `openai` |
| Google (Gemini) | `gemini-2.0-flash` | `google-genai` |
| xAI (Grok) | `grok-2` | `openai` · `https://api.x.ai/v1` |
| Meta (via Groq) | `llama-3.3-70b-versatile` | `openai` · `https://api.groq.com/openai/v1` |
| DeepSeek | `deepseek-v4-pro` (flagship, recommended) / `deepseek-v4-flash` (fast, economical) | `openai` · `https://api.deepseek.com` |
| Qwen (通义千问) | `qwen-plus` | `openai` · `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| Kimi (Moonshot) | `kimi-k3` | `openai` · `https://api.moonshot.cn/v1` |
| MiniMax | `abab6.5s-chat` | `openai` · `https://api.minimax.chat/v1` |

In Settings, pick a provider and model, paste an API key (with show/hide), set a
custom base URL if needed, and click **Save & test connection**. Keys are written
to the server `.env` (runtime overlay). With no key configured, everything still
works via the offline rule engine.

**Settings tabs:**

| Tab | Purpose |
|-----|---------|
| LLM | Active provider, model, API key, base URL |
| API keys | SerpAPI, Tavily, EPO, USPTO, … grouped secrets |
| Dependencies | Catalogued optional pip packages; install via `POST /api/dependencies/install` (restart backend after) |

If all tabs are empty and the console shows 401: bearer auth is on but the SPA has
no platform token. Disable auth for intranet, or paste the **API access token**
matching `FORMUNEX_API_TOKEN` at the top of Settings.

### 5.8 Multi-source research, file upload & grounded Q&A

The research step is split into selectable sources, all behind adapters with
offline fallbacks:

- **Patents** — `patent_client` (optional, `.[patents]`; offline: curated seed corpus per domain);
- **Literature** — arXiv + Semantic Scholar;
- **Internet** — DuckDuckGo (no API key required);
- **Local files** — upload PDF/DOCX/XLSX/PPTX/HTML/images; **markitdown**
  converts them to text (falling back to `pypdf` / `python-docx`), then splits
  the text into evidence chunks.
- **Deep Research** — the 🔬 button in the left panel runs `POST /api/research/deep`, an async
  DeepResearchEngine pipeline: `web_agent` (DuckDuckGo), `kb_agent` (HyDE query expansion →
  vector/TF-IDF re-rank → `llm_rerank` → `answer_question`), and `report_agent`
  (cross-validates web vs. KB sources, enforces per-claim `[source]` citations and
  marks insufficient-evidence gaps). The result is a structured `ComprehensiveReport`
  with `report_markdown`, `citations`, and `candidates` that auto-populate the center
  column and leaderboard.

Results from all selected sources are merged, de-duplicated, and ranked by
relevance into the left-column source list. The center-column chat then answers
questions **grounded in those sources**: a semantic-embedding (with
`sentence-transformers`) or TF-IDF re-rank selects the most relevant evidence,
the LLM composes an answer, and the citations used are shown as chips under
each reply.

### 5.9 Optional intelligence engines (auto-detected)

Beyond the LLM providers, FormuNex layers several specialist engines on top of
the built-in fallbacks. Each is **auto-detected**: install its library and the
adapter switches over with no code change; absent, the platform keeps using the
deterministic offline path, so behaviour never breaks.

- **Optimizer tiering** — the closed-loop optimizer auto-selects the best engine
  installed: **BoTorch** (GP + qNEHVI, the `bo` extra) → **Optuna** (NSGA-II/TPE,
  CPU-only, the lightweight `optimize` extra) → the built-in numpy UCB optimizer. The `engine`
  used is reported on the optimization result.
- **Active-learning DOE** — when ≥ `min_train_samples` records exist, the DOE
  modal's *🧠 AI active selection* uses the trained surrogate's expected
  improvement on the DOE grid; otherwise it falls back to random LHS.
- **Grounded-Q&A routing** — a chemistry-flavoured question (LogP, solubility,
  toxicity, compatibility, reaction, structure…) is routed to the **ChemCrow**
  agent when installed; other questions use **paper-qa** semantic synthesis with
  page-level citations. Both fall back to the TF-IDF re-rank → configured LLM →
  snippet path.
- **ChemCrow availability badge** — when the `intel` extra is installed and
  **Literature** or **Internet** is selected, the Sources panel shows a green
  🧪 badge ("ChemCrow chemistry-enhanced retrieval · enabled"); when `intel` is
  absent, a grey badge with a `pip install -e '.[intel]'` hint is displayed
  instead.
- **Semantic RAG** — with `sentence-transformers` installed (the `embedding`
  extra), **session** chat retrieval can use embeddings (BM25+FAISS / TF-IDF
  fallback). The **persistent KB** uses a separate `hybrid_search` path
  (BM25 + cosine; §5.21) — **not** chromadb and **not** the same store as
  session FAISS.
- **Compound enrichment** — set `FORMUNEX_ENRICH_COMPOUNDS=true` and, with
  **PubChemPy** installed, the platform backfills missing SMILES / molar-mass on
  the raw-material library at startup (curated values always win).
- **Physically-grounded VOC + Tg + viscosity** — with **thermo** installed,
  `voc_gpl` is computed from a real mass-weighted mixture density; the Fox &
  Mooney models pick up `tg_k` values curated on resins.
- **Color metrology** — with **`colour-science`** installed (the `color`
  extra), CIELAB and CIEDE2000 ΔE₀₀ are computed from spectral data; the
  leaderboard card renders a real color swatch.

### 5.10 NL intent parser (✨)

One-sentence brief parsing (NL Intent) is currently API-only
(`POST /api/intent/parse`). Send a one-sentence project brief and the platform
extracts a structured `Requirement`:

- with an LLM configured, the parser uses `complete_json()` (the same shared
  JSON-extraction helper that powers IP analysis);
- without an LLM, a fully deterministic regex/keyword fallback handles domain
  detection (anti-corrosion / degreaser / surface-treatment), substrate
  (carbon/galvanized/aluminum/stainless/magnesium), salt-spray hours, VOC limit
  (200 ≤ g/L for "low VOC / waterborne") and cure temperature.

The response lists the fields it filled, and the form below is updated in place.

### 5.11 IP compliance analysis (🔍)

Each leaderboard card has a **🔍 IP 合规分析** button. The platform:

- extracts chemical-term keywords from the ingredient list;
- retrieves up to N relevant patents from `literature.search_patents()`;
- with an LLM configured, calls `complete_json()` with a prompt asking for
  `{novelty_score, risks[], whitespace_hints[]}`;
- without an LLM, falls back to a deterministic keyword-overlap risk tagger.

The result modal shows a novelty gauge (0–1), per-patent risk cards with claim
overlap and remediation hints, and white-space opportunities for differentiation.

### 5.12 Process-parameter optimization (⚙️)

Manufacturing knobs — cure temperature/time, dispersion RPM, film thickness,
bath temperature, immersion time, pH, accelerator factor — are an independent
design space from the formulation composition. The **Process Optimization**
modal runs the same Bayesian engine over this space; domain-specific outcome
models score each candidate:

- **anti-corrosion**: Arrhenius cure conversion, dispersion uniformity, salt-spray
  thickness factor;
- **degreaser**: temperature-corrected cleaning efficiency, foam decay;
- **surface treatment**: phosphating power-law mass deposition.

### 5.13 Self-driving loop (🔄)

The Self-Driving Loop modal wires together every part of the platform into a
one-click iteration:

```
records (per domain) → retrain → optimize (auto-selected engine)
                              ↓
                  RMSE / R² → next active-learning DOE batch
```

It's a thin orchestrator over existing pieces (`registry.records_for`,
`workflow.run_optimization`, `active_learning_doe`); when no measured data
exists yet it degrades gracefully to empirical prior + LHS sampling.

### 5.14 3D molecular viewer (reserved)

Each expanded leaderboard card carries a **3D molecular-viewer panel**. In this
release it is a **placeholder + data contract**: it lists the formulation
components that carry a SMILES string and notes that they will be rendered as
ball-and-stick models via **3Dmol.js**. The full WebGL viewer (and the reserved
**MoLFormer** embedding path for richer property prediction) ships in a later
upgrade; the placeholder keeps the bundle lightweight today.

### 5.15 Inverse design (🎯)

**⭐ Recommend** proposes candidates. **🎯 Inverse Design** starts from the
specification instead: given targets, return a set of formulations that satisfy
them and show what each trades away.

**What makes it different from optimization.** Until this shipped, the
reconstruction step could only rescale the weight percentages of a hardcoded
baseline template. Which materials appear was a constant; only how much of each
was a variable. That single constraint is why three separate things were out of
reach at once — there was no inverse design (no "choose ingredients" dimension
in the search space), no material substitution (swapping a material *is*
changing the topology), and the Pareto view was decorative (candidates all came
from the same template, so the front held no real diversity). Unlocking the
topology made all three reachable from one foundation.

**Request** — hard constraints and soft objectives are now distinct, which they
previously were not (both were folded into `ObjectiveSpec.weight` and used only
for scoring, never as conditions a search had to satisfy):

| Field | Meaning |
|---|---|
| `targets.hard[]` | `(metric, op: le\|ge\|between, value[, value_max])` — must hold |
| `targets.soft[]` | `ObjectiveSpec` — optimized and traded off against each other |
| `population`, `generations` | search budget (default 48 × 30) |
| `seed_with_llm` | seed the initial population with LLM proposals (falls back to baseline + randomised variants) |

**Engine** — NSGA-II, pure numpy. That choice follows from measurement, not
preference: the environment ships numpy only (baybe / optuna / botorch / torch /
rdkit / scipy / sklearn are all optional and absent by default), the repository's
four optimizers are single-objective scalarisers, and a single candidate costs
about 1.1 ms to evaluate — so 60 × 40 ≈ 2.7 s of direct evolutionary search
beats wrapping the predictor in another surrogate. Constraints are handled by
**constraint-domination** (feasible always beats infeasible; between two
infeasible candidates the smaller total violation wins), which avoids inventing
penalty weights.

**Response**: `candidates`, `pareto_frontier_ids`, `tradeoff` (the existing
`TradeOffAnalysis`, so scenario picks and confidence come along for free),
`generations`, `evaluations`, `engine`, `rejected_infeasible`, `seeded_from`.

Two behaviours are worth knowing because they were only visible when the search
was actually run end-to-end, and no unit test would have caught either:

- **It will exploit the surrogate if you let it.** Unbounded, it found a
  "formulation" claiming 5110 hours of salt spray. Each slot is now bounded by
  the DOE lever ranges.
- **Crowding distance measures objective space only**, so two candidates built
  from entirely different materials look interchangeable once their predicted
  metrics are close, and selection collapses onto a single composition.
  Structural niching caps how many individuals may share an ingredient set,
  which took one distinct composition to twelve.

![Inverse design · hard constraints & soft objectives](./images/10-inverse-design.png)

Async: `POST /api/design/inverse` → 202 + SSE progress.

### 5.16 Material substitution & supply risk (🔁)

`POST /api/materials/substitutes` ranks replacements for one slot of a
formulation by fusing three signals:

1. **Structural similarity** — exact `substitute_group` match scores highest,
   then shared `functional_class`, then Hansen distance
   `Ra = √(4Δd² + Δp² + Δh²)`; Tanimoto fingerprint similarity is added when
   RDKit is available (detected via `chemtools.availability()`, not try/except).
2. **Predicted deviation** — the genome is rebuilt with the candidate and
   re-predicted, yielding a per-metric Δ. This is the differentiator: the answer
   is not "these are similar" but "this is what changes".
3. **Literature evidence** — knowledge-graph `substitutes` edges, each carrying
   `{source_id, chunk_id, sentence}`.

All 32 seed materials carry `functional_class` and `substitute_group` (18
interchangeable groups: `epoxy_hardener`, `anticorrosive_pigment`,
`pu_crosslinker`, `organic_solvent`, `thixotrope`, …). Note that `extender` and
`thixotrope` are deliberately separate groups — talc and fumed silica are not
interchangeable despite both being fillers.

> **`delta_confidence` is not decoration — read it.** Without RDKit,
> `_molecular_features` returns `{}` and the predictor can only distinguish
> same-role materials through role loading, the amine/epoxy equivalent ratio,
> and price/VOC lookups. Measured: swapping three epoxy hardeners changes cost
> (¥13.2 / 17.8 / 20.2 per kg) and leaves `salt_spray_hours` **identical at
> 867 h** for all three. The report reports `delta_confidence: cost_only` so an
> unchanged salt-spray figure is not mistaken for evidence that the swap is
> performance-neutral. Install `".[science]"` to raise it.

**Candidate recall** uses `role` + `substitute_group`, deliberately *not*
`genome.swappable()` — that is a *search* constraint which excludes pigments and
fillers, whereas substitution is user-directed and any slot should be queryable.

![Material substitution page](./images/11-substitution.png)

**Supply risk**: `POST /api/materials/availability` marks a material
`discontinued` or `restricted`; `GET /api/materials/supply-risk` then lists
every affected formulation with substitution suggestions.

### 5.17 Per-measurement results & QC report ingestion (📄)

`measured` used to be a single JSON dictionary on the experiment row — values
with no unit, no method and no timestamp. The `measurements` table now stores
one row per observation (metric, value, **unit, method, spec limit**,
timestamp), so a salt-spray number records that it came from ASTM B117 against a
≥1000 h spec and is therefore comparable to the next one.

**This is backward compatible by construction.** `ExperimentRecord.measured`
survives as a Pydantic `computed_field` derived from `measurements`, so every
existing reader is untouched, and a `model_validator(mode="before")` lifts
`measured` out of legacy payloads into `Measurement` objects.

**QC report upload** (`POST /api/qc/report`): upload → LLM extraction → ingest.
The ingest runs in a **single transaction**, deduplicates by content hash, and
attaches the original file **before** writing the measurements — reversing that
order would let a mid-way failure leave behind numbers with no provenance.

![QC report upload](./images/12-qc-report.png)

Read back with `GET /api/qc/experiments/{experiment_id}/measurements`.

### 5.18 Formulation revision history

`POST /api/formulations/versions` snapshots a formulation into a **lineage** — a
parent/child chain. Diffs are structured (ingredients added / removed /
adjusted, plus renames) rather than text, and `describe_diff` generates a
one-line summary when no author note was supplied:

> 移除 1 项（聚酰胺固化剂）；新增 1 项（异佛尔酮二胺）；调整 2 项（环氧树脂、二甲苯）

![Formulation revision history](./images/13-version-history.png)

| Endpoint | Purpose |
|---|---|
| `POST /api/formulations/versions` | save a version (optionally under a parent) |
| `GET /api/formulations/versions?name=` | find lineages by formulation name |
| `GET /api/formulations/versions/{lineage_id}` | walk one lineage |
| `GET /api/formulations/versions/detail/{version_id}` | one version's full snapshot |
| `GET /api/formulations/versions/{from_id}/diff/{to_id}` | structured diff |

### 5.19 Editable material catalogue & manual supplier quotes

The raw-material library is a **seed library plus a database overlay**, exposed
as an ordinary mapping: curated seed chemistry always wins, and fields the seed
never carries — `availability`, `supplier`, `functional_class`, `hansen_*`,
`substitute_group` — are filled from the database.

**Manual quotes (A′):** edit per-supplier `price_cny_per_kg`,
`price_observed_at`, `lead_time_days`, `moq`, `pack_size`
(`price_source=manual`). Missing observation dates or old prices annotate
**`stale_price`**. Recommendation explain and substitution candidates surface
these as supply badges (light demotion only — no silent hard penalty). Cost
prediction still primarily uses catalogue engineering estimates (feeding quotes
into `cost_cny_per_kg` is backlog).

| Endpoint | Purpose |
|---|---|
| `GET /api/materials` | search the catalogue (filter by role, functional class, substitute group) |
| `POST /api/materials` | add or update a material (optional `suppliers_json` quotes) |
| `POST /api/materials/availability` | mark `available` / `restricted` / `discontinued` |

> Canonical store is `suppliers` + `material_suppliers`; `materials.suppliers_json`
> remains dual-written by default for compatibility; reads hydrate from links.

### 5.20 Referential integrity check

Most cross-table references in this schema are plain string columns, and several
**cannot** become foreign keys: under `FORMUNEX_CAMPAIGN_BACKEND=datalab` the
row a reference points at lives in an external ELN, so a database constraint
would reject perfectly valid data. The honest alternative to a constraint you
cannot enforce is a check you actually run — otherwise orphans accumulate
silently and the first symptom is a retrieval quietly returning less than it
should.

`GET /api/kb/integrity` reports every soft reference with its orphan count.
References that are structurally unjoinable (Datalab-backed) are flagged
`unjoinable` and excluded from the orphan total rather than reported as a bogus
count.

> SQLite does not enforce foreign keys unless `PRAGMA foreign_keys=ON` is set,
> **per connection** — without it every `ForeignKey` declaration is decorative.
> The engine sets it on every new connection.

### 5.21 Knowledge Hub (📚)

The legacy Knowledge Hub modal has been split into three left-nav hub pages
(old deep links redirect to the matching tab):

| Page | Tabs | Purpose |
|---|---|---|
| Knowledge retrieval → Search (search hub) | Quick search, Deep search, Ops | Ops holds the quality dashboard, retrieval probe, and parsing & structure recognition |
| Literature & research → Library (library hub) | Catalog & import, Frozen & screening, Smart collections (scheduled rules) | Shares Capture / Freeze / OA-enrich with the report freeze strip |
| Literature & research → Wiki knowledge base | Page browse, Link graph | Compiled-memory pages; summaries can embed into dual-track retrieval (`wiki_embed_enabled`; Claims still filter wiki); link graph is **not** the materials KG |

**Gates (defaults):** topicality enforce on (`kb_relevance_shadow=False`);
retention purge never auto-deletes (dry-run + confirm).

| Path | Used for | Implementation |
|------|----------|----------------|
| Session RAG | Center chat | `rag.BM25FAISSStore` etc. |
| Persistent KB | Hub / recommend fuse | `hybrid_search` over `document_chunks` (scan_limit 5000; BM25 prefilter when near cap / hot p95) |

### 5.22 Recommendation explain & constraint tracing

Formulations scored via `_score_and_validate` attach `Formulation.explain`:

- objectives hit / constraints miss, evidence refs, KG signals, **supply flags**,
  uncertainty, **bias-corrected** badge
- **effect_trace**: which objectives / levers / VOC (etc.) are **wired**,
  **display_only**, or unwired

UI: leaderboard “Why recommended”; Requirements panel constraint chips.

### 5.23 Workbench flywheel toggles (default off)

On the **📋 Workbench** toolbar (persisted in the project workspace):

| Toggle | Behavior |
|--------|----------|
| **Auto-loop after save** | Sync of Completed+measured rows may dispatch optimize + next-DOE suggestion; does **not** auto-write next DOE into the ledger |
| **Project dossier auto-patch** | Allowlisted events refresh dossier sections |
| **Project prediction soft-correct** | Apply `prediction_bias.mean_error` to **predicted** only; never measured / Claims / DOE |

Also: BiasTrend panel and `loop_status`. Global env still defaults off; project OR global.

### 5.24 PathWizard & flag maturity

Right-rail **PathWizard** routes formula / substitute / knowledge. Settings and
dependency flags carry `maturity` (stable / beta / experimental).

### 5.25 Product-default constraints (memo)

Still **off** unless explicitly enabled:

- `auto_loop_on_sync` / `auto_adopt_next_doe_on_loop` / `prediction_bias_soft_correct`
- `kg_relations_on_ingest`, archive TTL physical purge, stopping `suppliers_json` dual-write
- Neo4j / external vector DBs (e.g. Qdrant) — not default until metrics demand them

### 5.26 2026-10 addenda: nav slimming, project shortcuts, custom process window & constraints, tox/eco records

- **Left nav (third slimming round, 41 web entries)**: recommend first in the
  formulation group, models and surface first in the optimization group,
  research artifacts moved up in the literature group, raw materials first in
  the materials group; the 4 entries removed from the menu each have a home
  and their routes are kept — quality-ops → search hub ops tab,
  connectors → settings-center tab, agent memory → knowledge-Q&A
  page header, project sources → contract page header only. MCP tool approvals
  keep their top-level entry (5-minute approval window; expired requests are
  auto-rejected).
- **Project shortcuts**: the top-bar project dropdown's 「+」 creates a project;
  the project list's row actions and the contract page header rename and delete
  it. Permission reuses `project.write`; delete takes a knowledge-base
  disposition (delete | global, default delete) while business data is always
  deleted.
- **Custom process parameters** (contract → Custom process parameters tab):
  numeric-range or discrete-list rows; unique keys, no clash with the 4 fixed
  keys, range low < high; the DOE designer's "Fill from process window"
  includes custom numeric items.
- **Structured process constraints** (contract → Constraints tab): constraint
  rows + a `constraint_values` single-value mirror (range rows excluded); old
  single values convert to target rows in place; `derive_process_levers` /
  `check_process_window` accept custom items.
- **Substance tox/eco records** (Materials & regulations → Substance tox/eco
  records, renamed from "test records" to disambiguate from work records):
  toxicology and ecotoxicology records render SDS §11/12; EH40 seeds 348
  occupational exposure limit rows from 445 file records (64 records without
  a usable CAS go to the review queue, not into the table); GBZ 2.1 seeds
  373 rows with 19 unmatched records (SDS §8 still uses GBZ only; the OEL
  query API covers the rest). Unmatched OEL records are handled through the
  seed review queue: `GET /api/plm/regulatory/review-items` (filter by kind
  `eh40_unmatched` / `oel_unmatched`) lists them, EHS either fixes the source
  file and re-seeds (open items regenerate) or resolves an item that needs no
  further action via `POST /api/plm/regulatory/review-items/{id}/resolve`
  (resolved items survive re-seeds).
- **Revision empty-state guidance**: the product-revision dialog guides when
  the formulation-version dropdown's lineage is empty; the version field is
  optional; with no default company, document generation checks China by
  default; the revision detail page labels unlinked versions.
- **Multi-regulation** (company configuration ticks applicable regulations —
  China / EU / US first): two-level agent (company default + per-generation /
  per-export override); generation computes per regulation, export emits one
  edition per regulation; CLP / OSHA rule sets are draft and require EHS
  sign-off before production release; the gate stays fail-closed. Existing
  data backfills to GB.
- **Generic product coding** (company configuration defines the coding rule):
  category Chinese/English names, identifier digits or letters, and product
  numeric-code length; new products auto-code; the code belongs to the product
  and is shared by its revisions; old MYH codes are not auto-mapped (a manual
  legacy-code field remains); a length-rule change applies to new products only
  — existing codes are frozen, never rewritten.
- **GHS statement scope**: H statements 96 → 95 (H361E removed: verbatim
  duplicate of H361D, no rule/label/frontend reference); the English gap is
  closed except P281, now filled with the historical GHS text matching the
  Chinese wording.

---

## 6. DOE feedback & model training

This is the heart of the platform. The empirical predictor is only a prior;
real experimental data is the source of truth.

### DOE design types

| Design | Use |
|--------|-----|
| `full_factorial` | full exploration when factors are few |
| `fractional_factorial` | fewer runs at some confounding |
| `plackett_burman` | main-effect screening, fewest runs |
| `ccd` | central composite, builds a quadratic response surface |
| `lhs` | Latin hypercube, uniform space filling |
| `active` | 🧠 AI active selection — EI on the DOE grid (uses the trained surrogate; LHS fallback when sample count is below the min) |

### Training mechanism

1. Each experiment record is featurized (`features.py`) into a role-based
   composition + process vector.
2. Once a (domain, metric) accumulates ≥ `FORMUNEX_MIN_TRAIN_SAMPLES`
   (default 4) samples, a model is trained:
   - scikit-learn `RandomForestRegressor` when installed;
   - otherwise a self-contained numpy ridge regressor.
3. `predictor.predict` **blends** the trained model with the empirical prior,
   weighting the model more as samples grow (`w = n/(n+8)`).
4. **The dataset is the source of truth**: models are not pickled — they are
   rebuilt from the persisted dataset on startup, keeping persistence simple and
   reproducible.

---

## 7. Import & export

### DOE worksheet export (B3)

- In the DOE area, click **Export CSV** or **XLSX** to download a worksheet with
  blank `measured_<metric>` columns for the lab to fill in.
- API: `GET /api/doe/{plan_id}/export?format=csv|xlsx`
- XLSX requires the optional `openpyxl` (`[export]` extra); CSV uses only the
  standard library and is always available.

### Experiment-result CSV import (B3)

- In the DOE area, click **Import CSV** to upload a filled-in worksheet for bulk
  ingestion and automatic retraining.
- API: `POST /api/experiments/import-csv`
- Parsing: `measured_*` columns become measured values, `cure_temperature_c` is
  routed to the process field, unfilled rows are skipped, and Excel's UTF-8 BOM
  is tolerated.

### Formula export (F2)

The **Export ▾** menu on each leaderboard card offers:

- **Copy JSON** — copy the full formulation object to the clipboard;
- **Export CSV** — ingredient rows + predicted metrics as a table;
- **Export PDF** — a one-page report card (name, ingredient table, predicted
  metrics with uncertainty, rationale). jsPDF is lazy-loaded so it never bloats
  the initial bundle.

---

## 8. Session history (F5)

- After every completed **research, optimization, feedback or loop** run, the
  platform automatically saves a session snapshot (requirement + leaderboard +
  models + convergence curve + timestamp).
- Click the header **🕐 History** button to open the right-side drawer listing
  the last 20 sessions.
- Each entry shows the domain label, time, Top-1 formula name and score; click
  to **restore** that session state in one tap.
- A **clear** action wipes history. Data lives in browser localStorage, so it
  survives a page refresh with no backend required.

---

## 9. API reference

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/search` | multi-source retrieval (patents / literature / internet) → merged, de-duped evidence |
| POST | `/api/ingest` | upload a local file → extracted evidence chunks |
| GET | `/api/auth/status` | whether platform bearer auth is required (public) |
| GET/POST | `/api/settings` | LLM provider, model, key, base URL; `POST /api/settings/test` |
| GET/POST | `/api/settings/secrets` | grouped API keys (LLM, SerpAPI, Tavily, …) |
| GET | `/api/dependencies` | optional-package install status |
| POST | `/api/dependencies/install` | async pip install of catalogued extras |
| GET | `/api/chemical/lookup` | ingredient name → CAS / SMILES / molar mass |
| POST | `/api/formulations/manual` | validate / enrich / score a hand-entered formulation |
| POST | `/api/research/modify` | async AI formula modification (SSE) |
| POST | `/api/chat` | Q&A grounded in the loaded sources (semantic / TF-IDF re-rank → LLM answer with citations) |
| POST | `/api/intent/parse` | natural-language project brief → structured `Requirement` (LLM `complete_json` or regex fallback) |
| POST | `/api/research` | retrieve prior art + RAG + recommended formulations (accepts optional pre-loaded `sources`) |
| POST | `/api/research/deep` | async deep-research: DeepResearchEngine multi-agent pipeline (web + KB + HyDE + re-rank + cross-validation report) → returns `task_id` |
| GET | `/api/search/status` | lightweight per-source availability check (no retrieval, no network) |
| POST | `/api/ip/analyze` | per-formula novelty score, infringement-risk list, white-space hints |
| POST | `/api/doe?design=…` | generate a DOE plan (5 designs) |
| POST | `/api/doe/active` | 🧠 active-learning DOE: surrogate EI selection (LHS fallback) |
| GET | `/api/doe/{plan_id}/export?format=csv\|xlsx` | export a fill-in worksheet (blank measured columns) |
| POST | `/api/optimize` | start the async multi-objective optimizer → returns `task_id` |
| POST | `/api/process-optimize` | optimize manufacturing process parameters (Arrhenius / empirical outcome models) |
| POST | `/api/loop/iterate` | one-click self-driving loop: data → retrain → optimize → next active-learning DOE → returns `task_id` |
| **POST** | **`/api/design/inverse`** | **inverse design: target spec → Pareto set of formulations (async, §5.15)** |
| **GET/POST** | **`/api/materials`** | **search / add materials in the editable catalogue (§5.19)** |
| **POST** | **`/api/materials/substitutes`** | **ranked replacements for one slot, with per-metric Δ (§5.16)** |
| **GET** | **`/api/materials/supply-risk`** | **formulations affected by discontinued materials** |
| **POST** | **`/api/materials/availability`** | **mark a material `available` / `restricted` / `discontinued`** |
| **POST** | **`/api/qc/report`** | **upload a QC report → extracted measurements bound to an experiment (§5.17)** |
| **GET** | **`/api/qc/experiments/{id}/measurements`** | **per-measurement rows (metric, value, unit, method, spec limit)** |
| GET | `/api/tasks/{id}` | poll task progress + result (Top-N leaderboard, loop report, …) |
| GET | `/api/experiments` | list experiments (id + label) so reports and measurements can reference one |
| POST | `/api/experiments` | feed back measured results → persist + (re)train |
| POST | `/api/experiments/import-csv` | upload a filled-in worksheet → bulk-ingest + train |
| POST | `/api/experiments/workbench/campaigns` | create workbench campaign from a DOE plan |
| GET | `/api/experiments/workbench/{id}` | AG Grid ledger rows (+ `loop_status`) |
| PUT | `/api/experiments/workbench/sync` | save ledger edits; optional project/global auto-loop |
| GET | `/api/experiments/workbench/{id}/bias-trend` | prediction_bias trend (soft-correct decisions) |
| GET | `/api/kb/stats` | active/archived sources & chunks, scan pressure |
| GET | `/api/kb/quality-ops` | Hub quality aggregate (incl. hybrid p50/p95) |
| POST | `/api/kb/hybrid-search` | persistent-KB hybrid retrieval |
| POST | `/api/kb/query-test` | retrieval probe with per-channel scores |
| POST | `/api/kb/retention/purge` | archived-source retention (default dry-run) |
| GET/POST | `/api/wiki/…` | Wiki pages / graph / dossier / STORM (flag-gated) |
| **POST/GET** | **`/api/formulations/versions`** | **save a formulation version / find lineages (§5.18)** |
| **GET** | **`/api/formulations/versions/{from}/diff/{to}`** | **structured diff between two versions** |
| POST | `/api/train` | force a retrain over all stored experiments |
| GET | `/api/models` | list trained models with `n_samples`, `R²`, `cv_R²`, `RMSE` |
| **GET** | **`/api/kb/integrity`** | **orphan report over the soft references (§5.20)** |
| GET | `/api/meta`, `/api/templates/{domain}` | metadata & baseline templates |
| GET | `/health` | database, **task broker** and Datalab reachability |

Interactive docs: after starting the backend, visit `http://localhost:8000/docs`.

### Async submission and the 503 contract

Every async endpoint (`/api/research/recommend`, `/api/research/deep`,
`/api/optimize`, `/api/design/inverse`, `/api/search/stream`,
`/api/loop/iterate`, `/api/dependencies/install`) returns **202** with
`task_id` + `stream_url`, or **503** with a JSON `detail` when the task broker
is unreachable.

503 rather than 500 is deliberate, and so is the speed. Celery's `.delay()` does
not fail cleanly with Redis down: it retries the *result backend* for roughly 19
seconds and then raises `RuntimeError: Retry limit exceeded … The Celery
application must be restarted`, which reaches the browser as a plain-text 500
that no JSON error handler can read. The API now probes the broker with a
one-second TCP connect first, so the caller gets an actionable reason in ~30 ms.

**A refused submission is delayed work, not lost work.** The durable outbox row
is written *before* dispatch, so `dispatcher.recover_stalled` re-enqueues the
job once the broker returns.

### Health check

```bash
curl -s localhost:8000/health
```

```json
{"status": "ok",
 "database":    {"ok": true,  "scheme": "sqlite"},
 "task_broker": {"required": true, "reachable": true},
 "datalab":     {"required": false, "reachable": false}}
```

- `status` is `degraded` — not failed — when a dependency is unreachable, and
  the endpoint still answers **200**. The container healthcheck asserts 200, and
  restarting the API would not bring Redis back.
- `task_broker.required` is `false` in eager mode, where tasks run in-process.
  **If you run a worker container and see `required: false`, tasks are not
  reaching it** — see §11.

### Inverse design example

```bash
curl -X POST localhost:8000/api/design/inverse -H 'content-type: application/json' -d '{
  "requirement": {"domain": "anticorrosion_coating", "substrate": "carbon_steel"},
  "targets": {
    "hard": [
      {"metric": "salt_spray_hours", "op": "ge", "value": 1000},
      {"metric": "voc_gpl",          "op": "le", "value": 250}
    ],
    "soft": [
      {"metric": "cost_cny_per_kg",  "weight": 0.5, "direction": "minimize"},
      {"metric": "salt_spray_hours", "weight": 0.5, "direction": "maximize"}
    ]
  },
  "population": 48, "generations": 30
}'
# → 202 {"task_id": "...", "stream_url": "/api/tasks/.../stream"}
# Result: candidates (structurally distinct), pareto_frontier_ids, tradeoff,
#         rejected_infeasible — check that last one if the front comes back thin.
```

### Substitution example

```bash
curl -X POST localhost:8000/api/materials/substitutes -H 'content-type: application/json' -d '{
  "formulation": { "...": "a Formulation object" },
  "slot_index": 1,
  "requirement": {"domain": "anticorrosion_coating"},
  "limit": 5
}'
# Each candidate carries metric_deltas AND delta_confidence.
# delta_confidence: "cost_only" means performance metrics have no resolution
# here — install ".[science]" before reading anything into an unchanged number.
```

### Self-driving loop request example

```bash
curl -X POST localhost:8000/api/loop/iterate -H 'content-type: application/json' -d '{
  "domain": "anticorrosion_coating",
  "substrate": "carbon_steel",
  "salt_spray_hours": 800,
  "film_weight_gsm": 70,
  "cure_temperature_c": 80,
  "cleaning_efficiency": 0,
  "voc_limit_gpl": 250,
  "ph_target": null,
  "notes": "",
  "objectives": [],
  "optimize_iterations": 24,
  "n_suggest": 4
}'
# → {"task_id": "...", "stream_url": "/api/tasks/.../stream", "status_url": "/api/tasks/..."}
# Poll /api/tasks/{id}; the result includes optimization.top_formulations,
# next_doe (with ai_suggested flags) and rmse_by_metric.
```

### Intent-parse example

```bash
curl -X POST localhost:8000/api/intent/parse -H 'content-type: application/json' -d '{
  "text": "Develop a waterborne epoxy anti-corrosion coating for automotive underbody, salt spray ≥ 1000 h, cures at 120 °C"
}'
# → {"requirement": {"domain":"anticorrosion_coating", "salt_spray_hours":1000, ...},
#    "engine": "offline-heuristic", "extracted_fields": ["domain","salt_spray_hours",...]}
```

### Custom multi-objective optimization example

```bash
curl -X POST localhost:8000/api/optimize -H 'content-type: application/json' -d '{
  "requirement": {
    "domain": "anticorrosion_coating",
    "salt_spray_hours": 800,
    "objectives": [
      {"metric": "salt_spray_hours", "weight": 0.7, "direction": "maximize"},
      {"metric": "cost_cny_per_kg",  "weight": 0.3, "direction": "minimize"}
    ]
  },
  "iterations": 30
}'
```

---

## 10. Install & run

### One-click install (Linux/macOS)

```bash
./scripts/install.sh
cp .env.example .env    # intranet: FORMUNEX_API_AUTH_ENABLED=false
```

### Local (no Docker, fully offline)

```bash
# Backend
cd backend
python3 -m venv .venv              # create once (required on Debian/Ubuntu with PEP 668)
source .venv/bin/activate          # Linux/macOS  (.venv\Scripts\activate on Windows)
pip install -e ".[dev]"
pytest -q                          # 1040+ tests, all offline
uvicorn app.main:app --reload --reload-exclude .venv  # http://localhost:8000/docs

# Frontend (separate shell)
cd frontend
npm install
npm run dev                        # http://localhost:5174
```

For production or Docker deployments, use the locked runtime deps:

```bash
pip install -r requirements.txt
pip install -e . --no-deps
```

### Docker

```bash
cp .env.example .env               # LLM keys, FORMUNEX_API_TOKEN, Tavily/SerpAPI, …
docker compose up -d --build       # redis + backend + worker + frontend
docker compose exec backend alembic upgrade head    # migrate an existing database
curl -s localhost:8000/health      # verify before using the UI
docker compose --profile heavy up  # also start LAMMPS / HTPolyNet engines
```

**Intranet / lab:** set `FORMUNEX_API_AUTH_ENABLED=false` in `.env` so Settings loads
without a platform bearer token. **Public:** keep default `true`, set
`FORMUNEX_API_TOKEN`, and enter it in Settings → API access token (see
`docker-compose.yml`) or Settings → API access token.

Host-network overlay: `docker compose -f docker-compose.yml -f docker-compose.host.yml up -d`

Enterprise ELN: `docker compose -f docker-compose.yml -f docker-compose.eln.yml up` — see `deploy/eln/README.md`.

> **Do not mix compose invocations.** The base file puts services on a bridge
> network; the host overlay uses `network_mode: host`. Switching between them
> recreates only the services whose definition changed and leaves the rest on
> the old network, so the backend stops resolving `redis` and any
> `localhost:`-style URL starts pointing at the container itself. The symptom is
> `task_broker.reachable:false` and `datalab.reachable:false` **at the same
> time**, with `docker compose ps` showing everything "Up" — and a redis row
> with no published ports, which is the tell. `docker compose down` then `up -d`
> realigns everything.

### Migrations

Migrations are Alembic, and `alembic.ini` ships inside the image, so a deployed
database can be migrated in place:

```bash
docker compose exec backend alembic upgrade head
curl -s localhost:8000/api/kb/integrity     # confirm afterwards
```

Outside production, `Base.metadata.create_all` creates any **missing tables** at
startup — but it never alters an existing one. A database created before a
column-type change therefore needs the migration; `create_all` will not do it
for you.

### Image build notes

The image installs no apt packages by default. Every package it installs
resolves to a prebuilt wheel on linux/x86_64/cp311; the one sdist-only
dependency (jieba) is pure Python and builds with no compiler present. If you
need a toolchain in the image — installing an sdist-only optional extra at
runtime through the dependency installer — opt in:

```bash
docker compose build --build-arg INSTALL_BUILD_TOOLCHAIN=true backend
```

The frontend image runs `npm ci`, which refuses an incomplete lockfile where
`npm install` would silently repair one. If it fails with
`Missing: … from lock file`, regenerate and commit:

```bash
cd frontend && npm install --package-lock-only
```

---

## 11. Configuration

All settings are environment-driven (prefix `FORMUNEX_`) with safe offline
defaults.

| Variable | Default | Purpose |
|----------|---------|---------|
| `FORMUNEX_LLM_PROVIDER` | `anthropic` | active provider: `anthropic`/`openai`/`gemini`/`xai`/`groq`/`deepseek`/`qwen`/`moonshot`/`minimax` |
| `FORMUNEX_LLM_MODEL` | `claude-sonnet-4-6` | LLM model for the active provider |
| `FORMUNEX_<PROVIDER>_API_KEY` | empty | LLM vendor keys (managed in Settings) |
| `FORMUNEX_API_AUTH_ENABLED` | `true` | platform Bearer auth on `/api/*`; use `false` for intranet dev |
| `FORMUNEX_API_TOKEN` | empty | platform access token (not an LLM key); required when auth is on in production |
| `FORMUNEX_SERPAPI_API_KEY` | empty | SerpAPI (Scholar / Patents) |
| `FORMUNEX_TAVILY_API_KEY` | empty | Tavily semantic search |
| `FORMUNEX_LLM_BASE_URL` | provider default | override the OpenAI-compatible base URL |
| `FORMUNEX_SEARCH_LIMIT_PER_SOURCE` | `5` | max results fetched per source type |
| `FORMUNEX_RAG_BACKEND` | `auto` | RAG store: `auto` (embedding if installed, else TF-IDF) / `embedding` / `tfidf` |
| `FORMUNEX_ENRICH_COMPOUNDS` | `false` | backfill SMILES/molar-mass via PubChem on startup (needs `intel` + network) |
| `FORMUNEX_DB_URL` | `sqlite:///./data/formunex.db` | **Production must use Postgres** (`postgresql://user:pass@host:5432/formunex`). SQLite is for single-process / CI only — multi-worker write load risks `database is locked`. |
| `FORMUNEX_REDIS_URL` | `redis://localhost:6379/0` | Celery broker |
| `FORMUNEX_REDIS_PROTOCOL` | `2` | Redis wire protocol: `2` works with any Redis (Windows ports, Ubuntu 20.04); `3` needs Redis ≥ 6.0 |
| `FORMUNEX_CELERY_EAGER` | `true` | run tasks in-process without a broker. **Operator-owned** — see below |
| `FORMUNEX_OPTIMIZE_ITERATIONS` | `24` | optimization iterations |
| `FORMUNEX_TOP_N_FORMULAS` | `5` | leaderboard size |
| `FORMUNEX_MIN_TRAIN_SAMPLES` | `4` | min samples before training a metric's model |
| `FORMUNEX_AUTO_RETRAIN` | `true` | retrain automatically on new experiments |
| `FORMUNEX_AUTO_LOOP_ON_SYNC` | `false` | auto closed-loop after ledger sync (or project checkbox OR) |
| `FORMUNEX_AUTO_ADOPT_NEXT_DOE_ON_LOOP` | `false` | auto-adopt next DOE after loop (experimental; default off) |
| `FORMUNEX_PREDICTION_BIAS_SOFT_CORRECT` | `false` | soft-correct predicted metrics (or project checkbox OR) |
| `FORMUNEX_KB_SEARCH_SCAN_LIMIT` | `5000` | max chunks scanned by persistent hybrid_search |
| `FORMUNEX_KB_HYBRID_ANN_GATE_P95_MS` | `800` | enable BM25 prefilter when hybrid p95 exceeds this |
| `FORMUNEX_KB_RELEVANCE_SHADOW` | `false` | `false` = topicality enforce; `true` = shadow-only stats |
| `FORMUNEX_MATERIALS_SUPPLIERS_JSON_DUAL_WRITE` | `true` | dual-write suppliers_json (compat window) |
| `FORMUNEX_WIKI_ENABLED` / `WIKI_EMBED_ENABLED` / `WIKI_PAGE_GRAPH_ENABLED` | see `.env.example` | Wiki compiled memory / embed dual-track / page graph |
| `FORMUNEX_FULLTEXT_ENRICH` | `false` | **The current full-text switch**: upgrade abstract-level hits to full text and index them (requires network; false by default to keep tests offline) |
| `FORMUNEX_PATENT_PREFER_HTML` | `true` | Take patent text from the Google Patents landing page — one request, ~0.7 s, and no OCR. Set false to prefer the PDF |
| `FORMUNEX_PDF_LOCAL_OCR` | `false` | Per-page OCR inside the local layout parser (pymupdf4llm). ⚠️ **The library defaults this on**, and that tier runs on every PDF ahead of everything else — so with Tesseract on the host, every page is OCR-ed first. Measured on a 6-page image-only PDF: 16.7 s on / 0.8 s off. Scans are unaffected: a document with no text layer is detected and handed to RapidOCR or MinerU. A **mixed** document (a few text pages plus scans) is not "a scan" — that test is `all()` over pages — so its text-layer-less pages are escalated to MinerU per page instead; with MinerU off, one warning per document names how many pages could not be read |
| `FORMUNEX_MINERU_ENABLED` | `false` | **Cloud MinerU escalate** (mineru.net). Default off — pages upload to a third party. Enable with token for dense tables/formulas/scans. Uses the built-in `hybrid` cascade (`mineru-open-sdk`), **not** `langchain-mineru` / Chroma |
| `FORMUNEX_MINERU_API_KEY` / `MINERU_TOKEN` | — | Precision extract token from [mineru.net](https://mineru.net/apiManage/token) |
| `FORMUNEX_MINERU_PAGE_TIMEOUT_S` | `90` | Wait budget for a **single** escalated page. Escalation is sequential, so timeouts accumulate page by page — raising this on a bad connection only doubles the waiting |
| `FORMUNEX_MINERU_TIMEOUT_S` | `300` | Wait budget for sending a **whole** scanned document to MinerU |
| `FORMUNEX_MINERU_MAX_PAGE_FAILURES` | `3` | Consecutive page failures before escalation is abandoned for that document (`0` disables). Without it one unreachable network is retried per page, paying the full timeout up to 20 times |
| `FORMUNEX_EMBEDDING_MODEL` | _(empty)_ | Optional pin. Empty = bilingual MiniLM + bge-small-zh routing. For a **single multilingual space** (zh query ↔ en chunks without translate), set `BAAI/bge-m3` or `Qwen/Qwen3-Embedding-0.6B`, install `pip install -e '.[embedding]'`, then **Rebuild index**. Do not change this casually — stale vectors are excluded until reindex |
| `FORMUNEX_KB_HYBRID_FUSION` | `weighted` | `weighted` (α·BM25+(1-α)·cosine) or `rrf` (reciprocal rank fusion A/B). Default stays weighted |
| `FORMUNEX_CHAT_CROSS_ENCODER_ENABLED` | `false` | Optional local CE on chat after session BM25 recall. Default off (CPU latency); failures keep BM25 order |
| `FORMUNEX_WIKI_STORM_CLAIM_REGENERATE` | `false` | When claim_check sets `needs_regenerate`, rewrite one failed section once (still `draft_not_claims`) |

### Settings saved in the UI vs. settings set by the deployment

Most flags in the Settings dialog are promoted into the process environment at
startup, deliberately outranking whatever the container was created with — a
value the user saved last is the most recent explicit intent, and a stale
`env_file` key should not beat it.

**`FORMUNEX_CELERY_EAGER` is the exception.** It decides whether tasks go to
the broker or run inside the API process — that is, whether the worker container
does anything at all — so it belongs to the operator, not to a UI toggle. When
the deployment sets it, a UI-saved value is ignored and the mismatch is logged
rather than applied silently. With no deployment value there is nothing to
overrule, so a single-process install can still choose eager mode from the UI.

If `/health` reports `task_broker.required:false` while you are running a worker
container, that is exactly this situation: check the backend logs for the
ignored-value warning, and turn "run tasks synchronously" off in Settings to
clear the stored value.

### Data persistence (B5)

- Experiment records are persisted in a SQL database (SQLite by default,
  zero-config, file-backed).
- On startup, a legacy `experiments.json` is **automatically migrated** into
  SQLite (idempotent; the original file is kept as an audit trail).
- **Production DB (P1 #25):** set `FORMUNEX_DB_URL=postgresql://...` for any
  multi-process / multi-host deploy. SQLite remains the default for local CI
  and single-worker demos; the API logs a warning when
  `FORMUNEX_ENVIRONMENT=production` still points at SQLite. No code changes
  are required to switch engines — SQLAlchemy URL only.
- **SQLite runs in WAL journal mode** (set on every connection). Under the
  default `delete` mode readers and writers are mutually exclusive, so one long
  ingest transaction blocks every unrelated read until it commits — that is what
  "database is locked" looks like in production. WAL allows any number of
  readers concurrently with one writer.
  ⚠️ **Back up `formunex.db-wal` and `formunex.db-shm` alongside the `.db`
  file**: committed data that has not been checkpointed yet lives in `-wal`, so
  copying the `.db` alone loses it. In-memory and network-hosted databases
  cannot do WAL and silently keep the old mode rather than failing to start.

---

## 12. Enabling the real engines

Native DOE and the built-in numpy optimizer are the **default**. Install the
corresponding extras on a capable machine only when you want those engines;
adapters switch over automatically — no code change. Missing `baybe` /
`optuna` / `botorch` does **not** block core recommend / native DOE / native
optimize.

```bash
pip install -e ".[llm]"          # Claude + OpenAI + Gemini SDKs (covers all 9 providers)
pip install -e ".[science]"      # scipy, scikit-learn, RDKit, ChemFormula, thermo
pip install -e ".[optimize]"     # optuna (optional CPU multi-objective; native is default)
pip install -e ".[bo]"           # BoTorch + gpytorch + torch CPU (optional GP qNEHVI)
pip install -e ".[intel]"        # paper-qa, molbloom, pubchempy, ddgs (arXiv is a core dependency; Semantic Scholar is queried over HTTP)
pip install -e ".[patents]"      # patent-client for online USPTO/EPO search — ⚠️ replaces the pinned httpx and pypdf; read the warning below first
pip install -e ".[file_ingest]"  # markitdown, pypdf, python-docx (local file upload)
pip install -e ".[embedding]"    # sentence-transformers → embeddings (session RAG / KB hybrid)
pip install -e ".[color]"        # colour-science (CIELAB / CIEDE2000)
pip install -e ".[colbert,crag]"   # ColBERT index + LangGraph CRAG pipeline
pip install -e ".[heavy]"        # torch, deepchem, transformers (MoLFormer), ase
pip install -e ".[export]"       # openpyxl (XLSX export; CSV needs nothing)
```

> ⚠️ **`.[patents]` replaces two pinned dependencies — which is why it is not part of `.[intel]` and nothing installs it for you.** `patent-client` (online USPTO/EPO patent search through its SDK) requires `httpx<0.28` and `pypdf<5.0`. pip resolves that conflict by *replacing* the pins instead of failing: `httpx 0.28.1→0.27.2` and `pypdf 6.19.0→4.3.1` — and pypdf is the parser that reads every uploaded and downloaded PDF; 4.3.1 has 49 published advisories (crafted-PDF denial of service). pip does not error and `pip check` reports nothing wrong. Install it only into an environment that does not parse untrusted PDFs, and only if you need the SDK: patent *full text* comes from Google Patents landing pages, and Google Patents / SerpAPI / the built-in seed corpus keep answering without it. The Docker image does not include it, Settings → Dependencies refuses it, and CI checks both (`.github/workflows/ci-deps.yml`: per-extra drift, and the Dockerfile's whole extras list resolved against `requirements.txt`).


After installing the `science` extra:
- property prediction adds **8 RDKit descriptors** (MolWt, MolLogP, TPSA,
  HBD/HBA, rotatable bonds, FractionCSP3, RingCount) and uses Fox/Mooney for
  Tg / viscosity;
- PVC / CPVC are computed from `density_gcm3` + `oil_absorption` in the
  knowledge base;
- model training upgrades from numpy ridge to scikit-learn random forest (with
  ensemble uncertainty);
- stoichiometry validation switches to ChemFormula for exact computation;
- `thermo` grounds the VOC g/L calculation in a real mixture density.

The optimizer auto-selects the strongest engine installed (**BoTorch** →
**Optuna** → numpy), grounded Q&A routes chemistry questions to
**ChemCrow** and others to **paper-qa**, both falling back to TF-IDF + the
configured LLM, and the RAG store upgrades from TF-IDF to semantic embeddings
when `sentence-transformers` is installed (see §5.9). The IP analyser and NL
intent parser share `complete_json()` against the configured LLM. None of these
are required — each lights up automatically.

### NotebookLM (fully removed, 2026-10-30)

The NotebookLM retrieval source has been fully removed with the backend removal: API, service, configuration, project-workspace binding and library tab are all gone, and old project binding fields are no longer read. Use literature / internet search or local file upload instead.

---

## 12b. RAG foundation (parse → chunk → hybrid → chat / Wiki)

Garbage in, garbage out: **document parse quality bounds retrieval**. FormuNex’s
single ingest path is `parsing.parse_document` → **hybrid** (local layout, then
per-page MinerU cloud escalate when enabled, then RapidOCR). Do **not** add a
parallel `langchain-mineru` + Chroma + `RetrievalQA` stack — that forks the
vector store and breaks `[^n]` / Claims Raw-only contracts.

Recommended ops (not silent code defaults):

1. Enable MinerU only when you accept third-party upload (`FORMUNEX_MINERU_ENABLED` + token).
2. For multilingual cosine without query-translate: set `FORMUNEX_EMBEDDING_MODEL`
   to `BAAI/bge-m3` or `Qwen/Qwen3-Embedding-0.6B`, install `.[embedding]`, **Rebuild index**.
3. Chat / STORM persistent KB retrieval shares `hybrid_search_scored` (α / optional RRF).
4. STORM reports remain `draft_not_claims` — claim regenerate never feeds DOE bounds.

## 13. FAQ & scope notes

**Q: Settings tabs are empty / 401 errors?**
Default `FORMUNEX_API_AUTH_ENABLED=true` blocks `/api/settings` without a bearer
token. Set `FORMUNEX_API_AUTH_ENABLED=false` for intranet, or enter the platform
token matching `FORMUNEX_API_TOKEN` at the top of Settings (distinct from LLM keys).

**Q: Does it work without an API key?**
Yes. Everything runs end-to-end fully offline; only LLM research synthesis is
replaced by the knowledge-base rule engine. The NL intent parser and IP
analyser also have deterministic offline fallbacks.

**Q: Are the predicted performance numbers trustworthy?**
The offline numbers are **engineering-reasonable screening estimates**, not
lab-validated specifications. Feed real DOE results back so data-driven models
progressively supersede the empirical prior — or run the **🔄 Self-Driving
Loop** to do it in one click per round.

**Q: Where is the 3D simulation?**
Each leaderboard card has a **3D molecular-viewer panel**, currently a
placeholder + data contract (it lists the SMILES-bearing components to be drawn
via 3Dmol.js). Full WebGL rendering — and reactive-MD trajectory rendering via
HTPolyNet/LAMMPS (Docker `heavy` profile) — ship in a later upgrade. The
optimization convergence chart is shown in the Optimization and Self-Driving
Loop modals.

**Q: Is patent retrieval a live online crawl?**
Yes — set `FORMUNEX_FULLTEXT_ENRICH=true`. It falls back to the per-domain
offline seed corpus when nothing is retrievable.

Patent full text comes from the **Google Patents landing page**
(`/patent/{number}/en`), reading its `abstract` / `description` / `claims`
sections. That covers US / CN / EP / WO / JP, and non-English publications
carry a machine translation alongside the original.

> Earlier versions fetched PDFs directly from USPTO / EPO. **All three of those
> URLs are now dead** — pdfpiw.uspto.gov resets the connection,
> `patents.google.com/…/pdf` returns an HTML landing page, and the EPO
> publication server returns an error page — so that path is gone, and
> `patent_client` is no longer needed for patent retrieval.

The IP analyser uses the same retrieval.


**Q: Where is session history stored?**
Browser localStorage (up to 20 entries), never uploaded to the server.
Experiment data is persisted in the backend SQLite/Postgres database.

**Q: Inverse design returned very few candidates, or none.**
Check `rejected_infeasible` in the response. A large count against a thin front
means the hard constraints are close to (or past) what the search space can
satisfy — relax one, or move it to a soft objective. A front that is non-empty
but structurally uniform usually means the swappable roles are over-constrained.

**Q: A substitution shows an identical salt-spray number for every candidate.
Does that mean the swap is safe?**
No — and this is the failure mode most worth understanding. Check
`delta_confidence`. When it reads `cost_only`, RDKit is absent, molecular
features are empty, and the predictor genuinely cannot distinguish same-role
materials on performance; identical numbers are the model having no resolution,
not evidence of equivalence. Cost, VOC and PVC-class deltas remain meaningful.
Install `".[science]"` to make performance deltas informative.

**Q: A feature returned 503 mentioning Redis. Did I lose the job?**
No. The outbox row is written before dispatch, so the submission is recorded and
re-enqueued once the broker is reachable. Start redis and the worker
(`docker compose ps` to see which is down).

**Q: The UI shows a bare `/api/... -> 502` with no explanation.**
That response did not come from the API — FastAPI errors carry a JSON `detail`
that the UI renders verbatim. A bare `path -> status` means the body was not
JSON, which points at the reverse proxy: nginx returns 502 when it cannot reach
the backend at all. Check `docker compose ps` and `curl localhost:8000/health`
directly, bypassing the proxy.

**Q: `docker compose ps` shows the worker as `unhealthy`.**
On current versions it should not. The worker shares the backend image and used
to inherit its HTTP healthcheck against `:8000`, a server the worker never runs,
so it reported unhealthy permanently. It now answers `celery inspect ping` —
which also proves the broker connection. If you still see it, rebuild.

---

> This document corresponds to the current FormuNex branch.
>
> The largest recent change is not a feature but the removal of a constraint.
> Formulation reconstruction could only rescale the weight percentages of a
> hardcoded template, so which materials appeared was fixed and only their
> proportions varied. That one limitation is why inverse design, material
> substitution and a meaningful Pareto front were all out of reach at the same
> time. Making composition searchable — an editable material catalogue plus a
> formulation genome — put all three within reach of the same foundation, and
> **🎯 Inverse Design** (§5.15), **🔁 Material Substitution** (§5.16) and true
> multi-front Pareto ranking followed from it.
>
> Alongside them: per-measurement results with unit, method and specification
> limit, fed by **📄 QC report** ingestion (§5.17); formulation revision
> lineage with structured diffs (§5.18); an editable material catalogue
> (§5.19); and a referential-integrity check for the references that cannot
> carry a database constraint (§5.20).
>
> All of it sits on the earlier platform: a three-pane layout
> (Sources / Research / Actions) with a ten-button Actions toolbar, multi-LLM
> support across nine providers, multi-source research (patents / literature /
> internet / local files), RAG-grounded Q&A with
> semantic-embedding upgrade, auto-detected intelligence engines
> (BoTorch/Optuna optimization, active-learning DOE, ChemCrow/paper-qa
> Q&A, PubChem enrichment, thermo-grounded VOC, Fox/Mooney rheology,
> CIELAB/ΔE₀₀ color, PVC/CPVC, IP novelty analysis, ✨ NL intent parser),
> multi-objective optimization, cost/sustainability scoring, confidence
> intervals, DOE import/export, SQL persistence, convergence chart, model
> dashboard and session history.
>
> Verified at **1040+ backend tests** and frontend vitest, all offline.

# FormuNex Quick Start (10 minutes)

FormuNex is an R&D + PLM platform in one: literature and patent insight → requirement contract → formulation design → DOE / Bayesian optimization → GB 30000 compliance → SDS / TDS / COA / labels → freeze and release. This guide walks you through one minimal loop. See the User Guide for full details.

## Prerequisite: start the platform

```bash
# Terminal 1: backend (from the repo backend/ directory)
uvicorn app.main:app --reload --reload-exclude .venv  # http://localhost:8000

# Terminal 2: frontend (from the repo frontend/ directory)
npm run dev                                            # http://localhost:5174
```

Open http://localhost:5174 and log in. No LLM key is fine: the platform shows an "offline degraded mode" banner, and features that work offline (recommendation, DOE, optimization) keep working, while deep research, online search and cross-source verification need a key. Document generation is async: run a Celery worker, or start the backend with `FORMUNEX_CELERY_EAGER=true` for local development.

> First-time deployments also offer a desktop setup wizard (`/setup`: local Docker stack or a remote server, regulation library, admin account). Team deployments just need a login.

## Step 0 · Overview

The UI is a light workbench: the **left nav** groups features by R&D workflow, the **top project selector** sets the current project, and most pages filter by project. Pick a project first; without one, most pages only show an empty state.

Left nav (web: 41 second-level entries + dashboard):

| Group | Entries |
|---|---|
| My Workspace | Dashboard |
| Knowledge Search | Search, Sources & Tree, Knowledge Q&A, Retrieval Evidence Audit |
| Literature & Research | Literature Library, External Search & Deep Research, Research Artifacts, Example Drafts, Patent / IP Analysis, Wiki Knowledge Base |
| R&D Requirements | PathWizard, Projects & Spec Contracts |
| Formulation & Lab | AI Formulation Recommendation, Formulation Editor, DOE Design, Lab Task Records, Measurement & Review, Material Substitution, Inverse Design, Methods & Instruments, External ELN |
| Optimization & Analysis | Surrogate Models, Response Surfaces, Uncertainty Diagnosis, Optimization Loop, Formulation Properties, Rheology Fitting |
| Documents & Release | Products & Revisions, Formulation DB Mappings, Label Designer, Regulatory Assistant |
| Materials & Regulations | Substances & Regulatory Data, Raw Materials, Substance Tox/Eco Records, Material Knowledge Graph |
| System Admin | MCP Tool Approvals, Users & Roles, Company Settings, Settings Center, Audit Log, Org Dashboard |

![Overview · Dashboard](./images/01-overview.png)

The top bar also has global search, a "+" next to the project selector to create a project (needs `project.write`), a task center, a to-do badge (measurements awaiting review, linking to Measurement & Review), and a ZH / EN switch.

## Step 1 · Create a project and set the requirement contract

Click "+" in the top bar to create a project (or go to **R&D Requirements → Projects & Spec Contracts**), fill in the contract tabs, and click **Save**:

- **Application**: product domain, substrate, and more;
- **Target Contract**: optimization targets, weights, directions;
- **Process Window**: manufacturing parameter ranges (cure temperature, cure time, dispersion speed, film thickness, and more);
- **Custom Process Parameters**: product-specific numeric ranges or discrete lists;
- **Process Constraints**: structured constraint rows;
- **Compliance Red Lines**: banned / restricted substances.

![Projects & Spec Contracts](./images/08-nl-intent.png)

> ✨ NL Intent is currently **API-only** (`POST /api/intent/parse`): send a one-sentence brief such as "Develop a waterborne epoxy anti-corrosion coating for automotive underbody, salt spray ≥ 1000 h, cures at 120 °C" and the API extracts domain, substrate, salt-spray hours, VOC limit, cure temperature and more (LLM when configured, otherwise a deterministic regex fallback). Fill the returned fields into the contract form. The contract page has no parse input box yet.

## Step 2 · Search sources and ask them questions

**Knowledge Search → Search** is hybrid retrieval (BM25 / cosine / hybrid) over the project knowledge base; you can upload local files (PDF / DOCX / XLSX / PPTX / HTML / images).

![Knowledge Search](./images/02-search.png)

**Literature & Research → External Search & Deep Research** is a hub page with tabs instead of the old three columns: **External Search** (patent / literature / web multi-source search), **Source Q&A** (grounded Q&A over selected sources, every answer carrying citation tags), **Deep Research** (multi-source comprehensive research flow), and **STORM Long-form** (long-form writing with citations). Offline, patent search returns a curated seed corpus; literature and web search need the matching optional dependencies.

![Research · Source Q&A](./images/03-research.png)

**Knowledge Search → Knowledge Q&A** is streaming Q&A over the project knowledge base, with sessions persisted per project. The "+" beside the input picks chat skills, Connectors, literature-synthesis mode and MCP servers (up to 4); typing `/` quickly picks an enabled skill. MCP write tools need per-session approval, not a permanent grant.

## Step 3 · Choose your LLM (Settings Center)

**System Admin → Settings Center** (needs `admin.llm`) has tabs for Models, API Keys & Search Routing, API Access Tokens, Skills, Environment Variables, Dependency Management, KB / KG Maintenance, and Connectors. Pick a model provider and fill in its key on the Models tab; enable skills on the Skills tab; import MCP servers on the Connectors tab (disabled by default).

![Settings · Models](./images/04-settings.png)

Do not confuse the two kinds of keys: the platform API access token (`FORMUNEX_API_TOKEN`) protects `/api/*` with Bearer auth; LLM / search API keys (DeepSeek, Tavily, and others) power LLM Q&A and online search.

## Step 4 · Generate recommended formulations

**Formulation & Lab → AI Formulation Recommendation**: the **Generate** tab creates recommendations from the contract (high-active / baseline / lean variants, scored and ranked by target metrics), then automatically switches to the **Leaderboard** tab. Expand a card for the ingredient table, predicted metrics with uncertainty (PVC / CPVC, Tg, relative viscosity, optional lab_L/a/b + ΔE₀₀), why it was recommended, and the **🔍 IP Compliance Analysis** button.

![Recommended formulations · Leaderboard](./images/05-recommend.png)

## Step 5 · Inverse design: start from the target

**Formulation & Lab → Inverse Design**: given target performance, search backwards for a formulation. Fill **hard constraints** on the left (must-haves, e.g. salt spray ≥ 1000 h, VOC ≤ 250 g/L, cost ≤ 40 CNY/kg) and **soft objectives** on the right (weights, e.g. maximize salt spray 0.5, minimize cost 0.5), set population and generations, and click **Start inverse design**. When the run finishes, it returns mutually distinct candidates on the Pareto front, which can be saved as formulation versions.

![Inverse design · hard constraints & soft objectives](./images/10-inverse-design.png)

> "Seed initial population with LLM" is checked by default; without an LLM key, uncheck it and run pure numerical optimization.

## Step 6 · Material substitution & supply risk

**Formulation & Lab → Material Substitution**: pick a formulation source (project baseline or a saved formulation version), name the ingredient to replace, and click **Find Substitutes**. Candidates pass a four-layer funnel: in-library predicted deviation → literature / knowledge-base evidence → web structural similarity (PubChem) → SureChEMBL patent chemical similarity → AI / rule expansion; only in-library items carry per-metric deltas (Δ), and candidates are ranked by composite score.

![Material substitution page](./images/11-substitution.png)

## Step 7 · Generate a DOE, run experiments, feed results back

**Formulation & Lab → DOE Design**: on the **Designer** tab pick a design type (orthogonal, central composite CCD, Plackett-Burman, mixture, D-optimal, or 🧠 AI active selection), click **Fill from process window** to center factors on the contract setpoints, and click **Generate design**. AI-selected rows are highlighted purple.

![DOE Design](./images/06-doe.png)

**Formulation & Lab → Lab Task Records**: dispatch tasks on the **Lab Tasks** tab; fill actual parameters and measured metrics on the **Lab Ledger** tab (Excel paste supported) and click **Save ledger & sync**. On the **Measurement & Review** tab you can upload a QC report for automatic parsing instead of typing by hand; results only enter optimization after another person reviews and approves them.

![Measurement & Review · report parsing](./images/12-qc-report.png)

Once a (domain, metric) pair accumulates ≥ `min_train_samples` samples (default 4), the platform trains a data-driven model automatically; the more measured data, the higher its weight (blend weight `w = n/(n+8)`).

## Step 8 · Optimization loop and self-driving iteration

**Optimization & Analysis → Optimization Loop** has tabs for **Pareto & Freeze** (default), **Point Review**, and **Loop Iteration**. The optimizer auto-picks the strongest installed engine (BoTorch → Optuna → built-in numpy UCB), samples the design space, validates each candidate stoichiometrically, and scores it with the empirical / data hybrid predictor. After the Pareto front and top formulations appear, review candidates one by one on the Point Review tab; a frozen formulation goes to Step 10 for documents and release.

![Optimization · Pareto & freeze](./images/07-optimize.png)

On the **Loop Iteration** tab, click **Run one loop iteration**: in a single async task the platform pulls all measured records for the current product domain, retrains the data-driven models, runs the optimizer, and uses the freshly trained surrogate to compute EI over the DOE grid, proposing the next batch of active-learning experiments. The page shows engine, sample counts, R² / RMSE cards and a convergence chart; the next DOE batch exports in one click, and the loop continues across rounds.

![Self-Driving Loop · loop iteration](./images/09-loop.png)

## Step 9 · Formulation revision history

Every save is an immutable revision record. On the formulation editor or the lineage page (`lab/lineage/<lineage-id>`) versions list their change notes, operator and creation time; pick two versions to compare ingredient and metric changes.

![Formulation revision history](./images/13-version-history.png)

## Step 10 · Documents & release (after freezing)

**Documents & Release → Products & Revisions**: create a product (auto-coded by the company coding rules; the code belongs to the product and is shared by its formulation revisions); create a revision and link a formulation version (an empty-lineage empty state guides you, the version field is optional, the dropdown filters by project while unscoped global versions are still listed). Generate SDS / TDS / COA / GHS label documents: China regulations are checked by default, and after the company ticks applicable regulations (China / EU / US) each regulation is computed and exported separately. SDS §11/12 come from **Materials & Regulations → Substance Tox/Eco Records**.

On a revision's **Release Gate** tab you see the G1–Gn checks; then review, sign off (sign or publish permission), and release. The gate is fail-closed: the CLP / OSHA rule sets are draft and only release published documents after EHS sign-off. The **Label Designer** validates GB 15258 completeness before producing a label; the **Regulatory Assistant** looks up regulation texts.

## Troubleshooting

- **Empty pages**: check the top bar — a project must be selected first; most pages filter by project and show only an empty state without one.
- **"Offline degraded mode" banner**: no LLM API key is configured. Deep research and online search are unavailable; recommendation, DOE and optimization still work.
- **Document generation stuck**: documents are async tasks. Make sure a Celery worker is running, or start the backend with `FORMUNEX_CELERY_EAGER=true` for local development.
- **Release gate G5 failing**: the classification rule set is draft and awaits EHS sign-off before it can back published documents. That is not an operator error.
- **Changing company info, coding rules or applicable regulations**: **System Admin → Company Settings** (needs `admin.companies`). The first company automatically becomes the default; document issuing units and SDS §1 supplier info come from it.

## Next steps

- Full feature details: User Guide §5 (optimization, cost and PVC / CPVC, rheology and color, prediction intervals, IP compliance, process optimization, self-driving loop, lineage, citation audits, and more).
- Permissions and roles: System Admin → Users & Roles. Write actions are permission-gated; buttons you lack permission for are disabled or hidden.

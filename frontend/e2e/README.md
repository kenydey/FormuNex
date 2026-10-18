# Browser tests (frontend)

`routes.spec.ts` opens every page twice (without and with a project selected) and fails on uncaught errors,
console errors, the error boundary or 5xx API responses. `smoke.spec.ts` has three tests: the main-path
smoke (dashboard → spec → formulation → DOE → SDS, all pure UI), the PLM publish lifecycle
(TDS/COA/label → approve → candidate → gate → 4-step sign-off → release), and lab measurement →
review → Pareto winning. It needs a running backend and Vite dev server, so it is **not** part of
`npm test` or PR CI — it runs nightly via `.github/workflows/playwright-nightly.yml` (and on demand
from the Actions tab).

```bash
# 1. throw-away database + seed (demo / Fx-Smoke-2026!, xylene/water, formulation fv-demo, lab methods)
export FORMUNEX_DB_URL=sqlite:////tmp/fx-e2e.db
export FORMUNEX_API_AUTH_ENABLED=false
export FORMUNEX_AUTH_JWT_SECRET=e2e-secret-0123456789-0123456789-0123456789
export FORMUNEX_PLM_STORAGE_DIR=/tmp/fx-e2e-plm
export FORMUNEX_PLM_COMPANY_NAME=方枢示例化工有限公司 FORMUNEX_PLM_EMERGENCY_PHONE=021-0001
export FORMUNEX_CELERY_EAGER=true
(cd ../backend && .venv/bin/python ../frontend/e2e/seed.py)   # creates the schema on first use

# 2. stack
(cd ../backend && .venv/bin/uvicorn app.main:app --port 8000) &
npx vite --port 5174 &

# 3. test (PW_CHROMIUM points at a preinstalled Chromium if Playwright's own is not downloaded)
npx playwright test            # both specs; or e.g. npx playwright test e2e/routes.spec.ts
```

The seed is not idempotent (unique usernames); delete the database file before re-seeding. The test
creates products with a unique code per run, so it can be repeated against the same database.

Seeded users (all password `Fx-Smoke-2026!`): `demo` (all roles, creates everything), `checker`
(EHS + quality: approves documents, reviews measurements), `signer_lead` / `signer_ehs` /
`signer_qa` / `signer_rm` (one per release sign-off step — the backend forbids one person
signing two steps). The xylene classification is locked (manual) and water carries an explicit
`not_classified` row so the release gate G2 passes.

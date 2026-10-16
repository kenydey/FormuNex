# Browser smoke test (frontend-next)

`smoke.spec.ts` logs in and walks the core screens (dashboard, products → revision → SDS proofreader,
release gate, label designer, formulation editor, lab tasks, audit log in English). It needs a running
backend and Vite dev server, so it is **not** part of `npm test` or CI.

```bash
# 1. throw-away database + seed (demo / Fx-Smoke-2026!, xylene/water, formulation fv-demo, lab methods)
export FORMUNEX_DB_URL=sqlite:////tmp/fx-e2e.db
export FORMUNEX_API_AUTH_ENABLED=false
export FORMUNEX_AUTH_JWT_SECRET=e2e-secret-0123456789-0123456789-0123456789
export FORMUNEX_PLM_STORAGE_DIR=/tmp/fx-e2e-plm
export FORMUNEX_PLM_COMPANY_NAME=方枢示例化工有限公司 FORMUNEX_PLM_EMERGENCY_PHONE=021-0001
export FORMUNEX_CELERY_EAGER=true
(cd ../backend && .venv/bin/python ../frontend-next/e2e/seed.py)   # creates the schema on first use

# 2. stack
(cd ../backend && .venv/bin/uvicorn app.main:app --port 8000) &
npx vite --port 5174 &

# 3. test (PW_CHROMIUM points at a preinstalled Chromium if Playwright's own is not downloaded)
npx playwright test
```

The seed is not idempotent (unique usernames); delete the database file before re-seeding. The test
creates products with a unique code per run, so it can be repeated against the same database.

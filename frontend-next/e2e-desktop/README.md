# Desktop smoke test (Tauri, Linux)

Drives the real desktop app with WebdriverIO through [`tauri-driver`](https://v2.tauri.app/develop/tests/webdriver/)
and WebKitWebDriver (Linux only; Windows would use msedgedriver, macOS has no WKWebView driver). It runs the
first-run wizard in **remote** mode against a running FormuNex API, signs in and opens a page — i.e. the
desktop-specific paths: cross-origin API base, body refresh token, setup gating, routing.

```bash
sudo apt-get install -y webkit2gtk-driver xvfb      # plus the WebKitGTK build libraries (docs/desktop.md)
cargo install tauri-driver --locked
npx tauri build --bundles deb                       # → src-tauri/target/release/formunex

# API with the desktop origins allowed, seeded like e2e/README.md (user demo / Fx-Smoke-2026!)
export FORMUNEX_CORS_ORIGINS='["tauri://localhost","http://tauri.localhost"]'
(cd ../backend && .venv/bin/uvicorn app.main:app --port 8000) &

xvfb-run -a node e2e-desktop/smoke.mjs              # FX_API / FX_APP / FX_USER / FX_PASSWORD to override
```

The local-stack mode (Docker Desktop orchestration) needs a Docker engine and is covered by the shell's unit
tests with a fake `docker` executable (`cargo test`); a real install / start / export smoke on Windows and macOS
is a release checklist item (docs/desktop.md).

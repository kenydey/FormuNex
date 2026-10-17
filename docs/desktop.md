# FormuNex 桌面端（Tauri 2，方案 §2.2 / §3.5）

桌面端与 Web 版使用同一套前端（`frontend-next/`），外壳在 `frontend-next/src-tauri/`。两种连接方式：

| 方式 | 说明 |
|------|------|
| **本机单机版** | 外壳用 Docker Desktop 在本机运行完整组件（PostgreSQL、Redis、Neo4j、数据库迁移、API、Celery worker / beat），API 只监听 `127.0.0.1`。数据在 Docker 命名卷 `formunex_fx-postgres`、`formunex_fx-neo4j`、`formunex_fx-data` 中，卸载重装不丢失。 |
| **连接服务器** | 只连接团队部署的 FormuNex 服务器（必须 https；仅本机地址可用 http），本机不运行容器。 |

## 首次启动向导

1. 选择连接方式。
2. 本机单机版：
   1. **检测 Docker**：查找 `docker`（PATH、Docker Desktop 默认安装位置，或在 `desktop.json` 的 `docker_path` 指定），检查引擎是否运行、`docker compose` v2 插件、Docker 可用内存（建议 ≥ 12 GB，待实测）。未安装时给出下载链接。Windows 需启用 WSL 2；Docker Desktop 对大型企业（> 250 人或年营收 > 1000 万美元）需付费订阅。
   2. **镜像**：联网拉取，或导入离线镜像包 `formunex-images-<版本>.tar.gz`（`docker load`）。
   3. **启动**：写入 `<应用数据目录>/stack/compose.yml` 与 `.env`（首次生成数据库 / 图数据库密码与 JWT 密钥，之后不再改动；文件权限 600），执行 `docker compose -p formunex up -d`，实时显示输出并轮询 `/health` 与各服务状态，直到 postgres、redis、kg、api、worker 都在运行且 API 健康。
   4. **管理员**：在 api 容器里运行 `scripts/create_user.py`，赋予全部内置角色；密码自动生成，只显示一次，同时存入系统钥匙串（服务名 `com.formunex.desktop`，条目 `admin-password`）。密码经子进程环境变量传入容器，不出现在命令行。
3. 连接服务器：输入地址 → 探测 `/health` → 保存。

设置保存在应用配置目录的 `desktop.json`（不含任何密钥）。之后可在「系统管理 → 桌面端与本机服务」或登录页的「更改连接」重新选择。

## 登录与会话

桌面端的界面来源（`tauri://localhost` / Windows 上 `http://tauri.localhost`）与 API 不同源，不能使用 httpOnly Cookie。登录 / 刷新接口在响应体中返回刷新令牌，外壳把它按服务器地址存入系统钥匙串（`refresh:<API 地址>`），刷新时放在请求体中；退出登录时吊销并删除。访问令牌只在内存中。

本机栈的 API 以 `FORMUNEX_ENVIRONMENT=production` 运行（强制账号登录、JWT 密钥由外壳生成），CORS 只放行桌面端来源，并暴露响应头（下载文件名、标签渲染诊断等）。连接服务器时，服务器需把上述两个来源加入 `FORMUNEX_CORS_ORIGINS`。

## 日常管理（系统管理 → 桌面端与本机服务）

- 服务状态、启动 / 重启 / 停止（停止不删数据）、按服务查看最近日志。
- **数据库备份**：`pg_dump -Fc` 到 `<应用数据目录>/stack/backups/`，保留最近 10 份。恢复：
  ```bash
  docker compose -p formunex exec -T postgres pg_restore --clean --if-exists -U formunex -d formunex < formunex-XXXX.dump
  ```
- **升级服务镜像**：先自动备份（数据库在运行且备份失败时不会继续），再改镜像标签、拉取并重建容器；数据库迁移由一次性的 `migrate` 服务在 API 启动前执行。
- **桌面程序更新**：Tauri updater（签名校验）。仅当构建时配置了更新公钥才启用，否则提示从发布页下载。
- 导出 PDF / Word / ZIP 等文件时使用系统“另存为”对话框（WKWebView 不支持 blob 下载）。
- 深度链接：`formunex://plm/packages/42` 打开对应页面；已运行时由单实例插件转交给现有窗口。
- 后台任务在窗口不在前台时完成，会发系统通知。

## 构建

```bash
cd frontend-next
npm ci
npm run tauri dev          # 开发：Vite 开发服务器 + 外壳
npm run tauri build        # 安装包：Windows .msi/.exe，macOS .dmg/.app，Linux .deb/.AppImage
```

Linux 需要 `libwebkit2gtk-4.1-dev libgtk-3-dev libsoup-3.0-dev librsvg2-dev libayatana-appindicator3-dev libssl-dev`。

外壳单元测试：`cd frontend-next/src-tauri && cargo test`（CI 作业 `desktop-shell` 同时跑 rustfmt 与 clippy）。
桌面冒烟测试（Linux，WebKitWebDriver + tauri-driver，需 Xvfb）：见 `frontend-next/e2e-desktop/README.md`。

## 发布

- `.github/workflows/images.yml`：推送到 main 时构建 `ghcr.io/<owner>/formunex-backend:edge`；版本标签 `v1.2.3` 时构建 `:1.2.3` 与 `:latest`，并生成离线镜像包（工作流产物；数 GB，超过 GitHub 发布附件 2 GB 上限，需另行分发）。本地生成：`deploy/desktop/make-offline-pack.sh 1.2.3 out/`。
- `.github/workflows/desktop-release.yml`：版本标签时在 windows-latest、macos-14（arm64）、macos-13（x64）、ubuntu-22.04 上构建安装包，生成草稿 Release。签名由仓库密钥开启：
  - macOS：`APPLE_CERTIFICATE`、`APPLE_CERTIFICATE_PASSWORD`、`APPLE_SIGNING_IDENTITY`；公证：`APPLE_ID`、`APPLE_PASSWORD`、`APPLE_TEAM_ID`。
  - Windows：`WINDOWS_SIGN_COMMAND`（EV 证书的签名命令，`%1` 为文件）。
  - 更新：`TAURI_SIGNING_PRIVATE_KEY`（及密码）、`TAURI_UPDATER_PUBKEY`。
  未配置时生成未签名安装包（内部测试可用，系统会提示风险）。
- 桌面程序版本与服务镜像版本相同（`src-tauri/Cargo.toml` / `tauri.conf.json` 的 version），新安装默认使用同版本镜像。

## 已知限制

- 本环境未接入 Docker 引擎，栈编排只通过模拟的 docker 可执行文件做了单元测试；首次在 Windows / macOS 上安装、拉镜像、启动、导出 PDF、打印的冒烟测试（方案 §7）仍需在真机上完成。
- 12 GB 内存建议值待实测；backend 镜像体积大（含科学计算与解析依赖），首次拉取耗时较长。
- 法规库初始化（13 万物质导入）未纳入向导，需按 `backend/scripts/seed_regulatory_db.py` 另行导入。
- 应用内刷新页面时依赖外壳对单页应用路由的回退；如遇空白页，回到首页即可。

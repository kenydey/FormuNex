# FormuNex（方枢）全面开发方案

> AI 驱动的产品研发与生命周期管理平台（R&D + PLM）
> 融合 FormuMind（AI 研发寻优）与 MSDS-Generator（化学品安全合规与文档生成）
>
> 版本：v1.1（方案评审稿） · 日期：2026-10-16 · 状态：待评审，评审通过后进入 Phase 1 编码
>
> v1.1 变更：ELN 方案由「Datalab 为实验数据唯一真源」调整为「FormuNex 内置实验执行模块为真源 + 外部 ELN 连接器（eLabFTW 首选、Datalab 保留）」，详见 §5.3.1。

---

## 0. 需求确认记录（问答结论）

| # | 议题 | 决定 |
|---|------|------|
| 1 | 仓库策略 | 以 **FormuMind 为底座**迁入 FormuNex 仓库；MSDS-Generator 的 `src/` 引擎重构为 `backend/app/services/plm/`，**丢弃 PySide6 GUI** |
| 2 | 前端交付形态 | **React Web + Tauri 2**：同一套前端代码同时交付浏览器版与 Windows / macOS 桌面版 |
| 3 | 部署模式 | **双模**：单机桌面 + 服务器多人协作；桌面端可切换连接远程服务器 |
| 4 | 重依赖 | **所有版本都要求完整组件**（PostgreSQL / Redis / Celery / Neo4j）；桌面版依赖 **Docker Desktop** |
| 5 | 法规范围 | **仅中国国标**：GB 30000.2~29、GB/T 16483、GB/T 17519、GB 15258、GB 12268 / GB 6944、GBZ 2.1、IECSC、《危险化学品目录》 |
| 6 | 文档格式 | **PDF + Word**，模板可配置（Logo / 页眉页脚 / 版式） |
| 7 | 语言 | **中文 + 英文**（界面与 SDS/TDS/COA/标签） |
| 8 | 发布审批 | **自动门禁 + 多级人工签批**（研发 → EHS/法规 → 质量 → 发布管理员），电子签名 + 审计日志 |
| 9 | 法规数据补缺 | **解析现有库 + PubChem 补充 + 人工确认**；供应商 SDS 导入优先级最高 |
| 10 | PageIndex | **自研轻量 ToC 树 + 两阶段按需启用**（兼容开源 PageIndex 树 JSON 格式） |
| 11 | UI 组件库 | **整体切换到 Ant Design 5 + ProComponents**，**一次性全部重写**现有约 190 个组件 |
| 12 | 配方表格 | **RevoGrid**（MIT）做配方编辑器 + **antd ProTable** 做列表；移除 ag-grid |
| 13 | 标签设计器 | **Web 画布设计器重写**（Konva.js），后端 ReportLab 按模板渲染 |
| 14 | 权限 | **内置账号 + RBAC**，预留 LDAP / OIDC（企业微信 / 钉钉 / 飞书） |
| 15 | LLM 策略 | **多供应商可配 + 敏感数据路由**（含精确配比的请求只走私有模型） |
| 16 | 外部对接 | ERP、企业文档系统 / 官网**预留接口，本期不开发** |
| 17 | 本次交付 | **先出方案，确认后再写代码** |
| 18 | ELN（v1.1） | **内置实验执行模块为真源 + 外部 ELN 连接器**（eLabFTW 首选，Datalab 降级为可选连接器），见 §5.3.1 |

---

## 1. 代码盘点结论（扫描结果）

### 1.1 FormuMind（底座）

| 维度 | 现状 |
|------|------|
| 后端 | FastAPI，47 个路由模块，约 230 个 service，50 个 Alembic 迁移（至 `0050_acquisition_snapshots`），586 个测试文件 |
| 数据层 | SQLAlchemy 2.0；默认 SQLite（`sqlite:///./data/formumind.db`），可切 PostgreSQL；Celery + Redis；Neo4j KG；Datalab ELN（`campaign_backend=auto`） |
| 前端 | Vite 5 + React 18 + TS + Tailwind 3 + Zustand，约 190 个组件，三栏暗色工业风；已装 `ag-grid-react@36` |
| 认证 | **仅 Bearer Token**（`middleware/api_auth.py` + `FORMUMIND_API_TOKEN`），多用户靠 `api_tokens_json` 映射 owner，**无 RBAC、无审批、无电子签名** |
| DOE | 全因子、部分因子、Plackett-Burman、CCD（含可旋转 α）、LHS、Box-Behnken（pyDOE）、Sobol、单纯形格子 / 单纯形重心 |
| 检索 | `hybrid_search`（BM25 + 余弦）、ColBERT 重排、`children_retrieval`；`DocumentChunk` 已有 `heading_path`、`page_no`、`page_end`、`block_type`、`bbox` —— **PageIndex 建树所需字段已基本齐备** |
| 合规 | `compliance_rules.py`（176 行，规则型）、`formulation_gate.py`、`publication_preflight.py`、`numeric_check.py` —— **无 GHS 分类计算** |

**缺口（需新增）**：`multimodal_fusion.py` 不存在；DOE 缺**正交表（Taguchi）**、**带上下限约束的混料设计（极端顶点 / D-最优）**、**分类因子混合设计**；无 Conformal Prediction 专用服务（仅 `training.py` 中有零散引用）；无 PLM / 产品 / 文档版本 / 审批体系。

### 1.2 MSDS-Generator（被整合方）

| 维度 | 现状 | 整合处置 |
|------|------|---------|
| GHS 计算 | `ghs_cn_mixture_engine.py`（ATE 加和、皮肤/眼加和法、CMR 阈值、H→P 映射）、`mixture_additivity_classifier.py`（753 行）、`ghs_regulation_engine.py`、`aqueous_mixture_rules.py` | **重构迁移**为 `plm/ghs/`，补齐 M 因子、SCL、吸入危害、未知 ATE 修正等 |
| 运输 | `transport_engine.py` / `transport_classifier.py` / `transport_regulations_mapper.py` | 迁移；**`gb12268_2025.json` 只有 10 条高频 UN 条目**，需补全表 |
| 职业接触限值 | `oel_gbz2_2019.json`（GBZ 2.1-2019）、`oel_eh40.json`（英国 EH40） | GBZ 迁入；EH40 不在国标范围，**不导入** |
| H/P 语句库 | `hazard_statements.json`、`precaution_statements.json`（中英） | 迁入 `ghs_statements` 表 |
| 化学品库 | `common_chemicals.json`：**131,089 条，仅 CAS / 中英名 / 分子式**；`hazard_chemical_info.json`（= `chemical_info.json`，内容相同）：**2,857 条危化品目录条目，其中 2,492 条有 CAS，危害分类为中文自由文本**（119 种危害类别写法） | 清洗导入 `substances` 表；中文分类文本解析为结构化「危害类别 + 类别 + H 代码」 |
| 映射 | `config/ghs_standard_map.json`（关键词 → H/P） | 作为解析器种子规则 |
| SDS 生成 | `msds_auto_generator.py`、`msds_section_generators.py`、`section3_builder.py`、`msds_pdf_exporter.py`、`word_exporter.py`（916 行） | 业务逻辑迁移，渲染层改为模板化 |
| 标签 | `ghs_label_reportlab.py`（623 行）+ PySide6 标签设计器（34 文件） | ReportLab 渲染迁移；设计器用 Konva 重写 |
| TDS | `tds_*`（10+ 文件）+ PySide6 TDS 生成器 | 迁移解析/版式逻辑 |
| 文档导入 | `pdf_import_orchestrator.py`、`sds_parser_adapter.py`、`enhanced_pdf_importer.py`（camelot / img2table / tungsten-sds） | **用于供应商 SDS 导入**；与 FormuMind 的 MinerU / pymupdf4llm 解析管线合并 |
| 象形图 | `pictograms/GHS01~09.svg/png` | 迁入（SVG 为普通文件，非 LFS） |
| `api_upload.py` | **只是文件上传解析接口，不是发布脚本** | 不作为发布连接器基础；发布连接器新写（本期只预留接口） |
| `.ghs_default_template` | **只含一个模板 ID（`7f753021`），无模板内容** | 需新建 SDS/标签模板 |
| `fonts/` | Arial / SimSun / 微软雅黑 / 黑体 / 仿宋等**微软专有字体**（且为 LFS 文件） | **不可随产品分发**，改用思源黑体 / 思源宋体（Noto Sans/Serif SC，SIL OFL） |
| `chemicals/` | 第三方 `chemicals` 库的完整源码副本（76 MB） | 删除，改为 pip 依赖 |

### 1.3 需要纠正的几个前提

1. **「pH 判定金属腐蚀物（GB 30000.17）」不成立**：金属腐蚀物只能依据 UN 试验 C.1（钢 / 铝腐蚀速率 > 6.25 mm/年）判定，**pH 不能推导**。pH ≤ 2 或 ≥ 11.5 是**皮肤腐蚀 / 严重眼损伤**的提示性依据（GB 30000.19/20 混合物判定的考虑因素）。方案中金属腐蚀物只接受实测腐蚀速率或供应商数据。
2. **原料本身往往是混合物**（树脂、助剂、色浆都有自己的 SDS 和组分），因此不能简单在 `materials` 表上加 GHS 字段，必须引入「**物质（substance，CAS 级）**」与「**原料（material，可采购商品）→ 组分构成**」两层模型，配方分类时把原料展开到物质级（见 §4.2）。
3. **法规数据缺毒理数值**：现有库只有分类结论，没有 LD50 / LC50 / M 因子 / 特定浓度限值（SCL）。引擎需支持「分类结论 → 类别换算点估计值」（GB 30000.18 表 2），并在 UI 中标注数据来源与置信度。
4. **桌面版全组件 + Docker Desktop**：既然 Python 后端也随容器运行，桌面端**不再需要 PyInstaller 打包 Python sidecar**，Tauri 只做外壳与 Docker 编排器，打包大幅简化。注意 Docker Desktop 对大企业（> 250 人或年营收 > 1000 万美元）需付费订阅。

---

## 2. 总体架构

### 2.1 分层架构

```
┌──────────────────────────────────────────────────────────────────────────┐
│  客户端：同一套 React 18 + TS + Ant Design 5 构建产物                     │
│  ├─ Web 浏览器（Nginx 托管）                                              │
│  └─ Tauri 2 桌面壳（Win: WebView2 / mac: WKWebView）                      │
│       · Docker 编排器（拉起 / 健康检查 / 升级本地 compose 栈）            │
│       · 连接配置：本机栈 ⇄ 远程服务器                                     │
│       · 系统集成：文件对话框、原生通知、打印、深链接 formunex://          │
├──────────────────────────────────────────────────────────────────────────┤
│  API 网关：FastAPI（/api/v1/*）· JWT 会话 · RBAC · 限流 · 审计中间件      │
│            · WebSocket/SSE（任务进度、实时合规评估）                       │
├──────────────────────────────────────────────────────────────────────────┤
│  应用服务层（按五大工作台 + 平台能力划分的领域模块）                       │
│  ┌─────────┬──────────┬────────────┬────────────┬──────────────────────┐ │
│  │W1 知识  │W2 需求   │W3 配方&DOE │W4 寻优     │W5 PLM 文档与发布     │ │
│  │情报     │规格      │            │驾驶舱      │ghs/ sds/ tds/ coa/   │ │
│  │         │          │            │            │label/ release/       │ │
│  └─────────┴──────────┴────────────┴────────────┴──────────────────────┘ │
│  平台：identity(RBAC) · approvals(签批) · audit(哈希链) · templates ·     │
│        llm_router(敏感路由) · lineage(数据链) · connectors(预留)          │
├──────────────────────────────────────────────────────────────────────────┤
│  引擎层（adapter + fallback，沿用 FormuMind 设计）                         │
│  RDKit · BayBE/BoTorch · pyDOE · MinerU · ColBERT · WeasyPrint ·         │
│  docxtpl · ReportLab · PubChem · LLM（9 家 + 私有 OpenAI 兼容端点）       │
├──────────────────────────────────────────────────────────────────────────┤
│  持久化：PostgreSQL 16（主库，含 pg_trgm / pgvector）· Redis 7 ·          │
│          Neo4j 5 · 对象存储（MinIO / 本地卷）· 外部 ELN（可选连接器）     │
└──────────────────────────────────────────────────────────────────────────┘
```

**关键原则**

1. **业务逻辑全部在 Python 后端**，前端无法规计算；桌面 / Web 行为一致。
2. **PostgreSQL 成为所有部署形态的唯一主库**（因桌面版也跑完整容器栈）；SQLite 仅保留给 CI 单测。这样可以使用 `pgvector`、`pg_trgm`（13 万物质名模糊检索）、JSONB 索引、行级锁。
3. **所有文档（PDF/Word/标签）在服务端渲染**，规避 WebView2 / WKWebView 打印与字体差异。
4. **结构化内容为真源**：SDS/TDS/COA 先生成结构化 JSON（`doc_content`），再由模板渲染为 PDF/Word；Diff、校对、跨文档数值一致性校验都基于结构化 JSON。
5. **全链路可追溯（lineage）**：每个发布产品可回溯到配方版本、DOE 计划、实验测量、文献证据和规则版本。

### 2.2 部署拓扑

| 形态 | 组成 | 说明 |
|------|------|------|
| **桌面单机版** | Tauri 安装包 + `docker-compose.desktop.yml`（postgres、redis、neo4j、api、worker、beat、minio） | 首启向导：检测 Docker Desktop → 拉镜像（支持离线镜像包 `formunex-images.tar`）→ 初始化库 → 创建管理员。资源要求：约 12 GB 内存起（待实测） |
| **服务器版** | `docker-compose.prod.yml` / Helm Chart；Nginx + TLS | 多人协作、审批流、集中审计 |
| **桌面客户端连服务器** | Tauri 壳，仅连接远程 URL，不启本地栈 | 设置中切换「本机 / 服务器」 |
| **纯 Web** | 浏览器访问服务器版 | 与桌面端同一构建产物 |

### 2.3 数据链（R&D → PLM）

```
SourceDocument ─(证据)─► ProjectSpec / ObjectiveContract ─► FormulationVersion
      │                         │  (目标、成本、禁用清单)        │  (RevoGrid 编辑, 实时 GHS)
      ▼                         ▼                                ▼
  ToC Tree / KG            DOEPlan ─► LabTask / Sample / Measurement (内置实验执行，可选外部 ELN 同步)
                                │              │
                                ▼              ▼
                       BayBE Campaign ─► Pareto / 定型配方 (winning)
                                                │
                                                ▼
         Product ─► ProductRevision ─► ClassificationResult(GB 30000, rule_set_version)
                                                │
                    ┌──────────────┬────────────┼─────────────┐
                    ▼              ▼            ▼             ▼
                  SDS            TDS          Label         COA(按批次)
                    └──────────────┴─────┬──────┴─────────────┘
                                         ▼
                         ReleaseGate ─► Approval(4 级签批) ─► Released
```

每条边都落库为 `lineage_edges(from_type, from_id, to_type, to_id, relation)`，前端提供「产品溯源」视图。

---

## 3. 前端方案

### 3.1 技术栈

| 类别 | 选型 | 说明 |
|------|------|------|
| 构建 | Vite 5 + React 18 + TypeScript 5（strict） | 沿用 |
| UI | **Ant Design 5 + @ant-design/pro-components**（ProLayout / ProTable / ProForm / ProDescriptions / StepsForm） | 企业后台风格；中文生态；内置暗色算法与 Token 主题 |
| 配方表格 | **RevoGrid**（MIT，`@revolist/react-datagrid`） | 范围选择、Excel 粘贴、自动填充、行分组（预混料子配方）、虚拟滚动；自定义单元格渲染合规色标 |
| 列表表格 | antd ProTable | 物料库、项目、文档、审批等 |
| 图表 | **ECharts 5**（`echarts-for-react`）+ `echarts-gl`（3D 响应面） | 收敛曲线、帕累托、响应面、流变曲线、色度 a*b* 图 |
| 化学结构 | **Ketcher**（结构绘制 / 检索输入）+ RDKit.js（SMILES 渲染） | 结构式检索 |
| PDF 预览 | **react-pdf（PDF.js）** | SDS/TDS/COA 校对、原文页码对照 |
| 标签设计器 | **react-konva（Konva.js）** | 拖拽元素、对齐吸附、象形图、二维码、批量变量 |
| 服务端状态 | **TanStack Query v5** | 缓存、乐观更新、SSE 任务进度订阅 |
| 客户端状态 | Zustand（仅 UI 状态） | 沿用 |
| 路由 | React Router v6（数据路由） | 五大工作台路由 |
| 表单校验 | ProForm + **Zod**（由后端 OpenAPI 生成） | 前后端同一份 schema |
| API 客户端 | **openapi-typescript + openapi-fetch** 由 FastAPI OpenAPI 自动生成 | 杜绝手写类型漂移 |
| 国际化 | **i18next + react-i18next**；antd `ConfigProvider locale` | zh-CN / en-US |
| Diff | `react-diff-viewer-continued` + 结构化字段 Diff 组件 | 文档版本对比 |
| 测试 | Vitest + Testing Library + **Playwright**（Web）+ WebdriverIO（Tauri） | |
| 桌面 | **Tauri 2**（插件：shell、dialog、fs、notification、updater、deep-link、process） | |

### 3.2 应用壳与导航

ProLayout 左侧「**生命周期五步导航**」，顶部为项目切换器、全局搜索（Cmd/Ctrl+K）、任务中心、审批待办、语言 / 主题、用户菜单。

```
① 知识与情报中心    /knowledge   检索 · 结构式 · 表格提取 · Wiki · 知识图谱
② 研发需求工作台    /spec        项目规格 · 目标契约 · 工艺窗口 · 合规红线
③ 配方与 DOE 实验室 /lab         配方编辑 · 替换推荐 · DOE 矩阵 · 实验任务与录入
④ AI 寻优驾驶舱     /cockpit     BayBE 迭代 · 偏差校正 · 帕累托 · 专项分析
⑤ PLM 文档与发布    /plm         产品 · SDS · TDS · COA · 标签 · 门禁 · 签批
──────────────
   物料与法规库     /materials   原料 · 物质 · 法规数据复核
   系统管理         /admin       用户角色 · 模板 · LLM 路由 · 审计 · 连接器
```

项目上下文贯穿全局：URL 形如 `/p/:projectId/lab/formulations/:fid`；顶部显示当前项目的生命周期阶段徽章（立项 / 研发 / 定型 / 已发布）。

### 3.3 目录结构

```
frontend/
├─ src/
│  ├─ app/                 # 应用壳：ProLayout、路由、Providers、主题、i18n
│  ├─ api/                 # openapi 生成的 client + query hooks（按领域）
│  ├─ shared/              # 通用组件：GhsPictogram、HazardBadge、SourceBadge、
│  │                       #   PdfViewer、DiffViewer、TaskProgress、Signature
│  ├─ features/
│  │  ├─ knowledge/        # W1
│  │  ├─ spec/             # W2
│  │  ├─ lab/              # W3（formula-editor/ 基于 RevoGrid）
│  │  ├─ cockpit/          # W4
│  │  ├─ plm/              # W5（sds/ tds/ coa/ label-designer/ release/）
│  │  ├─ materials/
│  │  └─ admin/
│  ├─ locales/{zh-CN,en-US}/*.json
│  └─ main.tsx
├─ src-tauri/              # Tauri 2：Rust 壳、docker 编排、updater 配置
└─ e2e/                    # Playwright
```

### 3.4 一次性重写策略（约 190 个组件）

一次性重写不等于一次性上线：在独立分支 `feat/ui-antd` 上完成，旧前端保留在 `frontend-legacy/` 直到新前端通过验收，**最后一次性切换**。

1. **功能清单冻结**：先把 190 个组件逐一登记到「功能映射表」（旧组件 → 新工作台页面 / 合并 / 废弃），作为验收基线，防止功能丢失。
2. **API 先行**：后端先补 OpenAPI 完整性（所有路由声明 response_model），前端生成类型。
3. **按工作台并行开发**：每个工作台一名前端负责人，共享 `shared/` 组件库。
4. **验收**：功能映射表 100% 打勾 + Playwright 主路径 E2E 全绿 + Win/mac 桌面冒烟测试。

### 3.5 Tauri 2 桌面端设计

| 能力 | 实现 |
|------|------|
| 栈编排 | Rust 侧调用 `docker compose -p formunex -f <app_data>/compose.yml up -d`；轮询 `/health` 显示启动进度页 |
| 首启向导 | 检测 Docker Desktop（未装则引导下载）→ 资源检查 → 导入离线镜像包（可选）→ 初始化 → 创建管理员 |
| 连接切换 | 本机栈 / 远程服务器 URL（证书校验，支持企业自签 CA） |
| 安全 | API 只绑定 `127.0.0.1`；本地栈管理员密码首启生成并存入系统钥匙串（Windows Credential Manager / macOS Keychain） |
| 文件 | 原生保存对话框导出 PDF/Word/ZIP；拖拽上传 |
| 更新 | Tauri updater（签名）更新外壳；镜像版本由 compose 标签管理，升级前自动 `pg_dump` 备份 |
| 签名 | Windows：EV 代码签名；macOS：Developer ID + 公证（notarization） |
| CI | GitHub Actions 矩阵：`windows-latest`、`macos-14`（arm64）、`macos-13`（x64）构建 `.msi` / `.dmg` |

### 3.6 关键交互设计

- **配方编辑器**（W3 核心）：左侧 RevoGrid（原料、角色、质量分数、成本小计、VOC 贡献、危害徽章），支持粘贴 Excel 区域、拖拽排序、预混料分组；底栏实时显示合计 / 归一化按钮；**右侧合规侧边栏**通过 WebSocket 防抖（400 ms）推送 GB 30000 评估结果：象形图、信号词、H 语句、VOC g/L、成本元/kg、红线拦截（禁用 CAS 命中整行标红并阻止保存为「候选」）。
- **SDS 校对器**（W5 核心）：左侧 16 节结构化表单（每个字段带来源标记：计算 / 实测 / 人工 / 物料 SDS）、右侧 PDF 实时预览；字段级锁定；中英对照模式；「与上一发布版本对比」按钮。
- **发布门禁**：Checklist 视图（每项通过 / 失败 / 豁免 + 证据链接），全部通过后才能「提交签批」。

---

## 4. 后端方案

### 4.1 模块结构（在 FormuMind 基础上调整）

```
backend/app/
├─ api/v1/
│  ├─ knowledge/…        # search, kb, kg, ingest, wiki, surechembl, structure, pageindex
│  ├─ spec.py            # 由 projects.py 拆出
│  ├─ formulations.py, materials.py, substances.py(新), doe.py, experiments.py
│  ├─ optimize.py, loop.py, analysis.py(新: rheology/colorimetry/acid)
│  ├─ plm/               # 新：products, classification, sds, tds, coa, labels,
│  │                     #     templates, documents, release, approvals
│  ├─ identity.py(新)    # 登录、用户、角色、项目成员
│  └─ audit.py(新)
├─ domain/               # 纯 Python schema 与规则（沿用 + 新增 plm 领域模型）
├─ services/
│  ├─ … (FormuMind 现有)
│  ├─ pageindex/         # 新：tree_builder, navigator, two_stage
│  ├─ doe_designs/       # 新：orthogonal_arrays, constrained_mixture, d_optimal
│  ├─ analysis/conformal.py  # 新
│  ├─ plm/
│  │  ├─ ghs/            # 分类引擎（见 §5.5）
│  │  ├─ regulatory/     # 物质库、数据源优先级、PubChem 补充、供应商 SDS 导入
│  │  ├─ transport.py
│  │  ├─ documents/      # sds_builder, tds_builder, coa_builder, label_builder
│  │  ├─ render/         # html_pdf(WeasyPrint), docx(docxtpl), label_pdf(ReportLab)
│  │  ├─ diff.py
│  │  └─ release_gate.py
│  ├─ identity/          # 密码哈希(argon2)、JWT、RBAC 策略、LDAP/OIDC 接口(预留)
│  ├─ approvals/         # 签批状态机、电子签名
│  ├─ audit/             # 哈希链审计日志
│  ├─ llm_router.py      # 敏感数据路由
│  ├─ lineage.py
│  └─ connectors/        # 发布连接器接口（ERP / 文档库 / 官网，本期仅接口与 Webhook 骨架）
├─ agents/regulatory.py  # 法规智能体（解释分类依据、建议替代、回答法规问答，只读）
└─ worker/               # Celery 任务：文档渲染、PubChem 补充、建树、批量 COA
```

API 统一加 `/api/v1` 前缀；旧路径在一个版本内保留 307 重定向。

### 4.2 数据模型（新增 / 调整）

**物质与原料两层模型**（核心调整）：

| 表 | 关键字段 | 说明 |
|----|---------|------|
| `substances`（新） | `id`, `cas_no`(唯一,可空), `ec_no`, `name_zh`, `name_en`, `aliases[]`, `mf`, `smiles`, `iecsc_listed`, `hazardous_catalog_2015`(bool + 序号), `key_supervised`, `precursor_control`(易制毒), `explosive_precursor`(易制爆) | 13.1 万条导入；`pg_trgm` 名称检索 |
| `substance_classifications`（新） | `substance_id`, `hazard_class`, `category`, `h_code`, `scl_json`(特定浓度限值), `m_factor_acute`, `m_factor_chronic`, `ate_oral/dermal/inhal_*`, **`source`**(manual / supplier_sds / cn_catalog / pubchem / inferred), `source_ref`, `confidence`, `locked_by`, `locked_at` | **多来源并存**，按优先级取有效值 |
| `substance_oel`（新） | `substance_id`, `mac`, `pc_twa`, `pc_stel`, `notes`(皮/敏/致癌标识), `standard`='GBZ 2.1-2019' | |
| `substance_transport`（新） | `un_number`, `proper_shipping_name_zh/en`, `class`, `subsidiary_risk`, `packing_group`, `special_provisions` | GB 12268 全表 |
| `ghs_statements`（新） | `code`, `kind`(H/P/EUH 不导入), `text_zh`, `text_en`, `combined_codes` | |
| `materials`（扩展） | 新增 `material_type`(substance / mixture / trade_product), `phys_state`, `regulatory_status`(待复核 / 已复核 / 已锁定), `sds_document_id` | 保留 FormuMind 现有字段 |
| `material_compositions`（新） | `material_id`, `substance_id`, `pct_min`, `pct_max`, `pct_typical`, `is_trade_secret` | 原料 → 物质展开 |

**PLM 与发布**：

| 表 | 关键字段 |
|----|---------|
| `products` | `id`, `code`(产品编码规则可配), `name_zh/en`, `project_id`, `lifecycle_state`(concept / rd / candidate / under_review / released / superseded / obsolete) |
| `product_revisions` | `product_id`, `rev`(A, B, C…), `formulation_version_id`, `winning_experiment_id`, `spec_snapshot_json`, `frozen_at`（冻结后不可变） |
| `classification_results` | `revision_id`, `rule_set_version`, `inputs_hash`, `result_json`, `trace_json`(逐条判定依据), `computed_at` |
| `doc_templates` | `doc_type`(sds / tds / coa / label), `lang`, `name`, `version`, `engine`(html / docx / label_json), `content`(存对象存储), `is_default` |
| `regulatory_documents` | `id`, `revision_id`, `doc_type`, `lang`, `version_no`, `status`(draft / in_review / approved / released / superseded), `content_json`(结构化真源), `field_sources_json`, `rendered_pdf_key`, `rendered_docx_key`, `content_hash`, `supersedes_id` |
| `batches` / `batch_qc_results` | 批号、生产日期、`qc_report` 实测值 → COA |
| `release_packages` | `revision_id`, `gate_result_json`, `document_ids[]`, `package_hash`, `status` |
| `approval_flows` / `approval_steps` | 流程模板（4 级）、当前步骤、指派角色 / 人、意见 |
| `e_signatures` | `user_id`, `step_id`, `meaning`(编制 / 审核 / 批准 / 发布), `signed_hash`(对 package_hash 签名), `signed_at`, `reauth_method` |
| `audit_log` | `seq`, `actor`, `action`, `entity`, `before/after_hash`, `prev_hash`, `hash`（**哈希链**，追加写；DB 触发器禁止 UPDATE / DELETE） |
| `lineage_edges` | 见 §2.3 |

**身份与权限**：`users`、`roles`、`user_roles`、`project_members(project_id, user_id, role)`、`auth_providers`(local / ldap / oidc 预留)。

**PageIndex**：`document_toc_trees(source_id, tree_json(JSONB), builder_version, node_count, max_depth, built_at, summaries_status)`。

### 4.3 Alembic 迁移规划

| 编号 | 内容 |
|------|------|
| `0051_identity_rbac` | users / roles / project_members；把现有 `owner` 迁为用户 |
| `0052_substances_regulatory` | substances、substance_classifications、substance_oel、substance_transport、ghs_statements |
| `0053_material_compositions` | materials 扩展字段 + material_compositions |
| `0054_document_toc_trees` | PageIndex 存储 |
| `0055_plm_products` | products、product_revisions、classification_results、lineage_edges |
| `0056_plm_documents` | doc_templates、regulatory_documents、batches、batch_qc_results |
| `0057_release_approvals_audit` | release_packages、approval_flows / steps、e_signatures、audit_log（含防篡改触发器） |
| `0058_pg_extensions` | pg_trgm、pgvector 索引（仅 PostgreSQL；SQLite CI 跳过） |
| `0059_lab_execution` | lab_tasks、samples、test_methods、instruments、eln_links；measurements 扩展（方法、仪器、复核状态） |

种子脚本：`backend/scripts/seed_regulatory_db.py`（幂等、可增量，记录 `data_release` 版本，见 §5.5.1）。

### 4.4 认证与权限（RBAC）

| 角色 | 主要权限 |
|------|---------|
| 研发工程师 | 项目内知识 / 配方 / DOE / 实验读写；生成文档草稿 |
| 项目负责人 | 上述 + 规格契约编辑、定型配方钦定、发起发布 |
| EHS / 法规专员 | 物质库复核锁定、分类结果复核、SDS / 标签审核签字 |
| 质量 | 批次 QC、COA 编制、质量签字 |
| 发布管理员 | 最终发布、撤回、作废 |
| 系统管理员 | 用户、模板、LLM 路由、连接器、审计查看 |

- **数据隔离**：配方精确配比属商业机密，按 `project_members` 授权；非成员只看到区间化组成（与 SDS 第 3 节一致的脱敏）。
- **会话**：JWT 访问令牌（15 分钟）+ 刷新令牌（httpOnly Cookie；Tauri 中存钥匙串）；密码 argon2id；登录失败锁定。
- **电子签名**：签名时要求重新输入密码，记录签名含义、时间、被签对象哈希。
- **预留**：`AuthProvider` 接口，LDAP（ldap3）与 OIDC（Authlib）实现留桩。
- 旧 Bearer Token 保留为「服务账号 API Key」（用于脚本 / 集成）。

### 4.5 LLM 敏感数据路由

- **敏感度分级**：`public`（文献、专利、公开法规问答）/ `internal`（项目规格、实验结论）/ `secret`（精确配比、配方版本、批次数据）。
- **自动判定**：在 `llm.py` 统一入口增加 `classify_payload()`：含 `FormulationVersion` / `material_compositions` 精确值 / 批次数据的上下文判为 `secret`。
- **路由表**（管理员配置）：`secret` → 私有 OpenAI 兼容端点（Ollama / vLLM / 企业私有部署的 Qwen / DeepSeek）；`internal` → 管理员白名单供应商；`public` → 任意已配置供应商。
- **降级**：`secret` 请求无私有模型时**拒绝**（不静默外发），并提示走规则引擎。
- 所有调用记入 `llm_trace`（已有），增加 `sensitivity`、`provider` 字段供审计。

### 4.6 任务与实时通道

- Celery 队列拆分：`ingest`、`optimize`、`render`（文档渲染）、`regulatory`（PubChem 补充、批量复算）、`default`。
- 进度：Redis Pub/Sub → SSE（`/api/v1/tasks/{id}/events`），沿用 FormuMind `taskStream`。
- 实时合规评估：WebSocket `/ws/formulations/{id}/compliance`，服务端缓存物质展开结果，单次评估目标 < 150 ms（50 个组分）。

---

## 5. 功能实现方案（五大工作台）

### 5.1 W1 知识与情报中心（Knowledge & IP Hub）

| 子功能 | 复用 | 新增 / 改造 |
|--------|------|------------|
| 文献 / 专利联邦检索 | `federated_search`、`search_providers`（OpenAlex / S2 / Crossref / arXiv）、`epo_ops`、`surechembl_client`、`literature_duplicates`、`literature_screening`、`fulltext_fetcher`、`pdf_downloader` | 新 UI：统一检索页（来源分面、去重簇展示、高价值评分、批量入库）；**新增 CNIPA 中国专利**来源评估（无官方开放 API，先走 Google Patents CN 全文，已有） |
| 化学结构式检索 | `structure_search`、`structure_index`（Morgan 指纹）、`ocsr`、`structure_recognize` | Ketcher 绘制 → 相似度 / 子结构检索；专利图片 OCSR 结果人工校正后回写 |
| 多模态表格提取预览 | `mineru_structured`、`vision_extract`、`table_contract`、`table_normalize`、`embodiment_drafts`、`page_thumbnails` | **新增 `multimodal_fusion.py`**：表格 ↔ 正文 ↔ 图的对齐与合并；左 PDF 原页（高亮 bbox）/ 右结构化表格对照；一键「入库为配方草案」或「生成 KG 三元组」 |
| STORM 领域 Wiki | `wiki/storm_*`、`dossier`、`citation_binder` | 新 UI：大纲编辑 → 草稿 → 润色三步，引用锚点点击跳回原文页码 |
| 知识图谱 | `kg/*`、`neo4j_kg` | 图浏览器（ECharts graph） |
| **PageIndex 两阶段检索** | `hybrid_search`、`colbert_store`、`children_retrieval`、`source_guide`、chunk `heading_path` / `page_no` / `page_end` | **新增**，见 §5.1.1 |

#### 5.1.1 PageIndex 设计（评估结论：需要，但限定场景）

**为什么需要**：长篇专利与国标中，「前体定义 → 表 1 配方 → 表 2 性能」分散在不同页，纯向量分块检索容易只召回其中一段，导致实施例萃取漏配对。ToC 树让 LLM 按章节结构寻址，并留下可审计路径。
**为什么限定场景**：逐文档 LLM 遍历每次需要 3~8 次 LLM 调用，延迟 5~20 s，普通问答不划算。

**构建期（无 LLM，入库时同步完成）**

1. 数据来源：`DocumentChunk.heading_path`（H1/H2/H3）、`page_no` / `page_end`、`block_type in ('table','table_caption','figure')`、MinerU 结构化输出中的表格标题。
2. 专利特化：识别「权利要求 / 说明书 / 实施例 N / 对比例 N / 表 N」等中英文模式，即使 PDF 没有标题层级也能建出「实施例」节点。
3. 节点结构（兼容开源 PageIndex 格式并扩展）：

```json
{
  "node_id": "0007",
  "title": "实施例 3",
  "summary": null,
  "page_range": [12, 14],
  "chunk_ids": ["…"],
  "table_ids": ["tbl_2", "tbl_3"],
  "refs": [{"to": "0012", "kind": "table_ref", "label": "表 2"}],
  "children": []
}
```

4. `refs`：在节点正文中抽取「见表 2 / 如实施例 1 所述 / Table 3」等交叉引用，建立跨章节边，这是跨页关联的关键。
5. `summary` 延迟生成：Celery 低优先级任务，仅对被 Stage 2 访问过或 ≥ 20 页的文档生成（走 `public` 级 LLM 路由，专利 / 文献属公开数据）。

**检索期**

- **Stage 1（广度）**：`hybrid_search` + ColBERT 重排 → Top-K 文档。
- **Stage 2（深度，按需触发）**：触发条件为文档 ≥ 20 页且查询属于「实施例萃取 / 深度研究 / 用户显式开启」。`navigator` 给 LLM 提供树的标题 + 摘要（不含正文），LLM 选择节点 → 读取节点正文 → 沿 `refs` 跳转，**预算**：最多 6 跳、16k 输入 token；输出 `answer`、`node_path`、`reasoning_trace`、引用（chunk_id + 页码）。
- **降级**：无树或预算耗尽时回退 `children_retrieval`。
- **挂载点**：`embodiment_drafts`（配方表 ↔ 性能表配对）、`deep_research/engine`、Chat 的「深度模式」开关。

**评估**：构建 30 篇长专利标注集（配方表 ↔ 性能表 ↔ 前体定义的正确配对），指标为配对准确率、页码命中率、平均跳数与延迟；对照组为纯 `hybrid_search`。目标：配对准确率提升 ≥ 20 个百分点。

### 5.2 W2 研发需求工作台（Requirement & Spec Studio）

| 子功能 | 复用 | 新增 / 改造 |
|--------|------|------------|
| 项目规格书 | `domain/project_spec.py`、`api/projects.py` | StepsForm 向导：应用场景 → 基材 → 性能目标 → 成本 → 工艺 → 合规 |
| 目标契约 | `domain/objective_contract.py` | 指标表格：指标名、单位、方向（≥ / ≤ / 区间 / 目标值）、上下限、权重、测试方法标准号（如 GB/T 10125 盐雾、GB/T 9286 划格）、**是否出厂指标**（供 COA 使用） |
| 工艺窗口 | `domain/levers.py` | 固化温度 / 时间、膜厚、施工方式、基材（A356 / ADC12 / 冷轧钢等枚举可扩展） |
| 合规红线 | `compliance_rules.py`、`formulation_gate.py` | **新增**：禁用 / 限用 CAS 清单（可从预置清单导入：六价铬化合物、《重点管控新污染物清单》、优先控制化学品名录等）、VOC 上限 g/L（关联 GB 30981 / GB 33372 等限值标准的预置模板）、目标 GHS 约束（如「不得出现 GHS06 / GHS08」、「信号词不得为危险」） |
| 契约版本 | `project_payload_history` | 契约变更生成版本并通知下游（DOE、门禁重新评估） |

合规红线在三处生效：**配方编辑实时拦截**、**AI 推荐 / 替换候选过滤**（`recommend_pipeline` / `substitution`）、**发布门禁复核**。

### 5.3 W3 配方与 DOE 实验室（Formulation & DOE Lab）

**配方编辑器**
- 版本树：复用 `FormulationVersion`（0013）+ `formulation_history`；UI 显示分支、比较任意两版。
- 归一化：合计 ≠ 100% 时提示；「按比例归一化」与「以溶剂 / 指定组分补足」两种模式；锁定行不参与归一化。
- 组分角色：复用 `kg/ingredient_roles.py`，下拉选择 + AI 建议。
- **实时合规侧边栏**：原料 → 物质展开 → GB 30000 分类（§5.5）→ 象形图 / 信号词 / H 语句 / VOC 估算 / 成本 / 红线告警；每项可展开查看「贡献组分」。

**智能原料替换**（`substitution.py`）
- 触发：超成本、断供（`supply_flags`）、命中红线、分类升级（如替换后不再有 GHS08）。
- 候选来源：KG 同功能（`material_graph`）、`external_alternatives`、`surechembl_alternatives`、`llm_alternatives`（`internal` 级路由）。
- 排序：功能相似度 × 危害降级收益 × 成本差 × 供应可用性；**每个候选附「替换后配方的分类预览」**。

**DOE 实验矩阵**

| 设计 | 现状 | 方案 |
|------|------|------|
| 全因子 / 部分因子 / PB | 已有 | UI 暴露 |
| CCD / Box-Behnken | 已有（native + pyDOE） | UI 暴露（α 选择） |
| LHS / Sobol | 已有 | UI 暴露 |
| 单纯形格子 / 重心 | 已有 | UI 暴露 |
| **正交表（Taguchi L4 / L8 / L9 / L12 / L16 / L18 / L27 …）** | 无 | **新增 `doe_designs/orthogonal_arrays.py`**（内置标准表 + 因子水平分配 + 交互列） |
| **约束混料（组分上下限、总和 100%）** | 无 | **新增 `constrained_mixture.py`**：极端顶点法（McLean-Anderson）+ 候选点上的 D-最优选点 |
| **混料-过程交叉设计** | 无 | 混料设计 × 过程因子（如固化温度）的乘积 / D-最优 |
| **分类因子**（树脂 / 固化剂牌号） | 部分（BayBE 支持） | DOE 层统一 `CategoricalFactor`，正交 / D-最优均支持 |

DOE 生成后对每个试验点**预跑合规评估**，命中红线的点标红，可一键剔除或替换。

**实验记录（ELN）：内置实验执行模块 + 外部 ELN 连接器**（v1.1 调整，评估见 §5.3.1）
- **内置「实验执行」模块为唯一真源（SSOT）**：DOE 计划一键下发为实验任务单 → 实验员在 FormuNex 中领取、按任务单录入实测值（RevoGrid 批量录入 / 扫码样品号 / 仪器文件解析）→ 写入 PostgreSQL `measurement_store`，触发：BayBE 下一轮、偏差校正、**SDS 第 9 节理化值候选更新**。
- 冲突处理：同一测点多次测量保留全部，取值策略（最新 / 均值 / 人工指定）可配。
- **外部 ELN 为可选连接器**：已在用独立 ELN 的企业，通过 `ElnConnector` 双向同步；首推 eLabFTW 连接器，保留 Datalab 连接器（复用 `datalab_client`、`datalab_sync`）。外部 ELN 中的记录以「镜像 + 来源链接」形式进入 FormuNex，**发布门禁只认 FormuNex 库内、已复核的测量数据**。

#### 5.3.1 ELN 方案评估：Datalab 是否最佳？

**结论：Datalab 不是 FormuNex 的最佳方案。** 它适合学术材料实验室做轻量化的样品/表征记录，但 FormuMind 当前把它作为 Campaign 与实验数据的**唯一真源**（`campaign_backend` / `experiment_backend = datalab`，Docker ELN 栈 `DATALAB_REQUIRED=true`），这与 FormuNex 的 PLM 定位冲突：

| 问题 | 说明 |
|------|------|
| 真源割裂 | 实验与测量数据存于 Datalab（MongoDB），而发布门禁、哈希链审计、电子签名、数据溯源、RBAC 都在 FormuNex（PostgreSQL）。跨库无事务，门禁无法对「签批时刻的数据」做一致性快照 |
| 权限与审计两套 | Datalab 有独立的用户与权限体系，无法与 FormuNex 的项目级商业机密授权、四级签批、电子签名统一 |
| 合规能力弱 | 无电子签名含义、无可信时间戳、无防篡改审计；不满足受控 COA / 定型数据的追溯要求 |
| 领域不匹配 | 面向电池 / 材料表征（XRD、电化学等数据块），无涂料配方、批次 QC、出厂指标的概念 |
| 运维负担 | 额外的 Flask + MongoDB + Vue 栈；在「桌面版也要求完整组件」的前提下，每台研发电脑多跑 2~3 个容器 |
| 生态 | 学术项目，社区和长期维护力量有限 |

**候选方案对比**

| 方案 | 许可 / 形态 | 优点 | 不足 | 评价 |
|------|------------|------|------|------|
| **A. FormuNex 内置实验执行模块 + 连接器** | 自研（复用 FormuMind 现有 `experiments` / `measurement_store` / sqlite「本地台账」代码） | 单一真源、事务一致；RBAC / 审计 / 签名统一；DOE→实测→寻优→SDS/COA 零同步延迟；少一套容器；可按涂料 / 表面处理领域定制录入模板 | 需要自研录入 UI、仪器文件解析、附件管理（约 3~4 周） | **推荐** |
| B. eLabFTW（作为外部 ELN） | AGPL-3.0，PHP + MySQL，Docker | 成熟、工业界用户多；REST API v2；RFC 3161 可信时间戳、签名、模板、物品库存；支持中文界面 | 通用 ELN，无配方 / DOE 概念；仍是第二真源 | **外部 ELN 首选连接器** |
| C. 继续使用 Datalab | MIT，Flask + MongoDB | 已有适配代码（41 个源文件、35 个测试文件引用） | 见上表 | 降级为可选连接器 |
| D. Chemotion ELN | AGPL，Ruby | 化学合成（反应、分子）能力强 | 面向合成化学，配方 / 批次支持弱，部署重 | 不推荐 |
| E. openBIS | Apache-2.0，Java | ELN + LIMS 一体，适合大型机构 | 很重，学习与运维成本高 | 不推荐 |
| F. Kadi4Mat | Apache-2.0，Python | 材料科学研究数据管理 | 偏科研数据仓库，ELN 交互弱 | 不推荐 |
| G. 商业 ELN / LIMS（国产：鹰谷 InELN、创腾等；国际：Revvity Signals、LabArchives 等） | 商业授权 | 合规成熟（部分支持 21 CFR Part 11 / GMP）、厂商支持 | 授权费用；集成依赖厂商 API；配方寻优仍需 FormuNex | 企业已采购时走连接器 |
| H. 配方信息学平台（Uncountable、Albert Invent、Citrine 等） | 商业 SaaS | 自带配方 + 实验 + AI | 与 FormuNex 功能高度重叠（实际是竞品）；数据出境 | 不适用 |

**推荐方案（A + 连接器）设计要点**

1. **数据模型**（新增迁移 `0059_lab_execution`）：`lab_tasks`（由 DOE 试验点生成：任务单号、样品号、指派人、状态 待领取 / 进行中 / 待复核 / 已完成）、`samples`（样品号、批号、制备记录、条码）、`test_methods`（方法库：标准号如 GB/T 10125 / GB/T 9286 / GB/T 6750，单位，修约规则，有效范围）、`measurements` 扩展（`method_id`、`instrument_id`、`operator_id`、`raw_file_key`、`review_status`、`reviewed_by`、`external_ref`）、`instruments`（设备台账、校准有效期）、`eln_links`（外部 ELN 记录映射）。
2. **录入体验**：任务看板（按人 / 按项目）；RevoGrid 批量录入（按 DOE 矩阵行列）；扫码定位样品；常见仪器导出文件（CSV / Excel，如流变仪、色差仪、粘度计）解析模板可配置；附件与照片上传至对象存储。
3. **数据质量**：录入即校验（单位、合理范围、方法有效范围）；异常值提示（复用 `doe_anomaly`）；**测量复核**（录入人 ≠ 复核人）后才可用于 BayBE 训练、SDS 第 9 节和 COA。
4. **合规**：测量记录纳入哈希链审计；修改留痕（保留原值与修改原因）；仪器校准过期时阻止录入或标记。
5. **连接器框架**：`services/eln/connectors/{base,elabftw,datalab}.py`。`ElnConnector` 接口包括 `push_plan()`、`pull_measurements(since)`、`link(record)`、`health()`；幂等同步（基于外部记录 ID + 内容哈希），冲突进人工处理队列。
6. **迁移路径**：FormuMind 现有 Datalab 数据通过一次性脚本 `scripts/migrate_datalab_to_native.py` 导入 PostgreSQL；`campaign_backend` / `experiment_backend` 默认值改为 `native`，`datalab` 仅作为连接器配置保留。
7. **部署收益**：桌面 / 服务器 compose 栈移除 datalab-api、datalab-app、MongoDB 三个容器，桌面版内存需求预计从 16 GB 降到约 12 GB（需实测确认）。

### 5.4 W4 AI 寻优驾驶舱（Optimization & Trade-off Cockpit）

| 面板 | 复用 | 新增 |
|------|------|------|
| BayBE 迭代 | `baybe_engine`、`doe_cycle_service`、`convergence`、`acquisition_trend`、`doe_pause_store` | 收敛曲线（最优值 vs 轮次）、采集函数趋势、下一轮推荐点表（人工审核：采纳 / 修改 / 驳回，暂停与规则微调） |
| 预测偏差校正 | `prediction_bias_correct`、`predictor` | 预测 vs 实测散点 + 残差分布；**新增 `analysis/conformal.py`**（分割共形预测，给出 90% 预测区间，覆盖率实时监控） |
| 帕累托前沿 | `tradeoff_analysis`、`tradeoff_schemas` | ECharts 2D / 3D 帕累托散点，刷选联动配方详情；灵敏度（Sobol 指数 / 局部偏导）；**合规维度作为约束或目标**（如「危害评分」最小化） |
| 钦定定型配方 | — | 「设为定型」：写入 `winning_experiment_id`，生成产品修订候选，进入 W5 |
| 流变 | `rheology.py` | 流动曲线拟合（Power-law / Herschel-Bulkley / Cross）、触变指数、剪切变稀 |
| 色度 | `colorimetry.py` | CIE L\*a\*b\* 散点、ΔE\*ab / ΔE00 |
| 酸稳定性 | `acid_stability.py` | 趋势与判定 |

### 5.5 W5 PLM 文档与发布中心（核心新增）

#### 5.5.1 法规数据库合流（seed_regulatory_db）

数据管线（幂等，可重复执行，每次生成 `data_release` 版本号）：

1. **物质主数据**：`common_chemicals.json`（131,089）→ `substances`；CAS 校验位验证，非法 CAS 记录进隔离表待人工处理；中英名、分子式入库。**`iecsc_listed` 不能直接由此表推断**（来源未注明是否为 IECSC），需导入官方 IECSC 名录后再标记。
2. **危化品目录**：`hazard_chemical_info.json`（2,857，含 365 条无 CAS 的类属条目，如「××化合物」）→ 标记 `hazardous_catalog_2015`；类属条目单独建「类属规则」表（按名称 / 元素匹配，如「六价铬化合物」）。
3. **分类文本解析**：119 种中文危害类别写法 → 规范化词表 → `(hazard_class, category)` → H 代码（以 `ghs_standard_map.json` 为种子，补全为完整映射表）；`source='cn_catalog'`、`confidence=0.9`；解析失败的条目进复核队列。
4. **OEL**：`oel_gbz2_2019.json` → `substance_oel`。
5. **H/P 语句**：`hazard_statements.json`、`precaution_statements.json` → `ghs_statements`（中英）。
6. **运输**：现有 GB 12268 仅 10 条，**需补齐全表**（GB 12268 危险货物品名表约 3,000 条，人工整理或采购数据），本期先导入现有条目并在 UI 中对缺失 UN 信息提示人工填写。
7. **PubChem 补充**（Celery 后台，按需触发）：仅对配方中实际使用、且无 `manual` / `supplier_sds` / `cn_catalog` 来源的物质拉取 GHS 分类；`source='pubchem'`、`confidence=0.6`；**不自动锁定**。
8. **供应商 SDS 导入**：迁移 MSDS-Generator 的 `pdf_import_orchestrator` / `sds_parser_adapter`，与 FormuMind MinerU 管线合并 → 解析第 2 / 3 / 9 / 11 / 12 / 14 节 → 生成原料的 `material_compositions` 与分类；`source='supplier_sds'`、`confidence=0.95`。
9. **复核锁定**：EHS 专员在「法规数据复核」页面确认 → `source='manual'`、`locked`。

**有效值取用优先级**：`manual(锁定)` > `supplier_sds` > `cn_catalog` > `pubchem` > `inferred`。每次分类计算记录所用来源，SDS 中可追溯。

#### 5.5.2 GB 30000 混合物分类引擎（`services/plm/ghs/`）

```
ghs/
├─ engine.py            # 编排：输入展开 → 各危害类别分类器 → 汇总 → 标签要素
├─ inputs.py            # 配方 → 物质级组成（含区间、未知组分比例）
├─ rules/               # 每个危害类别一个模块，规则参数外置为 YAML（rule_set_version）
│  ├─ physical/         # flammable_liquid.py(GB 30000.7), metal_corrosive.py(.17), …
│  ├─ acute_tox.py      # GB 30000.18
│  ├─ skin.py, eye.py   # .19 / .20
│  ├─ sensitization.py  # .21
│  ├─ cmr.py            # .22 / .23 / .24
│  ├─ stot.py           # .25 / .26
│  ├─ aspiration.py     # .27
│  ├─ aquatic.py        # .28
│  └─ ozone.py          # .29
├─ label_elements.py    # 象形图优先级、信号词、H 去重合并、P 选取
├─ trace.py             # 逐条判定依据（可读中文解释）
└─ rulesets/gb30000-2013.v1.yaml
```

**规则要点**（实现以标准原文为准，EHS 专员签字确认参数表后冻结为 `rule_set_version`）：

| 危害类别 | 混合物判定方法 |
|----------|----------------|
| 易燃液体（GB 30000.7） | **仅用实测或供应商数据**：闭杯闪点 + 初沸点，分类别 1~4；无数据时标记「数据缺失」而非推算（可提示按最低闪点组分估算，仅作预警） |
| 金属腐蚀物（.17） | 仅接受 UN C.1 试验结果；无数据则「未分类（无数据）」 |
| 急性毒性（.18） | ATE 加和公式 100/ATEmix = Σ(Ci/ATEi)；仅有类别时用换算点估计值；**未知急性毒性组分 > 10% 时使用修正公式**并在 SDS 中声明「x% 的组分急性毒性未知」 |
| 皮肤腐蚀 / 刺激（.19） | 加和法：Σ类别 1 ≥ 5% → 类别 1；10 × Σ类别 1 + Σ类别 2 ≥ 10% → 类别 2；支持 SCL；pH ≤ 2 / ≥ 11.5 的组分按加和法不适用规则处理 |
| 眼损伤 / 刺激（.20） | 同上加和法（类别 1 / 2A / 2B） |
| 致敏（.21） | 通用浓度限值（1A / 1B 区分，0.1% / 1%）及 SCL |
| CMR（.22~.24） | 通用浓度限值（类别 1 ≥ 0.1% / 0.3%，类别 2 ≥ 1% / 3%）；哺乳效应附加类别 |
| STOT（.25 / .26） | 通用浓度限值（类别 1 ≥ 10% → 类别 1；1%~10% → 类别 2）；呼吸道刺激 / 麻醉效应（类别 3）加和法 |
| 吸入危害（.27） | Σ类别 1 ≥ 10% 且 40 ℃ 运动黏度 ≤ 20.5 mm²/s（**读取实测黏度**） |
| 水生环境（.28） | 加和法 + **M 因子**（急性 1 / 长期 1~3 / 长期 4 安全网） |
| 臭氧层（.29） | 浓度限值 |
| 桥接原则 | 稀释、批次、浓缩、内插、实质类似 —— 支持「以已分类的参考配方为基准」的人工桥接记录 |

**输出**：`ClassificationResult { hazards[], pictograms[], signal_word, h_statements[], p_statements[], unknown_toxicity_pct, data_gaps[], trace[] }`。`trace` 中的每条记录类似：「皮肤腐蚀类别 1：组分 A（类别 1，12.0%）≥ 5% → 判定类别 1（GB 30000.19 表 3）」。

**标签要素**：象形图优先级规则（有 GHS06 则不用 GHS07 等）、信号词（有「危险」则不用「警告」）、H 语句去重合并（H314 覆盖 H318 等）、P 语句按类别映射去重并按 GB 15258 选取规则精简（选取上限可配置）。

**测试**：迁移 MSDS-Generator 现有测试；新增 ≥ 60 个由 EHS 专员确认的**黄金案例**（每个危害类别的边界值 ±0.01%），以及「同一配方新旧引擎结果对比」的回归报告。

**接入点**：W3 实时侧边栏、`formulation_gate`、`recommend_pipeline` / `substitution` 候选过滤、DOE 试验点预评估、W5 文档生成、发布门禁。

#### 5.5.3 运输分类

迁移 `transport_engine` 系列：由分类结果推导 GB 6944 危险货物类别 → 匹配 GB 12268 UN 编号（具体品名优先，否则 N.O.S. 条目 + 技术名称）→ 包装类别（易燃液体按闪点 / 初沸点；毒性按 ATE）→ 海洋污染物标记。数据缺失时输出「需人工确认」，不自动编造 UN 号。

#### 5.5.4 SDS 生成（GB/T 16483-2008 + GB/T 17519-2013）

- **结构化构建**：`sds_builder` 生成 16 节 `content_json`，每个字段携带 `source`（computed / measured / material_sds / manual / template_default）。
- **第 2 节**：分类结果、标签要素、其他危害。
- **第 3 节**：物质展开后列出需披露组分；**商业机密区间化**：按可配置区间表（默认 `<1%`、`1–5%`、`5–10%`、`10–20%`、`20–30%`…）把精确值转区间，商业机密组分可用通用化学名替代 CAS（须符合 GB/T 17519 要求，且保留内部映射供审计）。
- **第 8 节**：GBZ 2.1 职业接触限值（MAC / PC-TWA / PC-STEL）。
- **第 9 节**：优先回填已复核的实测值（闪点、pH、密度、黏度、固含、初沸点），标注测试方法；无实测时用计算或原料数据并标注。
- **第 11 / 12 节**：组分毒理 / 生态数据 + 混合物计算值（ATEmix）。
- **第 14 节**：运输分类结果。
- **第 15 节**：中国法规清单自动匹配（《危险化学品安全管理条例》《危险化学品目录》、重点监管、易制毒、易制爆、IECSC 状态、GBZ 2.1 等）。
- **第 16 节**：修订记录（版本号、修订日期、变更摘要由 Diff 自动生成）。
- **中英文**：字段级双语；语句类内容来自 `ghs_statements`；自由文本支持 LLM 辅助翻译（`internal` 路由）后人工确认。
- **24 h 应急电话**：在公司配置中设置，强制非空。

#### 5.5.5 安全标签（GB 15258-2009）

- 标签要素：化学品标识、象形图、信号词、危险性说明、防范说明、供应商标识、应急咨询电话、资料参阅提示语。
- **模板**：Konva 设计器输出 `label_json`（元素、坐标 mm、字体、绑定变量 `{{product.name_zh}}` 等）；按 GB 15258 容器容积对应的最小标签尺寸给出校验提示。
- **渲染**：后端 ReportLab（迁移 `ghs_label_reportlab.py`）：矢量象形图（SVG → svglib）、思源字体嵌入、300 / 600 dpi PNG、PDF（CMYK 选项）、二维码（segno，指向 SDS 下载地址或产品编码）。
- **批量**：按批次 / 包装规格批量生成。

#### 5.5.6 TDS

- 数据来源：定型配方修订版、`winning_experiment` 实测值、`objective_contract` 中标记为对外公布的指标、`dossier` 中的产品描述与应用说明、`levers` 工艺窗口（施工参数）。
- 版式：迁移 MSDS-Generator `tds_document_layout` / `tds_table_model` 逻辑为模板；中英双语。
- 跨文档一致性：密度、固含、闪点、pH 等与 SDS 第 9 节**取同一数据点**（同一 `measurement_id`），从源头避免冲突。

#### 5.5.7 COA

- 输入：批次（`batches`）+ `qc_report` 实测值 + `objective_contract` 中标记为「出厂指标」的上下限与测试方法。
- 判定：逐项 PASS / FAIL（含单位换算、修约规则 GB/T 8170），任一 FAIL 时 COA 状态为「不合格，不可签发」。
- 受控：COA 编号规则、质量签字（电子签名）、批量生成。

#### 5.5.8 文档版本、Diff 与多语言校对

- 每次生成 / 编辑产生新 `regulatory_documents.version_no`；已发布版本不可变。
- **结构化 Diff**：按节 / 字段对比 `content_json`（新增 / 删除 / 修改），对分类变化、第 3 节组成变化**高亮为重大变更**（触发「SDS 需修订并重新签批」）；另提供渲染文本的逐行 Diff。
- 校对 UI：中英并排、字段来源标记、锁定字段、批注（复用评论模型）。

#### 5.5.9 渲染层

| 输出 | 引擎 | 说明 |
|------|------|------|
| SDS / TDS / COA → PDF | Jinja2 → HTML → **WeasyPrint** | CSS 分页（页眉页脚、「第 x 页 共 y 页」）、思源字体嵌入、PDF/A-2b 选项 |
| SDS / TDS / COA → Word | **docxtpl**（Word 模板 + Jinja 标签） | 企业可直接在 Word 中改版式后上传为模板 |
| 标签 | ReportLab | 物理尺寸精确 |

模板管理：`doc_templates` 按类型 / 语言 / 品牌版本化，后台上传与预览，默认模板随产品内置。

#### 5.5.10 发布门禁与签批

**门禁检查项（`release_gate.py`，扩展 `publication_preflight` + `numeric_check`）**：

| # | 检查 | 失败处理 |
|---|------|---------|
| 1 | 配方合计 = 100%（容差 0.01%） | 阻断 |
| 2 | 所有物质分类来源 ≥ 指定置信度，且关键组分已人工锁定 | 阻断 |
| 3 | `objective_contract` 关键指标全部达标（以 winning 实测为准） | 阻断 / 可审批豁免 |
| 4 | 合规红线零命中 | 阻断（不可豁免） |
| 5 | 分类结果基于当前 `rule_set_version` 和 `data_release`，且输入哈希未变 | 重新计算 |
| 6 | SDS(中 / 英) + TDS(中 / 英) + 标签 + 首批 COA 齐全，且均为 approved | 阻断 |
| 7 | 跨文档数值一致：密度、pH、固含、闪点、VOC、UN 号等在 SDS / TDS / 标签 / COA 中一致（引用同一数据点或在容差内） | 阻断 |
| 8 | SDS 第 2 节与标签要素一致（象形图、信号词、H / P） | 阻断 |
| 9 | 必填字段完整（应急电话、供应商信息、修订日期） | 阻断 |
| 10 | 数据缺口（`data_gaps`）已逐条处置 | 阻断 / 可审批豁免 |

**状态机**：`rd → candidate`（项目负责人钦定）→ `under_review`（门禁通过后提交）→ 4 级签批（研发 → EHS / 法规 → 质量 → 发布管理员；任一驳回回到 `candidate`）→ `released`（冻结修订版，生成 `release_package` 哈希，文档状态变为 `released`）→ 新修订发布时旧版本变为 `superseded`。

**电子签名**：重新验证密码；签名含义；对 `package_hash` 签名；写入哈希链审计日志。

**连接器（本期预留）**：`connectors/base.py` 定义 `PublishConnector` 接口（`publish(package)`、`withdraw()`），实现骨架：Webhook（HMAC 签名、重试、经 `http_safe` 防 SSRF）；ERP / 文档库 / 官网仅留接口与配置页占位。发布时生成**交付包 ZIP**（全部 PDF / Word + manifest.json + 哈希）供手工分发。

### 5.6 法规智能体（`agents/regulatory.py`）

只读工具集：查询物质分类与来源、解释某配方的分类 trace、给出降低危害等级的组分调整建议（调用 `substitution`）、回答国标条款问答（基于 PageIndex 索引的国标全文，需用户自行导入标准文本）。**不得修改锁定数据或直接生成发布文档**；所有建议都附带依据。

---

## 6. Phase 0：品牌重塑与文件变更清单

### 6.1 品牌与命名

| 项 | 旧 | 新 |
|----|----|----|
| 产品名 | FormuMind | **FormuNex（方枢）** |
| Python 包 | `backend/app`（名称不变） | 不变；`pyproject` 项目名 `formunex-backend` |
| 环境变量前缀 | `FORMUMIND_*` | `FORMUNEX_*`（**兼容期一个版本内同时读取旧前缀并打印弃用警告**） |
| 数据库默认名 | `formumind.db` | PostgreSQL `formunex` |
| Docker 镜像 / compose 项目 | formumind-* | formunex-* |
| 前端标题 / Logo / favicon | FormuMind | 方枢 FormuNex |
| 文档 | README、QUICKSTART、使用指南 | 全部改名并重写 |

### 6.2 文件变更清单（File Change Manifest，按目录汇总）

**迁入（从 FormuMind 原样复制，再做修改）**：`backend/`、`frontend/`（→ `frontend-legacy/`，重写完成后删除）、`deploy/`、`scripts/`、`docs/`、`docker-compose*.yml`、`install.*`。

**后端新增**

```
backend/app/api/v1/plm/{products,classification,sds,tds,coa,labels,templates,documents,release,approvals}.py
backend/app/api/v1/{identity,audit,substances,analysis,pageindex}.py
backend/app/services/pageindex/{tree_builder,patent_patterns,navigator,two_stage}.py
backend/app/services/multimodal_fusion.py
backend/app/services/doe_designs/{orthogonal_arrays,constrained_mixture,d_optimal,categorical}.py
backend/app/services/analysis/conformal.py
backend/app/services/plm/ghs/{engine,inputs,label_elements,trace}.py
backend/app/services/plm/ghs/rules/{physical/*,acute_tox,skin,eye,sensitization,cmr,stot,aspiration,aquatic,ozone}.py
backend/app/services/plm/ghs/rulesets/gb30000-2013.v1.yaml
backend/app/services/plm/regulatory/{substance_repo,source_priority,catalog_text_parser,pubchem_enrich,supplier_sds_import,generic_entries}.py
backend/app/services/plm/transport.py
backend/app/services/plm/documents/{sds_builder,tds_builder,coa_builder,label_builder,confidential_ranges,section15_regs}.py
backend/app/services/plm/render/{html_pdf,docx_render,label_pdf,fonts}.py
backend/app/services/plm/{diff,release_gate,cross_doc_consistency}.py
backend/app/services/identity/{passwords,tokens,rbac,providers}.py
backend/app/services/approvals/{state_machine,signatures}.py
backend/app/services/audit/hash_chain.py
backend/app/services/{llm_router,lineage}.py
backend/app/services/connectors/{base,webhook}.py
backend/app/services/eln/{lab_tasks,samples,test_methods,instrument_parsers,review}.py
backend/app/services/eln/connectors/{base,elabftw,datalab}.py
backend/app/api/v1/lab_execution.py
backend/scripts/migrate_datalab_to_native.py
backend/app/agents/regulatory.py
backend/app/resources/plm/{templates/**,pictograms/*.svg,fonts/README（思源字体获取说明）}
backend/app/db/alembic/versions/0051 ~ 0059（见 §4.3）
backend/scripts/seed_regulatory_db.py
backend/data/regulatory/{common_chemicals,hazard_chemical_info,oel_gbz2_2019,hazard_statements,precaution_statements,gb12268_2025,cn_hazard_text_map}.json
backend/tests/{plm/**,pageindex/**,identity/**,doe_designs/**}
```

**后端修改**

```
backend/app/main.py                   # 路由 v1 前缀、新中间件（审计、RBAC）
backend/app/config.py                 # FORMUNEX_ 前缀 + 兼容；PG 默认；LLM 路由配置
backend/app/middleware/api_auth.py    # → JWT + 服务账号 API Key
backend/app/db/models.py              # 新模型（或拆分为 models/ 包）
backend/app/services/llm.py           # 接入 llm_router
backend/app/services/{embodiment_drafts,deep_research/engine,chat_*}.py   # 挂载两阶段检索
backend/app/services/{substitution,recommend_pipeline,compliance_rules}.py  # 接入 GHS 引擎与红线
backend/app/domain/{formulation_gate,objective_contract,project_spec}.py    # 红线、出厂指标标记
backend/app/services/engines/doe_registry.py   # 注册新设计
backend/app/services/{publication_preflight,numeric_check,qc_report}.py
backend/app/services/kb_ingest.py     # 入库后建 ToC 树
backend/pyproject.toml                # 新依赖：weasyprint、docxtpl、reportlab、svglib、segno、argon2-cffi、pyjwt、authlib(可选)、ldap3(可选)、chemicals、thermo
```

**前端（重写）**

```
frontend/src/{app,api,shared,features/{knowledge,spec,lab,cockpit,plm,materials,admin},locales}/**
frontend/src-tauri/**                 # 新
frontend/package.json                 # 移除 ag-grid / tailwind；新增 antd、pro-components、
                                      # @revolist/react-datagrid、echarts、echarts-gl、react-pdf、
                                      # react-konva、ketcher-react、@tanstack/react-query、
                                      # react-router-dom、i18next、zod、openapi-fetch
frontend/e2e/**
```

**部署 / CI**

```
docker-compose.desktop.yml（新）、docker-compose.prod.yml（改，含 minio）
docker-compose.eln.yml（删除；外部 ELN 改为连接器配置）、deploy/eln/（改为 eLabFTW / Datalab 连接器部署说明）
deploy/helm/formunex/**（新）
.github/workflows/{backend,frontend,desktop-build,e2e}.yml
```

**MSDS-Generator 不迁入的部分**：`gui_pyside6/`（被 Web 重写取代）、`chemicals/`（改 pip 依赖）、`ghostscript/`（Windows 二进制；camelot 改用 lattice 以外的解析或容器内安装 ghostscript）、`fonts/`（微软字体授权问题）、`oel_eh40.json`（非国标）、`word_com_converter.py`（依赖 Windows COM）。

---

## 7. 质量保障

| 层级 | 内容 |
|------|------|
| 单元测试 | 沿用 FormuMind 586 个测试并保持全绿；GHS 规则每类别边界测试；PageIndex 建树测试 |
| 黄金案例 | ≥ 60 个 GB 30000 分类案例（EHS 专员签字）；10 份完整 SDS 人工审定基线 |
| 集成测试 | `test_pageindex_two_stage_rag.py`（长专利配对准确率）；`test_gb30000_ghs_calculator.py`；`test_release_gate.py` |
| 端到端 | `test_plm_lifecycle_e2e.py`：文献检索 → 设定契约 → DOE → 实验任务录入与复核 → BayBE 一轮 → 定型 → 分类 → 生成 SDS / TDS / 标签 / COA → 门禁 → 4 级签批 → 发布；Playwright 前端主路径 |
| 桌面 | Win 11 / macOS 14 安装、启动栈、导出 PDF、打印冒烟测试 |
| 安全 | RBAC 越权测试；审计日志防篡改测试；LLM 敏感路由测试（secret 请求绝不发往外部供应商） |
| 性能 | 实时合规 < 150 ms（50 组分）；13 万物质名模糊检索 < 100 ms；SDS 渲染 < 5 s |

---

## 8. 里程碑与工作量估算

假设团队：后端 3 人、前端 3 人、EHS / 法规专家 1 人（兼职审核）、测试 1 人。

| 阶段 | 内容 | 周期 |
|------|------|------|
| **M0 迁仓与品牌** | FormuMind 迁入、改名、环境变量兼容、PG 默认、CI 绿 | 1 周 |
| **M1 数据底座** | 0051~0059 迁移、法规库种子、物质 / 原料两层模型、RBAC 与审计 | 3 周 |
| **M2 GHS 引擎** | GB 30000 全类别规则、黄金案例、运输分类、实时评估 API | 4 周（与 M1 后半重叠） |
| **M3 PageIndex + W1 缺口** | 建树、两阶段检索、multimodal_fusion、评估集 | 3 周（并行） |
| **M4 文档引擎** | SDS / TDS / COA / 标签生成、模板、渲染、Diff | 4 周 |
| **M5 发布门禁与签批** | 门禁、状态机、电子签名、交付包、连接器接口 | 2 周 |
| **M6 DOE 与驾驶舱补强** | 正交表、约束混料、D-最优、Conformal | 2 周（并行） |
| **M7 实验执行模块** | 任务单、样品、方法库、仪器解析、测量复核、eLabFTW / Datalab 连接器、Datalab 数据迁移 | 3~4 周（并行） |
| **F1~F4 前端重写** | 应用壳与共享组件（3 周）→ W1/W2（4 周）→ W3/W4（5 周）→ W5 与标签设计器（5 周）；与后端并行 | 约 17 周 |
| **D1 Tauri 桌面** | 壳、Docker 编排、首启向导、签名与公证 | 3 周（与前端后期并行） |
| **QA 与发布** | E2E、黄金案例审定、桌面冒烟、文档 | 3 周 |

**总周期约 6~7 个月**（关键路径为前端一次性重写）。如需更早交付价值，可先发布「后端 + 旧前端 + W5 新页面」的过渡版本。

---

## 9. 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| 法规计算错误导致 SDS 不合规 | 法律责任 | 规则参数表由 EHS 专员签字冻结；黄金案例回归；**所有 SDS 发布必须经 EHS 人工签批**；界面明示「计算结果需专业人员复核」 |
| 法规数据缺口（毒理数值、GB 12268 全表、IECSC 名录） | 分类准确性 | 来源分级与置信度；数据缺口在门禁中强制处置；补全数据列入 M1 任务（可考虑采购） |
| 前端一次性重写周期长、功能遗漏 | 延期 | 功能映射表为验收基线；旧前端并行保留至验收 |
| Docker Desktop 许可与资源占用 | 成本 / 体验 | 告知许可条款；提供离线镜像包；最低配置检测 |
| WebView 差异 | 显示不一致 | 文档均服务端渲染；Win / mac 冒烟测试纳入 CI |
| 字体授权 | 侵权 | 只用思源 / Noto（SIL OFL） |
| 敏感配方外泄到外部 LLM | 商业机密 | secret 级强制私有路由，无私有模型时拒绝；审计 |
| PageIndex 延迟与成本 | 体验 / 费用 | 限定场景、跳数 / token 预算、摘要延迟生成、结果缓存 |
| 内置实验执行模块替代 Datalab 后实验员不适应 | 采纳率 | 录入界面按 DOE 矩阵设计、支持扫码与仪器文件导入；已有 ELN 的企业继续通过连接器使用原系统 |
| 国标版本更新（如 GB 30000 修订、GB/T 16483 修订） | 合规时效 | 规则集版本化（`rule_set_version`），新规则集上线时批量复算并提示受影响产品 |

---

## 10. 待你确认后启动的第一批编码任务

1. **M0**：FormuMind 迁入 FormuNex 仓库、品牌重塑、`FORMUNEX_` 环境变量兼容、PostgreSQL 默认、CI 全绿。
2. **M1**：迁移 0051~0059 + `seed_regulatory_db.py`（含中文分类文本解析器及其测试）。
3. **M3**：`services/pageindex/` 建树 + 两阶段检索 + `test_pageindex_two_stage_rag.py`。

> 注：原提示中要求的 `0051_materials_regulatory_ghs` / `0052_plm_product_releases` / `0053_plm_regulatory_documents` 三个迁移，在本方案中因引入「物质 / 原料两层模型」与 RBAC、审计而拆分为 0051~0058 八个迁移，内容是原三项的超集；v1.1 再增加 `0059_lab_execution`。

---

## 附录 A：实施进度（2026-10-16）

> 口径说明（2026-10-18 更新）：后端测试 `pytest --collect-only -q` 实测收集 **5756 项**；前端 38 个测试文件，2026-10-18 迁移验证时实测 **190/190 通过**，当前数以 CI 为准。各里程碑行内的测试数为当时的验证值（历史记录），不追溯改写。

| 里程碑 | 状态 | 交付内容 | 验证 |
|--------|------|----------|------|
| **M0 迁仓与品牌** | ✅ 完成 | FormuMind 迁入并更名 FormuNex；`FORMUMIND_*` → `FORMUNEX_*`（`backend/app/env_compat.py` 兼容旧前缀并告警；Datalab 记录标识保持不变）；`docker-compose.yml` 新增 PostgreSQL 并作为主库；历史文档归档至 `docs/archive/formumind/` | 后端全量测试通过；前端 687 项 vitest 通过、tsc 无错误 |
| **M1 数据底座** | ✅ 完成（服务层） | 迁移 0051~0059（冻结表结构、幂等，自 0050 旧库升级后与模型零漂移）；法规库导入 `backend/scripts/seed_regulatory_db.py`：131,516 种物质、7,986 条分类、373 条 GBZ 2.1 限值；CAS 校验与 Excel 损坏修复（日期 / 日期序列号，共 92 条）；《危险化学品目录》中文分类文本解析（94% 无需复核，其余进复核队列）；RBAC 权限矩阵、argon2id 密码、哈希链审计日志 + 数据库只追加触发器 | 迁移、导入、解析、RBAC、审计专项测试 |
| **M2 GB 30000 分类引擎** | ✅ 完成（引擎 + API，规则集待 EHS 签核） | `services/plm/ghs/`：规则参数外置于版本化 YAML（`gb30000-2013.v1`，状态 draft）；急性毒性 ATE 加和（含未知组分 >10% 修正、按吸入形态换算）、皮肤 / 眼加和法（子类别、SCL、不适用加和组分、极端 pH）、致敏 / CMR / STOT 浓度限值、STOT SE3 按效应加和、吸入危害（需 40 ℃ 运动黏度）、水生急性 / 长期加和（M 因子）、臭氧层；易燃液体只认实测闪点 / 初沸点，金属腐蚀只认 UN C.1，其余物理危害一律提示试验；标签要素（象形图优先规则、信号词、H 去重、H→P 映射与 GB 15258 标签 6 条精选）；运输分类（UN 2.0.3.3 优先表、N.O.S. / 涂料条目、技术名称，始终标记需人工确认）；原料 → 组分展开（范围取上限、未申报部分计入未知）；结果持久化（`classification_results`，含 inputs_hash 与数据版本）；API：`POST /api/plm/ghs/classify`、`POST /api/plm/ghs/classify/formulation-versions/{id}`、`GET /api/plm/ghs/ruleset`、`GET /api/plm/substances` | 109 项规则边界测试 + 10 项 API/数据库测试；真实法规库冒烟测试 |
| **M3 PageIndex** | ✅ 完成（服务层 + API） | `services/pageindex/`：入库即建目录树（标题层级、专利章节/实施例/表题标记、表格 sidecar、交叉引用边，不调用 LLM）；两阶段检索（hybrid_search → 目录树导航，LLM 模式 + 启发式回退，预算控制，可审计 `node_path` / `reasoning_trace`）；挂载深度研究（`pageindex_evidence`）与实施例草稿（配方表 ↔ 性能表关联）；API：`GET /api/kb/sources/{id}/toc-tree`、`POST …/toc-tree/rebuild`、`POST /api/kb/pageindex/search` | 合成长专利评估集（3 篇 / 4 问）：跨页关联覆盖率 第二阶段 4/4，仅第一阶段 3/4 |
| **M4 文档引擎** | ✅ 完成（服务层 + API，模板文本待 EHS 审核） | `services/plm/documents/`：结构化内容先于渲染，每个字段标注来源（computed / measured / product / company / template）与缺项清单；SDS（GB/T 16483 十六部分，第 2/3/14 部分取自 M2 分类结果，GB/T 17519 商业秘密浓度区间披露，GBZ 2.1 职业接触限值，目录 / 剧毒 / 易制毒 / 易制爆标注）、TDS（典型物性绑定实测记录）、COA（GB/T 8170 修约、全数值 / 修约值比较判定，PASS / FAIL / INCOMPLETE，不合格不可出具）、标签（GB 15258 尺寸校核、象形图、信号词、H/P 精选、二维码）；`services/plm/render/`：PDF 统一用 ReportLab（矢量 GHS 象形图、页眉页脚“第 x 页 共 y 页”、标签按物理毫米尺寸输出），Word 用 python-docx，可上传 docxtpl 模板；文档版本化（每次生成新版本、`supersedes_id`、字段级差异与重大变更标记，冻结修订禁止重新生成 SDS/TDS/标签）；产品 / 修订（A、B…）/ 批次 / 质检结果；API：`/api/plm/products`、`…/revisions`、`…/batches`、`/api/plm/batches/{id}/qc-results`、`/api/plm/revisions/{id}/documents`、`/api/plm/documents/{id}`（含 `file/{pdf\|docx}`、`diff`）、`POST /api/plm/templates` | 36 项文档专项测试（区间披露、修约、判定、尺寸、差异、渲染、API 端到端）；PDF / 标签版式人工目检 |
| **M5 发布门禁与签批** | ✅ 完成（服务层 + API） | **账号与会话**：`POST /api/identity/login`、`refresh`、`logout`，HS256 访问令牌（15 分钟，仅接受 HS256、校验签名 / 过期 / 签发方）+ 可吊销刷新会话（`user_sessions` 只存哈希，httpOnly Cookie，轮换），登录失败 5 次锁定 15 分钟，改密吊销全部会话；中间件同时接受服务 API Key 与账号令牌，但签批 / 审核 / 豁免 / 用户管理只认账号；首个管理员用 `scripts/create_user.py` 创建。**发布门禁** `services/plm/release/gate.py`：方案 §5.5.10 十项检查（配方合计、分类来源与关键组分锁定、目标契约（已复核实测）、合规红线（RoHS + 规格红线）、分类结果现行性（按生成时输入重算比对哈希 / 规则集 / 法规数据版本）、必备文档齐全且已审核（可配置 `FORMUNEX_PLM_RELEASE_REQUIRED_DOCS`）、跨文档数值一致（密度 / pH / 固含 / 闪点 / VOC / UN 号，带容差）、SDS 第 2 部分与标签一致、必填字段、数据缺口）；仅“目标指标”与非阻断“数据缺口”可电子签名豁免。**签批**：文档审核签名（编制人不能审自己的文档，COA 需质量签发）；状态机 草稿 → 候选 → 签批中 → 已发布 / 被替代 / 已撤回；四级签批（项目负责人 → EHS/法规 → 质量 → 发布管理员），每步须持有该角色、重输密码、同一人不能签两步，任一驳回回到候选；电子签名绑定用户、含义、时间与发布包哈希，可校验；每次签名前重新核对已冻结文件，被改动即拒签。**交付包**：清单含每份文档内容哈希与 PDF / Word 文件哈希，`package_hash` 覆盖客户实际收到的文件；发布时生成 ZIP（全部文件 + manifest.json + 签名记录）。**连接器**：`PublishConnector` 接口 + Webhook 实现（HMAC-SHA256 签名、5xx 重试、SSRF 安全客户端），失败只记录不回滚发布；ERP / 文档系统 / 官网仅列为预留。全部操作写入哈希链审计，`GET /api/plm/audit` 附链完整性校验 | 17 项发布专项测试（令牌伪造 / 过期 / alg=none、锁定、会话吊销、权限、门禁各项、豁免、完整发布、驳回、篡改拒签、替代旧版、Webhook 签名与重试）；迁移 0060 自 0050 旧库升级零漂移 |
| **M6 DOE 与驾驶舱补强** | ✅ 完成（服务层 + API） | **正交表** `services/doe_designs/orthogonal_arrays.py`：按有限域构造 L4/L8/L16/L32/L64、L9/L27/L81、L16(4^5)/L64(4^21)、L25(5^6)，另含 L12（Plackett–Burman）与 L18(2^1×3^7)，全部经正交性校验；二水平表沿用 Taguchi 列号（1×2→3、1×4→5）；因子按回溯分配列，交互作用列不与任何因子 / 交互冲突；自动选最小可用表，二、三水平混合用 L18 或拟水平。**约束混料** `constrained_mixture.py`：上下限自动收紧与相容性检查，McLean–Anderson 极端顶点 + 棱中点 / 三分点 + 面心 + 总质心 + 轴向检验点作为候选集；**D-最优** `d_optimal.py`：Fedorov 交换（多起点），Scheffé 线性 / 二次 / 特殊三次（伪组分），连续过程因子（线性 / 交互 / 二次），分类因子（效应编码），混料 × 过程完全交叉或加性模型；报告 D 效率（含相对候选全集）与最大预测方差。**DOE 合规预筛** `compliance_screen.py`：每个试验点按原料展开为物质后跑 GB 30000 分类，检查物质红线（与发布门禁同格式）、RoHS、禁用象形图、禁用信号词、危害评分上限，可一键剔除。**驾驶舱**：分割共形预测区间（绝对 / 按 σ 归一化）+ 按时间顺序的覆盖率监控与漂移告警；帕累托排序可把“危害评分”（`ghs/hazard_score.py`，仅用于排序）作为最小化目标，由组成自动计算；“设为定型”从定型实验生成产品修订（继承规格、记录数据链、直接进入候选态）。设计可选写入现有 `doe_plans`。API：`/api/doe/designs/{orthogonal-arrays,orthogonal,mixture,d-optimal,screen}`、`/api/cockpit/{conformal,pareto,winning}` | 28 项专项测试（全部正交表正交性、Taguchi 列号与交互列、McLean–Anderson 照明弹例题顶点与独立枚举一致、约束满足、Fedorov 优于随机、交叉模型参数、共形分位数 / 覆盖率 / 漂移告警、危害评分、API 端到端含合规预筛与“设为定型”后门禁目标检查通过） |
| **M7 实验执行模块** | ✅ 完成（服务层 + API） | **原生台账为默认**：`experiment_backend` / `campaign_backend` 默认 `native`（FormuNex 自有数据库为唯一真源），基础 compose 与 `.env.example` 不再强制 Datalab，`docker-compose.eln.yml` 仍可显式启用。**方法库**：20 项常用国标方法预置（盐雾、划格、拉开法、铅笔硬度、干燥时间、不挥发物、密度、涂-4 杯 / 旋转黏度、细度、冲击、弯曲、膜厚、光泽、色差、闭口闪点、VOC、pH、运动黏度、电导率；标准号不含年代号，由实验室核对现行版本），可增改。**仪器台账**：校准到期即拒绝录入（可配置为仅告警）。**任务单**：DOE 计划一键下发（每个试验点一张任务单 + 条码样品 + 实验记录，幂等，可携带模型预测值），领取 → 录入 → 提交 → 复核 → 完成，可取消。**录入校验**：必须注明方法；指标 / 单位与方法一致；方法有效范围；按方法小数位 GB/T 8170 修约并保留原始读数；同一 DOE 计划内稳健 z 值离群提示。**复核**：录入人不能复核自己的数据，驳回需理由并退回录入人；只有已复核值进入实验的 `measured`（优化、SDS 第 9 节、发布门禁读取的视图）。**修改留痕**：`measurement_amendments` 保存原值、新值、理由，已复核值被修改即回到待复核。**重复测量**：全部保留，取值策略 最新 / 均值（`FORMUNEX_LAB_VALUE_STRATEGY`），复核人可人工指定。**仪器文件解析**：可配置 CSV / Excel 模板（宽表 / 长表、别名列、读数聚合、UTF-8 / GB18030、可选小数逗号），内置色差仪 / 旋转黏度计 / 光泽仪 / 通用长表模板；原始文件存档并关联每条测量值。**数据回流**：`GET /api/plm/revisions/{id}/property-candidates` 给出 SDS 第 9 节实测候选值；`POST /api/cockpit/conformal/from-lab` 用任务单预测值与复核实测值自动校准共形区间。**ELN 连接器**：`ElnConnector` 接口；eLabFTW（API v2：任务单推送为实验 + 每个指标一个数值字段，回读填写值）；Datalab 只读；同步幂等（远端 / 本地双摘要），远端新值以“待复核”进入队列，两侧都改则进入 `eln_sync_conflicts` 人工处理（保留本地 / 采用远端）。**Datalab 迁移**：`scripts/migrate_datalab_to_native.py`（支持 dry-run、幂等，迁入值标记为 `legacy`：保留在优化数据中，但发布门禁不认，须复测或复核）。迁移 0061；全部操作写入哈希链审计 | 13 项实验执行测试（方法库与权限、下发幂等、录入校验与修约、仪器校准阻断 / 告警、离群提示、复核 / 驳回 / 修改留痕 / 取值策略 / 人工指定、解析模板（GB18030、Excel、小数逗号、聚合）、仪器文件导入、SDS 候选与共形自动取数、eLabFTW 推送 / 回读 / 冲突处理、Datalab 只读与迁移、原生默认后端）；迁移 0061 自 0050 旧库升级零漂移 |
| **F1–F4 前端重写（第一批）** | ✅ 完成（已切换，旧前端已删除） | 新前端已切换为 `frontend/`，旧前端已删除（§3.4）。技术栈：Vite + React 18 + TypeScript（strict）+ Ant Design 5 / ProComponents、TanStack Query、Zustand、react-router（懒加载）、i18next（中文为默认，英文 614 条键全覆盖，构建前校验）、ECharts、RevoGrid、react-konva；API 类型由 OpenAPI 自动生成（`npm run gen:api`），令牌只存内存、401 时用 httpOnly Cookie 刷新并重试一次。**F1 壳与公共组件**：登录 / 改密、五步导航、项目切换、全局搜索、明暗主题、GHS 象形图、状态 / 来源标签、权限守卫、电子签名弹窗、PDF 预览、结构化差异、可编辑行表；用户与角色、审计日志（含链校验）、连接器管理。**F2 知识与规格**：检索、PageIndex 深度检索（推理轨迹）、资料库与目录树原文、项目与规格契约编辑（工艺窗口 / 红线 / 出厂 / 验收）。**F3 配方、实验、驾驶舱**：配方编辑器（RevoGrid，合计 / 合规实时校核）、DOE 设计器（正交 / 约束混料 / D-最优 + 合规预筛 + 下发任务单）、任务看板与详情（批量录入、仪器文件导入）、复核队列、方法与仪器、ELN 同步与冲突、帕累托（危害评分目标）、共形覆盖率、“设为定型”。**F4 产品与合规**：产品 / 修订 / 规格、文档生成与校对（结构化内容按来源着色、缺项、差异、PDF 预览）、发布门禁清单（豁免需签名）、签批包与四级签批、待我签批面板、标签设计器（拖拽元素、批次变量、溢出与必备要素检查、服务端 PDF 预览，模板保存后用于标签生成）。为此后端新增：`GET /api/plm/products`（列表）、`GET /api/plm/revisions/{id}`、`PUT /api/plm/revisions/{id}/spec`、`GET /api/plm/packages`、标签模板 `/api/plm/label-templates`（列表 / 详情 / 保存 / 预览），`services/plm/render/label_layout.py` | 后端全量 5535 项通过；新增 4 项接口测试；前端 tsc 无错误、11 项 vitest（配方计算、i18n 键覆盖、令牌刷新、门禁清单、标签布局）、生产构建通过；Playwright 冒烟（登录 → 仪表板 → 产品 → 修订 → SDS 校对 → 门禁 → 标签设计器 → 配方编辑器 → 任务 → 英文审计）在本地全绿并人工检查截图；CI 新增 `frontend` 作业（类型检查 / 单测 / 构建），冒烟测试需运行中的后端，暂不进 CI |
| **F5 旧前端功能迁移** | ✅ 完成（已切换，2026-10-18） | 按 `docs/frontend-feature-map.md` 把旧前端 123 个组件迁入 `frontend/`：已迁移 114、废弃 / 取代 9（收尾前为 107 / 部分 7）。新前端按功能模块组织（`src/features/<模块>/module.tsx` 登记路由与导航，英文词条按模块拆到 `src/locales/en/`），新增模块：知识问答（流式、会话、引用脚注、深度模式、审批、证据审计、任务中心、健康横幅、全局错误边界）、资料库（入库、文献库、引用清单与冻结、集合、检索探针、质量、记忆、NotebookLM、解析与结构识别）、研究（外部检索、深度研究报告、研究产出版本与预检、专利 / IP 分析、实施例草稿、技术报告导出）、Wiki 与知识图谱、配方历史（实验记录、行详情与差异、版本树、证据谱系、AI 推荐、排行榜）、设计工具（原料替换、逆向设计、相似配方、QC 报告、经典 DOE、DOE 历史、因子）、工作台与驾驶舱（实验台账、BayBE 闭环、代理模型、响应曲面、平行坐标、偏差趋势）、需求（自由约束、影响追溯、契约历史回滚、主路径向导、项目资料、原料库）、设置中心（模型 / 密钥 / 能力 / 环境开关 / 依赖 / 知识库维护、MCP 审批与连接器、组织看板）。后端随之：设置、依赖、技能、嵌入模型、连接器的写操作对登录账号强制 `admin.llm` / `admin.connectors`（此前只靠前端隐藏）；问答接口新增 `deep`（PageIndex 两阶段检索并入证据，失败有提示）；`/health` 区分原生台账（默认，不再误报“Datalab 不可达”） | 前端 tsc 无错误、32 个测试文件 175 项 vitest、生产构建通过（旧前端 687 项 vitest 仍通过）；`e2e/routes.spec.ts` 在运行中的后端上逐一打开全部约 70 个页面（未选 / 已选项目各一遍）无异常、控制台错误、错误边界或 5xx，`e2e/smoke.spec.ts` 主路径通过；后端全量 5608 项通过 |
| **D1 Tauri 桌面端** | ✅ 完成（代码与构建；Win / mac 真机冒烟待做） | 外壳 `frontend/src-tauri/`（Tauri 2，Rust）：本机单机版 / 连接服务器两种方式，设置存 `desktop.json`（不含密钥）。**栈编排**：调用 `docker` CLI（自动查找 PATH 与 Docker Desktop 安装位置），`docker compose -p formunex` 驱动 `deploy/desktop/docker-compose.desktop.yml`（postgres、redis、neo4j、一次性 migrate、api、worker、beat；端口只绑 127.0.0.1；命名卷保存数据；`FORMUNEX_ENVIRONMENT=production` 强制登录），首次生成数据库 / 图数据库密码与 JWT 密钥写入权限 600 的 `.env`，之后不再改动；启动 / 停止 / 重启 / 拉取 / 导入离线镜像包输出实时推送到界面；`pg_dump -Fc` 备份（保留 10 份），升级镜像前强制备份。**首启向导**（`/setup`）：选择方式 → 检测 Docker（引擎、compose v2、内存建议 ≥ 12 GB，未装给下载链接）→ 拉取或导入镜像 → 启动并轮询 `/health` 与服务状态 → 在容器内创建管理员（密码自动生成，经环境变量传入，只显示一次并存入系统钥匙串）；连接服务器只接受 https（本机可 http）并先探测 `/health`。**会话**：界面与 API 不同源，刷新令牌按服务器存系统钥匙串、放请求体；所有 API 请求按所选服务器改写地址。**其他**：系统“另存为”对话框导出文件、`formunex://` 深度链接 + 单实例、后台任务完成的系统通知、签名更新（仅在配置公钥时启用）、“系统管理 → 桌面端与本机服务”页（状态、日志、备份、升级、检查更新）、登录页显示当前服务器。后端：CORS 暴露响应头、桌面来源放行测试、桌面 compose 变量登记。CI：`desktop-shell` 作业（rustfmt / clippy / 单测）；`desktop-release.yml` 在版本标签时构建 Windows / macOS（arm64、x64）/ Linux 安装包（签名、公证、更新签名由密钥开启）；`images.yml` 发布后端镜像到 GHCR 并生成离线镜像包。文档 `docs/desktop.md` | Rust 18 项单测（配置与地址校验、compose 参数、`ps` 两种输出格式、密钥只生成一次与文件权限、备份写入与轮转、管理员密码只经环境变量、深度链接、健康检查判定，栈操作用模拟 docker 可执行文件验证）、clippy 无警告；前端 38 个测试文件 190 项 vitest（API 地址改写、钥匙串刷新流程、另存为、向导连接 / 不可达 / 缺 Docker）；在本环境安装 WebKitGTK 后 `tauri build` 产出 `FormuNex_0.2.0_amd64.deb`（6.7 MiB），并用 tauri-driver + WebKitWebDriver 在 Xvfb 下驱动真实桌面程序完成“首启向导 → 连接服务器 → 登录 → 工作台 → 产品页”（`e2e-desktop/smoke.mjs`）；后端全量通过 |

**尚未完成、留待后续里程碑的部分：**
- GB 30000 规则集为 draft：所有阈值 / 换算值需 EHS 专员逐项核对签核后改为 approved；黄金案例（真实配方 + 专员确认结果）尚待建立。
- 物质库只有《危险化学品目录》分类，目录外物质一律按“危害未知”计入（真实配方冒烟测试中未知占比可达 80%）；需通过供应商 SDS 导入或法规专员标记 `not_classified` 补齐。
- 桥接原则（`services/plm/ghs/bridging.py`，引擎已调用）与气溶胶分类（`rules/physical.py:aerosol`，8 项测试覆盖类别 1/2/3、点火距离、泡沫试验）已实现；气体分类、有机过氧化物等需试验的物理危害仍未实现计算，只提示数据缺口。
- 账号登录与签批相关接口已按 RBAC 鉴权；其余既有接口（知识库、配方、DOE 等）仍只校验服务 API Key，逐接口接入账号权限随前端重写（F1）进行；LDAP / OIDC 仍为预留。
- `ghs_statements` 目前只有中文文本，英文 H/P 语句待补；GB 12268 仅 10 条，全表待补；IECSC 名录未导入（`iecsc_listed` 保持未知）。
- PageIndex 节点摘要（`summaries_status`）延迟生成已接线：Celery 任务 `formunex.toc_summaries` + `POST /kb/sources/{id}/toc-tree/summarize`（202，显式触发）；入库自动触发未见。Chat「深度模式」与知识中心目录树界面已随 F2 交付。
- **M4 偏离方案说明**：方案原定 WeasyPrint 渲染 PDF，但后端镜像无 apt 层、无法安装 pango 等系统库，改用纯 Python 的 ReportLab；中文字体按 `FORMUNEX_PLM_DOC_FONT_PATH` → 系统 TrueType 中文字体 → 内置 CID 字体（不嵌入，带告警）顺序选取，生产镜像需挂载可嵌入的 TrueType 中文字体。
- 文档引擎：标签已输出 PDF + PNG（`render/label_png.py`，72~600 dpi，6 项测试含下载 API；打印机格式待定）；SDS 第 4~8、10~13、15 部分的模板文本为通用草稿，需 EHS 专员审核；英文版 SDS 的 H/P 语句因法规库缺英文文本而显示为缺项；文档签批与发布门禁已在 M5 接入。
- M5：门禁“目标契约”检查读取修订版 `spec_snapshot.acceptance` 与定型实验中已复核的测量值，规格契约编辑界面随 F3 交付；“关键组分”阈值（≥10%，或有危害且 ≥1%）与跨文档容差为代码常量，需 EHS / 质量确认；生产环境必须设置 `FORMUNEX_AUTH_JWT_SECRET`（≥32 位），否则登录返回 503 且启动时告警；交付包只含 PDF / Word，标签打印格式待定。
- M6：灵敏度分析（Sobol 指数）与流变 / 色度 / 酸稳定性面板沿用原有服务，未新增；共形区间可由调用方提供配对，或经 M7 从任务单预测值与复核实测自动取数；危害评分权重为排序用启发式，不是法规量。
- M7：工作台（Campaign）在 Datalab 中的行数据未纳入迁移脚本（只迁移训练实验记录），仍在用 Datalab 工作台的部署需保留 `campaign_backend=datalab` 直至工作台迁移完成；仪器文件解析模板需按实验室实际仪器导出格式补充；方法库预置的有效范围与修约位数为通用值，需实验室负责人确认；BayBE 等既有优化代码读取 `measured` 视图，原生任务只在复核后写入，但 Datalab 模式下的旧数据路径未改动。
- **前端切换已完成（2026-10-18）**：`88f5aa0`（旧 `frontend/` 删除、新前端重命名为 `frontend/`）+ `c3843e8`（Dockerfile/nginx 移植、CI 切换、lint 门禁、openapi 再生）。功能映射表已全部为“已迁移 / 废弃”（114 / 9）。F5 收尾补齐了：服务器任务列表（`GET /api/tasks`）、问答行动包、外部检索结果对应知识库全文（`POST /api/kb/sources/resolve`）、上传按项目归属（`/api/ingest(/batch)` 的 `project_id`）、单篇重新解析（`POST /api/kb/sources/{id}/reparse`）、`@` 引用真正限定问答检索、单条实验 / DOE 计划详情（`GET /api/experiments/{id}`、`GET /api/doe/plans/{id}`）、AI 主动选点计划在台账 id 无效时不再静默丢失、`doecycle-status` 正确路径（旧拼写保留为隐藏别名）、台账附件数批量接口 + 撤销 / 重做 + 右键菜单、原生台账行版本（快照写入审计日志，仅从此之后的修改有历史）、排行榜单卡 PDF 与 KG 关系洞察、研究产出谱系列表、MCP 审批显示参数（敏感键打码后才落盘）、显式清空项目会话资料、知识图谱按项目过滤、密钥只在显式 null 时清除。切换后仍需按功能表做一轮人工业务验收（目前只有全路由渲染巡检与主路径冒烟）；行动包跳转到经典 DOE、闭环、逆向设计、原料替换、专利分析时尚未自动带入问题文本；`/api/tasks` 在多用户模式下不显示无 owner 的旧任务。
- **D1 桌面端未验证的部分**：本环境没有 Docker 引擎，本机栈的拉取 / 启动 / 迁移 / 建管理员 / 备份只在模拟 docker 上做了单元测试，真实 Docker Desktop 上的首装、启动、导出 PDF、打印冒烟（方案 §7，Windows 11 / macOS 14）仍需真机完成；安装包签名、公证与更新签名需配置证书密钥；12 GB 内存建议值待实测；后端镜像尚未在 GHCR 发布过（首个版本标签触发 `images.yml`），离线镜像包数 GB，超过 GitHub 发布附件上限需另行分发；Linux 容器内无 Secret Service，钥匙串写入失败时会话只保留到程序关闭；法规库导入未纳入向导。
- 真实长专利标注集（方案 §5.1.1 的 30 篇）尚需收集；当前评估集为合成数据，仅证明机制可用，不代表真实准确率。

## 附录 B：已接受偏离（2026-10-18 更新）

执行中部分要求已偏离本方案原定设计，盲目“补回”成本高、收益低。以下偏离经决策后接受，记入文档，不再算缺口；补齐计划不再立项补回。

| # | 偏离 | 理由 | 决策 |
|---|---|---|---|
| D-1 | `/api/v1` 未实施，路由平铺 `/api/...`，无 307 重定向 | v1 前缀对单体应用无实际收益；补回需全部 403 路由改前缀 + 客户端同步，破坏性变更 | 2026-10-18 接受偏离，记为已废弃要求 |
| D-2 | 渲染层用 ReportLab 而非方案原定的 WeasyPrint（Jinja2→HTML→WeasyPrint） | 后端镜像无 apt 层、无法安装 pango 等系统库；ReportLab 纯 Python 已稳定出图；切换需全部 PDF 模板重写 + 排版回归（附录 A M4 偏离说明亦有记录） | 2026-10-18 已同意方案，WeasyPrint 不再补 |
| D-3 | CI 为单体 `ci.yml` + `desktop-release` / `playwright-nightly` / `docker-smoke` 等专项文件，非方案的 `{backend,frontend,desktop-build,e2e}.yml` 四分 | 现有 CI 作业覆盖已全 | 2026-10-18 接受偏离，不拆分 |
| D-4 | 部署：Helm Chart 延后；TLS 应用内不终止；MinIO 延后；pgvector 不做 | Helm：单机 compose 已满足当前部署模型，有 K8s 客户需求时再做。TLS：TLS 终止属于边缘反代，不进应用，见 `deploy/reverse-proxy/` 反代 TLS 示例 + 部署文档。MinIO：单机本地卷够用，S3 抽象已预留，接入只增运维负担。pgvector：现有检索引擎可用，迁移向量库高成本高风险、无实测收益 | 2026-10-18（Cheng 委托最优方案） |
| D-5 | RDKit.js 暂不做 | Ketcher 自带渲染，smiles-drawer 覆盖只读描绘需求，暂不引入 wasm 重量；Ketcher 做（结构绘制核心交互，见补齐计划 D-5） | 2026-10-18（Cheng 委托最优方案） |

### 与整合方案的边界

Logo / 公司配置（含 Logo 上传、公司信息、文档模板配置）与 TS 模块、批量导出、配方数据库映射由《FormuNex 整合 MSDS-Generator_PLM 方案》覆盖，本开发方案不重复立项。两方案重叠处以整合方案为准。

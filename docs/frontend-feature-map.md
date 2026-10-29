# 前端功能映射表（旧前端 → 当前前端）

> **状态：切换进行中（未完成）。** 旧前端已删除，新前端已重命名为 `frontend/`（commit `88f5aa0`，仅重命名）；引用同步、Docker 部署配置、CI 接线等收尾工作见后续提交。本表保留旧→新组件映射作为验收基线。
>
> 方案 §3.4 的验收基线：旧前端（已删除）的每个组件对应到当前前端 `frontend/src` 的位置与状态。

统计（123 个组件）：✅ 已迁移 114，🟡 部分 0，⬜ 待迁移 0，➖ 废弃 / 取代 9

验证（2026-10-30 随 Chromium 剔除，Playwright 页面扫描已删除）：现以 vitest 单元测试 + 桌面冒烟测试（`frontend/e2e-desktop/README.md`）覆盖；Web 端全页扫描不再运行。

另外，新前端还包含旧前端没有的功能：账号登录与用户角色、审计日志、物质法规库检索、PageIndex 深度检索与目录树、规格契约（工艺窗口 / 红线 / 出厂 / 验收）、
约束混料 / 正交 / D-最优 DOE 与合规预筛、实验任务单 / 测量复核 / 方法与仪器 / ELN 同步、Conformal 覆盖率、PLM 产品修订 / 文档生成与校对 / 发布门禁 / 四级签批 / 标签设计器。

| 工作台 | 旧组件 | 状态 | 新位置 / 说明 |
|---|---|---|---|
| 壳 | `App` | ✅ 已迁移 | app/App.tsx + app/Shell.tsx（ProLayout 五步导航） |
| 壳 | `AuthDownloadLink` | ✅ 已迁移 | api/client.download（带令牌下载） |
| 壳 | `main` | ✅ 已迁移 | main.tsx |
| 壳 | `ErrorBoundary` | ✅ 已迁移 | chat/ErrorBoundary：App 全页边界 + Shell 页面区边界（URL 变化自动复位，识别懒加载失败） |
| 壳 | `DegradedBanner` | ✅ 已迁移 | chat/HealthBanners（未配置 LLM key / 检索降级），按钮直达设置页；旧 API token 提示由登录取代 |
| 壳 | `InfraHealthBanner` | ✅ 已迁移 | chat/HealthBanners：30 s 轮询 /health |
| 壳 | `Modal` | ➖ 废弃 / 取代 | 由 antd Modal 取代 |
| 壳 | `NotificationCard` | ✅ 已迁移 | chat/TaskCenter（顶栏任务中心：服务器任务列表 GET /api/tasks + 本地跟踪、进度、取消、待审批） |
| 壳 | `NotificationStack` | ✅ 已迁移 | chat/TaskCenter（同上） |
| ① 知识 | `DocOutline` | ✅ 已迁移 | SourceDetailPage 目录树 |
| ① 知识 | `SourceDetailModal` | ✅ 已迁移 | knowledge/SourceDetailPage（目录树 + 原文） |
| ① 知识 | `ResearchPanel` | ✅ 已迁移 | research/ResearchPage /knowledge/research（外部多源检索、问答、深度研究报告；检索结果经 /api/kb/sources/resolve 对应知识库全文） |
| ① 知识 | `SourcesPanel` | ✅ 已迁移 | knowledge/SourcesPage（归档/恢复/删除/导出/实施例草稿/添加资料）；检索工作区部分由 research 外部检索承担 |
| ① 知识 | `ThinkingTimeline` | ✅ 已迁移 | chat/ThinkingTimeline + research/TaskThinking |
| ① 知识 | `AddSourceModal` | ✅ 已迁移 | library/AddSourceDrawer（文件、标识号、网页、粘贴文本、联网检索入库） |
| ① 知识 | `ArtifactDrawer` | ✅ 已迁移 | research/ArtifactsPage /knowledge/research/artifacts（导出架、版本、预检、报告导出） |
| ① 知识 | `ArtifactSplitLayout` | ➖ 废弃 / 取代 | 纯布局容器，由 ProCard / PageContainer 取代 |
| ① 知识 | `ArtifactVersionsPanel` | ✅ 已迁移 | research/VersionsPanel /knowledge/research/artifacts/:lineageId（另加提交/定稿/哈希校验）；后端无按项目列谱系接口 |
| ① 知识 | `ChatComposerPlus` | ✅ 已迁移 | chat/ChatComposerPlus（模式、技能、连接器、MCP、行动包 → 对应新页面并带上问题） |
| ① 知识 | `ChatSlashMenu` | ✅ 已迁移 | chat/TokenPicker（/技能，另加 @知识源） |
| ① 知识 | `CitationRenderer` | ✅ 已迁移 | chat/CitationAnswer（脚注上标、页码跳转原文） |
| ① 知识 | `EmbodimentDraftModal` | ✅ 已迁移 | research/EmbodimentDraftDrawer（资料库、检索、/knowledge/research/embodiment 共用一份） |
| ① 知识 | `ExtractionTablesPanel` | ✅ 已迁移 | library/SourceTables → SourceDetailPage“结构化抽取”标签 |
| ① 知识 | `IPReportModal` | ✅ 已迁移 | research/IpAnalysisPage /knowledge/research/ip（排行榜卡片可直接带配方进入） |
| ① 知识 | `KgFeedbackStatsStrip` | ✅ 已迁移 | wiki/KgFeedbackStatsStrip |
| ① 知识 | `KgRelationPanel` | ✅ 已迁移 | wiki/KgRelationPanel（另列出矛盾关系） |
| ① 知识 | `ManifestDetailPanel` | ✅ 已迁移 | library/ManifestPage /knowledge/manifest |
| ① 知识 | `MarkdownMessage` | ✅ 已迁移 | shared/Markdown（GFM、KaTeX、SMILES、Mermaid） |
| ① 知识 | `MermaidBlock` | ✅ 已迁移 | shared/Markdown MermaidBlock |
| ① 知识 | `NotebookLMPanel` | ❌ 已下线（2026-10-30 NotebookLM 后端下线） | 原 library/NotebookLMPage /knowledge/notebooklm，已删除 |
| ① 知识 | `OcsrPanel` | ✅ 已迁移 | library/ParsingPage /knowledge/parsing（识别状态 + SMILES 输入绘制；旧组件本身也只有状态面板，Ketcher 从未接入） |
| ① 知识 | `ProjectNotebookLMModal` | ❌ 已下线（2026-10-30 NotebookLM 后端下线） | 原 library/NotebookLMPage（项目笔记本 ID 存入项目），已删除 |
| ① 知识 | `PublicationPreflightPanel` | ✅ 已迁移 | research/PreflightPanel（产出页 ?tab=preflight） |
| ① 知识 | `RagPrewarmBar` | ✅ 已迁移 | chat/ChatToolbar |
| ① 知识 | `RelatedWikiList` | ✅ 已迁移 | wiki/RelatedWikiList（问答与研究问答回答下方） |
| ① 知识 | `ReviewerAuditModal` | ✅ 已迁移 | chat/Reviewer ReviewerAuditPanel，/knowledge/reviews/:runId |
| ① 知识 | `ReviewerCard` | ✅ 已迁移 | chat/Reviewer ReviewerCard |
| ① 知识 | `SaveWikiDraftButton` | ✅ 已迁移 | wiki/SaveWikiDraftButton（问答与研究问答回答下方） |
| ① 知识 | `SessionPlanApprovalCenter` | ✅ 已迁移 | chat/Approvals（全局轮询，任务中心“待审批”） |
| ① 知识 | `SessionPlanModal` | ✅ 已迁移 | chat/Approvals |
| ① 知识 | `SourceTypePicker` | ✅ 已迁移 | library/SourceTypePicker |
| ① 知识 | `SurechemblDraftModal` | ✅ 已迁移 | research/EmbodimentDraftDrawer（同一组件） |
| ① 知识 | `TableBadges` | ✅ 已迁移 | library/SourceTables → SourceDetailPage“表格资产”标签 |
| ① 知识 | `TechReportExportButton` | ✅ 已迁移 | research/TechReportExport |
| ① 知识 | `WikiChatModeSelector` | ✅ 已迁移 | chat/ChatToolbar（Segmented） |
| ① 知识 | `WikiMarkdownReader` | ✅ 已迁移 | wiki/WikiReader（wikilink → 路由链接） |
| ① 知识 | `knowledge-hub/HubCollectionsPane` | ✅ 已迁移 | library/CollectionsPage /knowledge/collections |
| ① 知识 | `knowledge-hub/HubGraphPane` | ✅ 已迁移 | wiki/KgGraphPage /knowledge/graph |
| ① 知识 | `knowledge-hub/HubLiteratureLibraryPane` | ✅ 已迁移 | library/LiteratureLibraryPage /knowledge/library |
| ① 知识 | `knowledge-hub/HubMemoryPane` | ✅ 已迁移 | library/MemoryPage /knowledge/memory |
| ① 知识 | `knowledge-hub/HubQualityPane` | ✅ 已迁移 | library/QualityPage /knowledge/quality |
| ① 知识 | `knowledge-hub/HubReportsPlaceholderPane` | ➖ 废弃 / 取代 | 占位 |
| ① 知识 | `knowledge-hub/HubWikiGraphInsights` | ✅ 已迁移 | wiki/WikiGraphInsights |
| ① 知识 | `knowledge-hub/HubWikiGraphPane` | ✅ 已迁移 | wiki/WikiGraphPage /knowledge/wiki-graph |
| ① 知识 | `knowledge-hub/HubWikiPane` | ✅ 已迁移 | wiki/WikiPage /knowledge/wiki/* |
| ① 知识 | `knowledge-hub/KnowledgeHubModal` | ✅ 已迁移 | 拆为“知识”导航下的独立页面 |
| ① 知识 | `knowledge-hub/LiteratureFreezeStrip` | ✅ 已迁移 | library/ManifestPage 顶部 |
| ① 知识 | `knowledge-hub/RetrievalProbePanel` | ✅ 已迁移 | library/RetrievalProbePage /knowledge/retrieval |
| ① 知识 | `knowledge-hub/WikiPageGraphCanvas` | ✅ 已迁移 | wiki/GraphCanvas（ECharts 力导向图） |
| ② 需求 | `RequirementPanel` | ✅ 已迁移 | spec/SpecStudioPage（场景、目标契约、工艺窗口、红线、出厂 / 验收） |
| ② 需求 | `ConstraintsEditor` | ✅ 已迁移 | requirements/ConstraintsEditor → SpecStudio“工艺约束”标签（含自由约束） |
| ② 需求 | `ProjectHistoryPanel` | ✅ 已迁移 | requirements/ProjectHistory（含回滚） |
| ② 需求 | `ConstraintEffectTrace` | ✅ 已迁移 | requirements/EffectTrace |
| ② 需求 | `PathWizard` | ✅ 已迁移 | requirements/PathsPage /spec/paths |
| ② 需求 | `ProjectAgentContext` | ✅ 已迁移 | requirements/AgentContextCard → SpecStudio“Agent 上下文”标签 |
| ③ 实验室 | `FormulaTableView` | ✅ 已迁移 | lab/FormulationEditorPage（RevoGrid） |
| ③ 实验室 | `DoeHistoryPanel` | ✅ 已迁移 | design/DoeHistoryPage /lab/doe/history（暂停按台账 DOE 循环） |
| ③ 实验室 | `DoeResultsPanel` | ✅ 已迁移 | design/ClassicDoePage /lab/doe/classic（全因子/部分因子/PB/CCD/LHS/BBD/单纯形/Sobol/AI 主动选点） |
| ③ 实验室 | `ExperimentDetail` | ✅ 已迁移 | formulation/WorkbenchRowPage |
| ③ 实验室 | `ExperimentsBrowser` | ✅ 已迁移 | formulation/ExperimentsPage /lab/experiments（训练记录 + 跨台账检索） |
| ③ 实验室 | `LabWorkbench` | ✅ 已迁移 | workbench/CampaignPage /cockpit/workbench/:id（附件数列、撤销 / 重做、右键菜单、原生台账行版本） |
| ③ 实验室 | `LeversEditor` | ✅ 已迁移 | design/LeversPanel /lab/doe/levers（含 material_map） |
| ③ 实验室 | `QCReportModal` | ✅ 已迁移 | design/QcReportPage /lab/qc-report（台账行页可直达） |
| ③ 实验室 | `VersionHistoryModal` | ✅ 已迁移 | formulation/VersionHistory（列表/图、任意两版差异）+ /lab/lineage |
| ③ 实验室 | `AttachmentPreview` | ✅ 已迁移 | formulation/RowPanels RowAttachments |
| ③ 实验室 | `ExperimentDiff` | ✅ 已迁移 | formulation/RowDiffPage |
| ③ 实验室 | `FormulaExplainPanel` | ✅ 已迁移 | formulation/ExplainPanel |
| ③ 实验室 | `FormulaLeaderboard` | ✅ 已迁移 | formulation/LeaderboardPage /lab/leaderboard（单卡 PDF、配方技术报告、KG 关系洞察横幅） |
| ③ 实验室 | `FormulationModeSelector` | ✅ 已迁移 | formulation/ModeSelector（推荐页） |
| ③ 实验室 | `InverseDesignModal` | ✅ 已迁移 | design/InverseDesignPage /lab/inverse-design（“采纳到排行榜”改为存为配方版本） |
| ③ 实验室 | `LineageTree` | ✅ 已迁移 | formulation/RowPanels RowLineage |
| ③ 实验室 | `MaterialSubstitutionModal` | ✅ 已迁移 | design/SubstitutionPage /lab/substitution（配方表“找替代原料”直达） |
| ③ 实验室 | `MaterialsPanel` | ✅ 已迁移 | requirements/materials → /materials/raw（原料库、待审候选、结构检索） |
| ③ 实验室 | `NoteEditor` | ✅ 已迁移 | formulation/RowPanels NoteModal |
| ③ 实验室 | `ProvenanceLineage` | ✅ 已迁移 | formulation/Provenance /lab/provenance/:type/:id |
| ③ 实验室 | `RecommendedFormulaTable` | ✅ 已迁移 | formulation/IngredientTable（CAS 校核、找替代原料） |
| ③ 实验室 | `RowDetailBar` | ✅ 已迁移 | formulation/WorkbenchRowPage（计划 vs 实际、规格判定、上传检测报告） |
| ③ 实验室 | `RowVersionHistoryModal` | ✅ 已迁移 | formulation/RowPanels RowVersions（仅有 ELN refcode 的行） |
| ③ 实验室 | `SimilarFormulationModal` | ✅ 已迁移 | design/SimilarPage /lab/similar |
| ③ 实验室 | `TagPicker` | ✅ 已迁移 | formulation/RowPanels TagModal |
| ③ 实验室 | `WorkbenchModal` | ✅ 已迁移 | workbench/WorkbenchPage /cockpit/workbench（检索/历史在 /lab/experiments） |
| ③ 实验室 | `charts/FormulationLineageGraph` | ✅ 已迁移 | formulation/VersionHistory LineageGraph（ECharts 树） |
| ④ 驾驶舱 | `charts/ParetoFrontPlot` | ✅ 已迁移 | cockpit/ParetoPage（ECharts） |
| ④ 驾驶舱 | `BiasTrendPanel` | ✅ 已迁移 | workbench/panels BiasTrendCard（含 ConformalPage） |
| ④ 驾驶舱 | `ActionsPanel` | ➖ 废弃 / 取代 | 只是打开各弹窗的入口，由“④ 驾驶舱”导航取代 |
| ④ 驾驶舱 | `ActivePlaybookStrip` | ➖ 废弃 / 取代 | 显示旧前端内存中的“当前技能”，新前端无此概念 |
| ④ 驾驶舱 | `AdaptiveDoeInsights` | ✅ 已迁移 | workbench/panels AdaptiveDoeInsights（经典 DOE 页同样展示） |
| ④ 驾驶舱 | `CampaignRoundsModal` | ✅ 已迁移 | workbench/RoundsDrawer |
| ④ 驾驶舱 | `HistoryPanel` | ➖ 废弃 / 取代 | 项目列表与切换由顶栏项目选择器与 /spec/projects 取代 |
| ④ 驾驶舱 | `LoopModal` | ✅ 已迁移 | workbench/LoopPage /cockpit/loop（BayBE 闭环，多轮自动） |
| ④ 驾驶舱 | `MeasuredMetricHitsBanner` | ✅ 已迁移 | workbench/panels（排行榜卡片） |
| ④ 驾驶舱 | `ModelVersionsPanel` | ✅ 已迁移 | workbench/ModelsPage /cockpit/models |
| ④ 驾驶舱 | `SimPlaceholder` | ➖ 废弃 / 取代 | 占位 |
| ④ 驾驶舱 | `TrainingDataBanner` | ✅ 已迁移 | workbench/panels TrainingDataBanner |
| ④ 驾驶舱 | `charts/BiasTrendChart` | ✅ 已迁移 | workbench/charts |
| ④ 驾驶舱 | `charts/ChartContainer` | ➖ 废弃 / 取代 | 由 shared/Chart（ECharts）取代 |
| ④ 驾驶舱 | `charts/ContourPlot` | ✅ 已迁移 | workbench/SurfacePage /cockpit/surface |
| ④ 驾驶舱 | `charts/ParallelCoordinates` | ✅ 已迁移 | workbench/charts ParallelChart（帕累托页“平行坐标”标签；刷选只淡化，不计数） |
| 物料 | `knowledge-hub/HubMaterialsPane` | ✅ 已迁移 | requirements/ProjectSourcesPage /spec/sources（项目资料：勾选、归档、重入库、删除）；未入库的会话资料不再列出 |
| 系统管理 | `ConnectorsSettingsPanel` | ✅ 已迁移 | settings/McpConnectorsSection（嵌入 /admin/connectors） |
| 系统管理 | `ApiAccessPanel` | ➖ 废弃 / 取代 | 服务 API Key 页由账号登录取代（API Key 仍可用于脚本） |
| 系统管理 | `ApiSettingsPanel` | ✅ 已迁移 | settings/ApiKeysTab /admin/settings/keys |
| 系统管理 | `DependencyManager` | ✅ 已迁移 | settings/DependenciesTab + KbMaintenanceTab |
| 系统管理 | `EmbeddingModelPanel` | ✅ 已迁移 | settings/EmbeddingCard |
| 系统管理 | `EnvFlagsPanel` | ✅ 已迁移 | settings/EnvFlagsTab /admin/settings/env（?focus= 深链） |
| 系统管理 | `McpApprovalCenter` | ✅ 已迁移 | settings/McpApprovalsPage /admin/mcp-approvals + chat/Approvals 全局提示 |
| 系统管理 | `McpApprovalDialog` | ✅ 已迁移 | chat/Approvals |
| 系统管理 | `OrgDashboardModal` | ✅ 已迁移 | settings/OrgDashboardPage /admin/org-dashboard |
| 系统管理 | `OrganizationDashboard` | ✅ 已迁移 | settings/OrgDashboardPage |
| 系统管理 | `ParseProfileSelector` | ✅ 已迁移 | library/ParsingPage /knowledge/parsing |
| 系统管理 | `SettingsModal` | ✅ 已迁移 | settings/SettingsPage /admin/settings/:tab（旧标签 id 自动重定向） |
| 系统管理 | `SkillsSettingsPanel` | ✅ 已迁移 | settings/SkillsTab |
| 系统管理 | `VisionModelPanel` | ✅ 已迁移 | settings/ModelTab 视觉模型卡片 |

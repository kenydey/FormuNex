# 前端功能映射表（旧前端 → 新前端）

方案 §3.4 的验收基线：旧前端 `frontend/src` 的每个组件对应到新前端 `frontend-next/src` 的位置与状态。
新前端在本表 100% 为“已迁移 / 废弃”且 Playwright 主路径全绿之前不替换旧前端（一次性切换）。

统计（123 个组件）：✅ 已迁移 8，🟡 部分 17，⬜ 待迁移 93，➖ 废弃 / 取代 5

另外，新前端还包含旧前端没有的功能：账号登录与用户角色、审计日志、物质法规库检索、PageIndex 深度检索与目录树、规格契约（工艺窗口 / 红线 / 出厂 / 验收）、
约束混料 / 正交 / D-最优 DOE 与合规预筛、实验任务单 / 测量复核 / 方法与仪器 / ELN 同步、Conformal 覆盖率、PLM 产品修订 / 文档生成与校对 / 发布门禁 / 四级签批 / 标签设计器。

| 工作台 | 旧组件 | 状态 | 新位置 / 说明 |
|---|---|---|---|
| 壳 | `App` | ✅ 已迁移 | app/App.tsx + app/Shell.tsx（ProLayout 五步导航） |
| 壳 | `AuthDownloadLink` | ✅ 已迁移 | api/client.download（带令牌下载） |
| 壳 | `main` | ✅ 已迁移 | main.tsx |
| 壳 | `ErrorBoundary` | 🟡 部分 | 路由懒加载 + Suspense；全局错误边界待补 |
| 壳 | `DegradedBanner` | ⬜ 待迁移 | 基础设施降级横幅 |
| 壳 | `InfraHealthBanner` | ⬜ 待迁移 | /health 详情横幅 |
| 壳 | `Modal` | ➖ 废弃 / 取代 | 由 antd Modal 取代 |
| 壳 | `NotificationCard` | ⬜ 待迁移 | 任务中心通知 |
| 壳 | `NotificationStack` | ⬜ 待迁移 | 任务中心通知 |
| ① 知识 | `DocOutline` | ✅ 已迁移 | SourceDetailPage 目录树 |
| ① 知识 | `SourceDetailModal` | ✅ 已迁移 | knowledge/SourceDetailPage（目录树 + 原文） |
| ① 知识 | `ResearchPanel` | 🟡 部分 | SearchPage / DeepSearchPage（多源外部检索、深度研究报告待迁移） |
| ① 知识 | `SourcesPanel` | 🟡 部分 | knowledge/SourcesPage（列表 + 目录树；上传 / 删除 / 归档待迁移） |
| ① 知识 | `ThinkingTimeline` | 🟡 部分 | DeepSearchPage 的推理轨迹（Chat 版待迁移） |
| ① 知识 | `AddSourceModal` | ⬜ 待迁移 | 文献 / 专利入库 |
| ① 知识 | `ArtifactDrawer` | ⬜ 待迁移 | 研究产出（Artifact）版本 |
| ① 知识 | `ArtifactSplitLayout` | ⬜ 待迁移 |  |
| ① 知识 | `ArtifactVersionsPanel` | ⬜ 待迁移 |  |
| ① 知识 | `ChatComposerPlus` | ⬜ 待迁移 | 知识问答 Chat（含深度模式开关） |
| ① 知识 | `ChatSlashMenu` | ⬜ 待迁移 |  |
| ① 知识 | `CitationRenderer` | ⬜ 待迁移 |  |
| ① 知识 | `EmbodimentDraftModal` | ⬜ 待迁移 | 实施例草稿（后端已接 PageIndex） |
| ① 知识 | `ExtractionTablesPanel` | ⬜ 待迁移 | 表格提取 |
| ① 知识 | `IPReportModal` | ⬜ 待迁移 | 专利 / IP 分析报告 |
| ① 知识 | `KgFeedbackStatsStrip` | ⬜ 待迁移 |  |
| ① 知识 | `KgRelationPanel` | ⬜ 待迁移 | 知识图谱 |
| ① 知识 | `ManifestDetailPanel` | ⬜ 待迁移 |  |
| ① 知识 | `MarkdownMessage` | ⬜ 待迁移 |  |
| ① 知识 | `MermaidBlock` | ⬜ 待迁移 |  |
| ① 知识 | `NotebookLMPanel` | ⬜ 待迁移 |  |
| ① 知识 | `OcsrPanel` | ⬜ 待迁移 | 结构式识别 / 检索（Ketcher 待接入） |
| ① 知识 | `ProjectNotebookLMModal` | ⬜ 待迁移 |  |
| ① 知识 | `PublicationPreflightPanel` | ⬜ 待迁移 |  |
| ① 知识 | `RagPrewarmBar` | ⬜ 待迁移 |  |
| ① 知识 | `RelatedWikiList` | ⬜ 待迁移 | Wiki |
| ① 知识 | `ReviewerAuditModal` | ⬜ 待迁移 | 证据审稿 |
| ① 知识 | `ReviewerCard` | ⬜ 待迁移 |  |
| ① 知识 | `SaveWikiDraftButton` | ⬜ 待迁移 |  |
| ① 知识 | `SessionPlanApprovalCenter` | ⬜ 待迁移 | 会话计划审批 |
| ① 知识 | `SessionPlanModal` | ⬜ 待迁移 |  |
| ① 知识 | `SourceTypePicker` | ⬜ 待迁移 |  |
| ① 知识 | `SurechemblDraftModal` | ⬜ 待迁移 |  |
| ① 知识 | `TableBadges` | ⬜ 待迁移 |  |
| ① 知识 | `TechReportExportButton` | ⬜ 待迁移 |  |
| ① 知识 | `WikiChatModeSelector` | ⬜ 待迁移 |  |
| ① 知识 | `WikiMarkdownReader` | ⬜ 待迁移 |  |
| ① 知识 | `knowledge-hub/HubCollectionsPane` | ⬜ 待迁移 |  |
| ① 知识 | `knowledge-hub/HubGraphPane` | ⬜ 待迁移 |  |
| ① 知识 | `knowledge-hub/HubLiteratureLibraryPane` | ⬜ 待迁移 |  |
| ① 知识 | `knowledge-hub/HubMemoryPane` | ⬜ 待迁移 |  |
| ① 知识 | `knowledge-hub/HubQualityPane` | ⬜ 待迁移 |  |
| ① 知识 | `knowledge-hub/HubReportsPlaceholderPane` | ➖ 废弃 / 取代 | 占位 |
| ① 知识 | `knowledge-hub/HubWikiGraphInsights` | ⬜ 待迁移 |  |
| ① 知识 | `knowledge-hub/HubWikiGraphPane` | ⬜ 待迁移 |  |
| ① 知识 | `knowledge-hub/HubWikiPane` | ⬜ 待迁移 |  |
| ① 知识 | `knowledge-hub/KnowledgeHubModal` | ⬜ 待迁移 | 知识中心（集合、文献库、Wiki、图谱、记忆、质量） |
| ① 知识 | `knowledge-hub/LiteratureFreezeStrip` | ⬜ 待迁移 |  |
| ① 知识 | `knowledge-hub/RetrievalProbePanel` | ⬜ 待迁移 | 检索探针 / 质量 |
| ① 知识 | `knowledge-hub/WikiPageGraphCanvas` | ⬜ 待迁移 |  |
| ② 需求 | `RequirementPanel` | ✅ 已迁移 | spec/SpecStudioPage（场景、目标契约、工艺窗口、红线、出厂 / 验收） |
| ② 需求 | `ConstraintsEditor` | 🟡 部分 | SpecStudio 工艺窗口 / 红线（旧 constraint_values 自由约束未迁移） |
| ② 需求 | `ProjectHistoryPanel` | 🟡 部分 | SpecStudio 契约版本列表（回滚待迁移） |
| ② 需求 | `ConstraintEffectTrace` | ⬜ 待迁移 |  |
| ② 需求 | `PathWizard` | ⬜ 待迁移 |  |
| ② 需求 | `ProjectAgentContext` | ⬜ 待迁移 |  |
| ③ 实验室 | `FormulaTableView` | ✅ 已迁移 | lab/FormulationEditorPage（RevoGrid） |
| ③ 实验室 | `DoeHistoryPanel` | 🟡 部分 | Conformal 页的 DOE 计划选择（历史列表页待迁移） |
| ③ 实验室 | `DoeResultsPanel` | 🟡 部分 | lab/DoeDesignerPage（新设计：正交 / 混料 / D-最优；旧 /api/doe 全因子 / CCD / LHS 入口待迁移） |
| ③ 实验室 | `ExperimentDetail` | 🟡 部分 | TaskDetailPage |
| ③ 实验室 | `ExperimentsBrowser` | 🟡 部分 | 实验任务列表（旧训练记录浏览器待迁移） |
| ③ 实验室 | `LabWorkbench` | 🟡 部分 | lab/TasksPage + TaskDetailPage（任务单录入取代工作台行录入；Campaign 工作台待迁移） |
| ③ 实验室 | `LeversEditor` | 🟡 部分 | DOE 设计页的因子表（旧 lever material_map 未迁移） |
| ③ 实验室 | `QCReportModal` | 🟡 部分 | 仪器文件导入（QC 报告 PDF 抽取待迁移） |
| ③ 实验室 | `VersionHistoryModal` | 🟡 部分 | 配方编辑器“打开版本”（版本 diff 视图待迁移） |
| ③ 实验室 | `AttachmentPreview` | ⬜ 待迁移 | 实验附件 |
| ③ 实验室 | `ExperimentDiff` | ⬜ 待迁移 |  |
| ③ 实验室 | `FormulaExplainPanel` | ⬜ 待迁移 |  |
| ③ 实验室 | `FormulaLeaderboard` | ⬜ 待迁移 |  |
| ③ 实验室 | `FormulationModeSelector` | ⬜ 待迁移 |  |
| ③ 实验室 | `InverseDesignModal` | ⬜ 待迁移 |  |
| ③ 实验室 | `LineageTree` | ⬜ 待迁移 | 配方版本树 |
| ③ 实验室 | `MaterialSubstitutionModal` | ⬜ 待迁移 | 智能原料替换 |
| ③ 实验室 | `MaterialsPanel` | ⬜ 待迁移 | 需求中的原料清单 |
| ③ 实验室 | `NoteEditor` | ⬜ 待迁移 |  |
| ③ 实验室 | `ProvenanceLineage` | ⬜ 待迁移 |  |
| ③ 实验室 | `RecommendedFormulaTable` | ⬜ 待迁移 | AI 推荐配方 |
| ③ 实验室 | `RowDetailBar` | ⬜ 待迁移 |  |
| ③ 实验室 | `RowVersionHistoryModal` | ⬜ 待迁移 |  |
| ③ 实验室 | `SimilarFormulationModal` | ⬜ 待迁移 |  |
| ③ 实验室 | `TagPicker` | ⬜ 待迁移 |  |
| ③ 实验室 | `WorkbenchModal` | ⬜ 待迁移 |  |
| ③ 实验室 | `charts/FormulationLineageGraph` | ⬜ 待迁移 |  |
| ④ 驾驶舱 | `charts/ParetoFrontPlot` | ✅ 已迁移 | cockpit/ParetoPage（ECharts） |
| ④ 驾驶舱 | `BiasTrendPanel` | 🟡 部分 | cockpit/ConformalPage（偏差 + 覆盖率；旧偏差趋势图待迁移） |
| ④ 驾驶舱 | `ActionsPanel` | ⬜ 待迁移 |  |
| ④ 驾驶舱 | `ActivePlaybookStrip` | ⬜ 待迁移 |  |
| ④ 驾驶舱 | `AdaptiveDoeInsights` | ⬜ 待迁移 | 主动学习 DOE |
| ④ 驾驶舱 | `CampaignRoundsModal` | ⬜ 待迁移 |  |
| ④ 驾驶舱 | `HistoryPanel` | ⬜ 待迁移 |  |
| ④ 驾驶舱 | `LoopModal` | ⬜ 待迁移 | BayBE 闭环迭代 |
| ④ 驾驶舱 | `MeasuredMetricHitsBanner` | ⬜ 待迁移 |  |
| ④ 驾驶舱 | `ModelVersionsPanel` | ⬜ 待迁移 |  |
| ④ 驾驶舱 | `SimPlaceholder` | ➖ 废弃 / 取代 | 占位 |
| ④ 驾驶舱 | `TrainingDataBanner` | ⬜ 待迁移 |  |
| ④ 驾驶舱 | `charts/BiasTrendChart` | ⬜ 待迁移 |  |
| ④ 驾驶舱 | `charts/ChartContainer` | ➖ 废弃 / 取代 | 由 shared/Chart（ECharts）取代 |
| ④ 驾驶舱 | `charts/ContourPlot` | ⬜ 待迁移 | 响应面 |
| ④ 驾驶舱 | `charts/ParallelCoordinates` | ⬜ 待迁移 |  |
| 物料 | `knowledge-hub/HubMaterialsPane` | 🟡 部分 | materials/MaterialsPage（列表；归档 / 导入导出待迁移） |
| 系统管理 | `ConnectorsSettingsPanel` | 🟡 部分 | admin/ConnectorsPage（发布 / ELN 连接器只读展示；MCP 连接器待迁移） |
| 系统管理 | `ApiAccessPanel` | ➖ 废弃 / 取代 | 服务 API Key 页由账号登录取代（API Key 仍可用于脚本） |
| 系统管理 | `ApiSettingsPanel` | ⬜ 待迁移 | LLM / 检索设置页 |
| 系统管理 | `DependencyManager` | ⬜ 待迁移 |  |
| 系统管理 | `EmbeddingModelPanel` | ⬜ 待迁移 |  |
| 系统管理 | `EnvFlagsPanel` | ⬜ 待迁移 |  |
| 系统管理 | `McpApprovalCenter` | ⬜ 待迁移 | MCP 工具审批 |
| 系统管理 | `McpApprovalDialog` | ⬜ 待迁移 |  |
| 系统管理 | `OrgDashboardModal` | ⬜ 待迁移 | 组织看板 |
| 系统管理 | `OrganizationDashboard` | ⬜ 待迁移 |  |
| 系统管理 | `ParseProfileSelector` | ⬜ 待迁移 |  |
| 系统管理 | `SettingsModal` | ⬜ 待迁移 | 设置中心（LLM 路由、解析、嵌入模型等） |
| 系统管理 | `SkillsSettingsPanel` | ⬜ 待迁移 |  |
| 系统管理 | `VisionModelPanel` | ⬜ 待迁移 |  |
